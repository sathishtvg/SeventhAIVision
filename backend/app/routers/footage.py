"""Footage Review — /api/v1/footage

Videos people upload so that they can be searched and reviewed (design:
docs/plans/2026-10-20-footage-review-design.md). This router is phase 1: a
video is uploaded in parts, listed, kept for 30 days, held past them, or
deleted. How a video arrives and where its file is kept is in
app.services.footage; the daily removal of expired videos in
app.services.footage_retention.

Permissions:
    footage:upload       start an upload, send its parts, finish or cancel one's own
    footage:view         list the videos and read one
    footage:delete       delete a video before its 30 days are up, or cancel anybody's upload
    evidence:hold:manage keep a video past its 30 days (with footage:view); a hold is
                         lifted where every hold is, POST /api/v1/evidence-holds/{id}/release

A video may name one of the organisation's sites. Somebody limited to some
sites sees the videos of those sites only, and must name one of them when
uploading; a video without a site is seen only by somebody with no site limit.
A video somebody may not see is answered 404, as one that does not exist.

NOTHING HERE RAISES AN ALERT, AN INCIDENT OR A NOTIFICATION. What is found in a
video stays in Footage Review until a person does something with it.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import clamp
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import _client_ip, get_db_with_tenant
from app.routers.operations_board import held_by
from app.services import footage
from app.services import intel_audit

router = APIRouter(prefix="/api/v1/footage", tags=["footage"])

_UPLOAD = [Depends(require_permission("footage:upload"))]
_VIEW = [Depends(require_permission("footage:view"))]
_DELETE = [Depends(require_permission("footage:delete"))]
_HOLD = [Depends(require_permission("footage:view")), Depends(require_permission("evidence:hold:manage"))]
HELD = ("footage:upload", "footage:view", "footage:review", "footage:delete", "evidence:hold:manage")

#: How far ahead of the server's clock a "filmed at" may be: a camera's clock is rarely right.
CLOCK_SLACK = timedelta(minutes=10)


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _may(held: frozenset[str]) -> dict:
    return {"upload": "footage:upload" in held, "review": "footage:review" in held,
            "delete": "footage:delete" in held, "hold": "evidence:hold:manage" in held}


def _rules() -> dict:
    return {"part_size": footage.PART_SIZE, "max_bytes": footage.MAX_BYTES, "max_seconds": footage.MAX_SECONDS,
            "extensions": sorted(footage.EXTENSIONS), "retention_days": footage.RETENTION_DAYS}


_VIDEO = """
    v.id, v.source, v.title, v.original_filename, v.content_type, v.size_bytes, v.received_bytes, v.part_size,
    v.site_id, s.name AS site_name, v.place, v.filmed_at, v.duration_seconds, v.width, v.height, v.frame_rate,
    v.status, v.progress, v.error, v.sha256, v.storage_path, v.uploaded_by_user_id, u.full_name AS uploaded_by_name,
    v.created_at, v.updated_at, v.upload_completed_at, v.processed_at, v.expires_at,
    h.id AS hold_id, h.reason AS hold_reason, h.placed_at AS hold_placed_at, hu.full_name AS hold_placed_by_name
"""
_FROM = """
      FROM footage_videos v
      LEFT JOIN sites s ON s.id = v.site_id
      LEFT JOIN users u ON u.id = v.uploaded_by_user_id
      LEFT JOIN LATERAL (SELECT id, reason, placed_at, placed_by_user_id FROM evidence_holds
                          WHERE kind = 'FOOTAGE' AND ref_id = v.id AND released_at IS NULL
                          ORDER BY placed_at LIMIT 1) h ON TRUE
      LEFT JOIN users hu ON hu.id = h.placed_by_user_id
"""


def _out(row, now: datetime) -> dict:
    v = {k: val for k, val in dict(row).items() if k not in ("total", "storage_path")}
    v["status_label"] = footage.STATUS_LABEL.get(v["status"], v["status"])
    v["held"] = v["hold_id"] is not None
    left = v["expires_at"] - now
    v["deleted_in_days"] = None if v["held"] else max(0, left.days + (1 if left.seconds or left.microseconds else 0))
    return v


async def _one(db: AsyncSession, video_id: uuid.UUID, allowed: list[str] | None, *, lock: bool = False):
    row = (await db.execute(text(f"SELECT {_VIDEO} {_FROM} WHERE v.id = CAST(:id AS uuid)"
                                 + (" FOR UPDATE OF v" if lock else "")), {"id": str(video_id)})).mappings().first()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Video not found")
    return row


# ── uploading ─────────────────────────────────────────────────────────────────

class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(..., min_length=1, max_length=footage.FILENAME_LIMIT)
    size_bytes: int = Field(..., gt=0)
    title: str | None = Field(None, max_length=footage.TITLE_LIMIT)
    place: str | None = Field(None, max_length=footage.PLACE_LIMIT)
    site_id: uuid.UUID | None = None
    filmed_at: datetime | None = None


@router.post("", status_code=201, dependencies=_UPLOAD)
async def start_upload(
    body: StartBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Begin an upload: say what the file is and how big. Answers with the id and
    the part size; the parts then go to PUT /{id}/content."""
    ext = footage.extension_of(body.filename)
    if ext is None:
        raise HTTPException(422, "Only MP4, MOV, MKV and AVI videos can be uploaded.")
    if body.size_bytes > footage.MAX_BYTES:
        raise HTTPException(413, f"A video may be at most {footage.MAX_BYTES // 1024 ** 3} GB.")
    if body.site_id is None and allowed is not None:
        raise HTTPException(422, "Choose one of your sites for this video.")
    if body.site_id is not None:
        if not is_site_allowed(allowed, body.site_id):
            raise HTTPException(404, "Site not found")
        if (await db.execute(text("SELECT 1 FROM sites WHERE id = CAST(:s AS uuid)"),
                             {"s": str(body.site_id)})).first() is None:
            raise HTTPException(404, "Site not found")
    if body.filmed_at is not None:
        if body.filmed_at.tzinfo is None:
            raise HTTPException(422, "Say the time zone of when it was filmed.")
        if body.filmed_at > datetime.now(timezone.utc) + CLOCK_SLACK:
            raise HTTPException(422, "When it was filmed cannot be in the future.")
    title = (body.title or "").strip() or PurePosixPath(body.filename.replace("\\", "/")).stem.strip() or "Video"
    place = (body.place or "").strip() or None
    video_id = uuid.uuid4()
    path = footage.original_path(token.tenant_id, video_id, ext)
    upload_id = await footage.begin(path, footage.EXTENSIONS[ext])
    await db.execute(text("""
        INSERT INTO footage_videos (id, tenant_id, title, original_filename, content_type, size_bytes, part_size,
                                    storage_path, s3_upload_id, site_id, place, filmed_at, uploaded_by_user_id,
                                    expires_at)
        VALUES (:id, current_setting('app.current_tenant')::uuid, :title, :filename, :ctype, :size, :part, :path,
                :upload, :site, :place, :filmed, CAST(:who AS uuid), now() + make_interval(days => :days))
    """), {"id": video_id, "title": title[:footage.TITLE_LIMIT], "filename": body.filename.strip(),
           "ctype": footage.EXTENSIONS[ext], "size": body.size_bytes, "part": footage.PART_SIZE, "path": path,
           "upload": upload_id, "site": body.site_id, "place": place, "filmed": body.filmed_at,
           "who": token.user_id, "days": footage.RETENTION_DAYS})
    await intel_audit.record(db, request, token, "footage.upload.start", "footage_video", video_id,
                             site_id=body.site_id, detail={"title": title, "filename": body.filename.strip(),
                                                           "size_bytes": body.size_bytes})
    answer = _out(await _one(db, video_id, allowed), datetime.now(timezone.utc))
    answer["rules"] = _rules()
    await db.commit()
    return answer


@router.get("/{video_id:uuid}/upload", dependencies=_UPLOAD)
async def upload_state(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """How far an upload has got: where to carry on from."""
    v = await _one(db, video_id, allowed)
    return {"id": v["id"], "status": v["status"], "size_bytes": v["size_bytes"],
            "received_bytes": v["received_bytes"], "part_size": v["part_size"]}


@router.put("/{video_id:uuid}/content", dependencies=_UPLOAD)
async def send_part(
    video_id: uuid.UUID,
    request: Request,
    offset: int = Query(..., ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One part of the file, as the request's body, at `offset`. A part at any
    other offset than the one reached is refused with 409 and that offset."""
    v = await _one(db, video_id, allowed, lock=True)
    if str(v["uploaded_by_user_id"]) != str(token.user_id):
        raise HTTPException(403, "Only the person who began this upload can add to it.")
    if v["status"] != "UPLOADING":
        raise HTTPException(409, "This upload is finished.")
    if offset != v["received_bytes"]:
        raise HTTPException(409, {"message": "Carry on from the offset the server has reached.",
                                  "received_bytes": v["received_bytes"]})
    want = footage.expected_part(v["size_bytes"], offset, v["part_size"])
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) != want:
        raise HTTPException(422, f"The part at offset {offset} is {want} bytes long.")
    chunks, got = [], 0
    async for chunk in request.stream():
        got += len(chunk)
        if got > want:
            raise HTTPException(422, f"The part at offset {offset} is {want} bytes long.")
        chunks.append(chunk)
    if got != want:
        raise HTTPException(422, f"The part at offset {offset} is {want} bytes long; {got} arrived.")
    part = await footage.write_part(v["storage_path"], offset, b"".join(chunks), await _upload_id(db, video_id),
                                    v["part_size"])
    parts_sql, params = "", {"id": str(video_id), "got": offset + got}
    if part is not None:
        current = (await db.execute(text("SELECT s3_parts FROM footage_videos WHERE id = CAST(:id AS uuid)"),
                                    {"id": str(video_id)})).scalar_one()
        parts_sql, params["parts"] = ", s3_parts = CAST(:parts AS jsonb)", json.dumps(footage.with_part(current, part))
    await db.execute(text(f"UPDATE footage_videos SET received_bytes = :got, updated_at = now(){parts_sql} "
                          "WHERE id = CAST(:id AS uuid)"), params)
    answer = {"id": v["id"], "received_bytes": offset + got, "size_bytes": v["size_bytes"],
              "complete": offset + got == v["size_bytes"]}
    await db.commit()
    return answer


async def _upload_id(db: AsyncSession, video_id: uuid.UUID) -> str | None:
    return (await db.execute(text("SELECT s3_upload_id FROM footage_videos WHERE id = CAST(:id AS uuid)"),
                             {"id": str(video_id)})).scalar_one()


@router.post("/{video_id:uuid}/complete", dependencies=_UPLOAD)
async def finish_upload(
    video_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The whole file has arrived: join it and queue it to be analysed."""
    v = await _one(db, video_id, allowed, lock=True)
    if str(v["uploaded_by_user_id"]) != str(token.user_id):
        raise HTTPException(403, "Only the person who began this upload can finish it.")
    if v["status"] != "UPLOADING":
        raise HTTPException(409, "This upload is already finished.")
    if v["received_bytes"] != v["size_bytes"]:
        raise HTTPException(409, {"message": "Not all of the file has arrived.",
                                  "received_bytes": v["received_bytes"]})
    parts = (await db.execute(text("SELECT s3_parts FROM footage_videos WHERE id = CAST(:id AS uuid)"),
                              {"id": str(video_id)})).scalar_one()
    await footage.finish(v["storage_path"], await _upload_id(db, video_id), parts)
    await db.execute(text("""
        UPDATE footage_videos SET status = 'QUEUED', upload_completed_at = now(), updated_at = now(),
                                  s3_upload_id = NULL
         WHERE id = CAST(:id AS uuid)
    """), {"id": str(video_id)})
    await intel_audit.record(db, request, token, "footage.upload.complete", "footage_video", video_id,
                             site_id=v["site_id"], detail={"title": v["title"], "size_bytes": v["size_bytes"]})
    answer = _out(await _one(db, video_id, allowed), datetime.now(timezone.utc))
    await db.commit()
    answer["announced"] = await footage.announce(getattr(request.app.state, "redis", None), token.tenant_id, video_id)
    return answer


@router.delete("/{video_id:uuid}/upload", dependencies=_UPLOAD)
async def cancel_upload(
    video_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Stop an unfinished upload and throw away what arrived. One's own, or
    anybody's for somebody who may delete videos."""
    v = await _one(db, video_id, allowed, lock=True)
    if v["status"] != "UPLOADING":
        raise HTTPException(409, "This upload is finished. A finished video is deleted, not cancelled.")
    if str(v["uploaded_by_user_id"]) != str(token.user_id) and \
            "footage:delete" not in await held_by(db, token.role_id, HELD):
        raise HTTPException(403, "Only the person who began this upload can cancel it.")
    await _remove(db, request, token, v, "footage.upload.cancel", None)
    await db.commit()
    return {"cancelled": True}


# ── reading ───────────────────────────────────────────────────────────────────

@router.get("", dependencies=_VIEW)
async def list_videos(
    q: str | None = Query(None, max_length=100),
    status: str | None = Query(None),
    site_id: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The videos the person may see, newest first, a page at a time."""
    if status is not None and status not in footage.STATUSES:
        raise HTTPException(422, f"status is one of {', '.join(footage.STATUSES)}")
    limit, offset = clamp(limit, offset)
    params: dict = {"limit": limit, "offset": offset}
    where: list[str] = []
    scope = site_scope_clause(allowed, "v.site_id", params)
    if scope:
        where.append(scope)
    if q and q.strip():
        params["q"] = _like(q.strip())
        where.append("(v.title ILIKE :q OR v.place ILIKE :q OR v.original_filename ILIKE :q)")
    if status:
        where.append("v.status = :status")
        params["status"] = status
    if site_id is not None:
        where.append("v.site_id = CAST(:site AS uuid)")
        params["site"] = str(site_id)
    rows = (await db.execute(text(f"""
        SELECT {_VIDEO}, count(*) OVER () AS total {_FROM}
         {('WHERE ' + ' AND '.join(where)) if where else ''}
         ORDER BY v.created_at DESC, v.id
         LIMIT :limit OFFSET :offset
    """), params)).mappings().all()
    now = datetime.now(timezone.utc)
    held = await held_by(db, token.role_id, HELD)
    return {"items": [_out(r, now) for r in rows], "total": rows[0]["total"] if rows else 0,
            "limit": limit, "offset": offset, "may": _may(held), "rules": _rules()}


@router.get("/{video_id:uuid}", dependencies=_VIEW)
async def get_video(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """One video, with its hold if it has one."""
    answer = _out(await _one(db, video_id, allowed), datetime.now(timezone.utc))
    answer["may"] = _may(await held_by(db, token.role_id, HELD))
    return answer


# ── keeping and deleting ──────────────────────────────────────────────────────

class WhyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=5, max_length=2000)


@router.post("/{video_id:uuid}/hold", status_code=201, dependencies=_HOLD)
async def hold_video(
    video_id: uuid.UUID,
    body: WhyBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Keep a video past its 30 days, saying why. It is kept until the hold is lifted."""
    reason = body.reason.strip()
    if len(reason) < 5:
        raise HTTPException(422, "Say why it is being kept.")
    v = await _one(db, video_id, allowed, lock=True)
    if v["hold_id"] is not None:
        raise HTTPException(409, "This video is already held.")
    if v["status"] == "UPLOADING":
        raise HTTPException(409, "Finish the upload before holding the video.")
    row = (await db.execute(text("""
        INSERT INTO evidence_holds (tenant_id, kind, ref_id, site_id, reason, placed_by_user_id)
        VALUES (current_setting('app.current_tenant')::uuid, 'FOOTAGE', :ref, :site, :reason, CAST(:who AS uuid))
        RETURNING id, placed_at
    """), {"ref": video_id, "site": v["site_id"], "reason": reason, "who": token.user_id})).mappings().one()
    await db.execute(text("""
        INSERT INTO evidence_custody_events
               (tenant_id, package_id, kind, ref_id, step, actor_user_id, actor_role, reason, detail, ip_address,
                request_id)
        VALUES (current_setting('app.current_tenant')::uuid, NULL, 'FOOTAGE', :ref, 'LOCKED', CAST(:who AS uuid),
                :role, :reason, CAST(:detail AS jsonb), :ip, :request)
    """), {"ref": video_id, "who": token.user_id, "role": token.role_id, "reason": reason,
           "detail": json.dumps({"title": v["title"]}), "ip": _client_ip(request),
           "request": getattr(request.state, "request_id", None)})
    await intel_audit.record(db, request, token, "evidence.hold.place", "evidence_hold", row["id"],
                             site_id=v["site_id"], detail={"kind": "FOOTAGE", "item_id": str(video_id),
                                                           "reason": reason})
    answer = {"id": row["id"], "placed_at": row["placed_at"]}
    await db.commit()
    return answer


@router.delete("/{video_id:uuid}", dependencies=_DELETE)
async def delete_video(
    video_id: uuid.UUID,
    body: WhyBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Delete a video before its 30 days are up, with everything worked out from
    it, saying why. A video under a hold is not deleted."""
    reason = body.reason.strip()
    if len(reason) < 5:
        raise HTTPException(422, "Say why it is being deleted.")
    v = await _one(db, video_id, allowed, lock=True)
    if v["hold_id"] is not None:
        raise HTTPException(409, "This video is under an evidence hold. Lift the hold before deleting it.")
    if v["status"] == "PROCESSING":
        raise HTTPException(409, "This video is being analysed. Delete it once that has finished.")
    await _remove(db, request, token, v, "footage.delete", reason)
    await db.commit()
    return {"deleted": True}


async def _remove(db: AsyncSession, request: Request, token: TokenPayload, v, action: str,
                  reason: str | None) -> None:
    """The file first, then the row: a failure between leaves a row whose file is
    missing, never a file nothing refers to."""
    await asyncio.to_thread(footage.remove_everything, footage.storage_dir(token.tenant_id, v["id"]),
                            v["storage_path"], await _upload_id(db, v["id"]))
    await db.execute(text("DELETE FROM footage_videos WHERE id = CAST(:id AS uuid)"), {"id": str(v["id"])})
    await intel_audit.record(db, request, token, action, "footage_video", v["id"], site_id=v["site_id"],
                             detail={"title": v["title"], "status": v["status"], "reason": reason})
