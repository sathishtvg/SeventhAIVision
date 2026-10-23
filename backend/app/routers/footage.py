"""Footage Review — /api/v1/footage

Videos people upload so that they can be searched and reviewed (design:
docs/plans/2026-10-20-footage-review-design.md). This router is phase 1: a
video is uploaded in parts, listed, kept for 30 days, held past them, or
deleted. How a video arrives and where its file is kept is in
app.services.footage; the daily removal of expired videos in
app.services.footage_retention.

Permissions:
    footage:upload       start an upload, send its parts, finish or cancel one's own
    footage:view         list the videos, read one, search them, see their frames and play them
    footage:review       have a video analysed again; mark a possible altercation confirmed or not
    footage:delete       delete a video before its 30 days are up, or cancel anybody's upload
    footage:upload and recording:read
                         copy a saved recording in to be analysed (POST /from-recording)
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
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from limits import parse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.limiter import limiter
from app.core.pagination import clamp
from app.core.security import InvalidTokenError, decode_access_token
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.dependencies.permissions import require_permission
from app.dependencies.rate_limit import STORE_TIMEOUT_S, caller_of
from app.dependencies.sites import get_allowed_site_ids, is_site_allowed, site_scope_clause
from app.dependencies.tenant import _client_ip, get_db_with_tenant
from app.routers.operations_board import held_by
from app.services import footage
from app.services import footage_search as finder
from app.services import intel_audit
from app.services.audit import write_audit_log

router = APIRouter(prefix="/api/v1/footage", tags=["footage"])
logger = logging.getLogger(__name__)

_UPLOAD = [Depends(require_permission("footage:upload"))]
_VIEW = [Depends(require_permission("footage:view"))]
_DELETE = [Depends(require_permission("footage:delete"))]
_REVIEW = [Depends(require_permission("footage:review"))]
_FROM_RECORDING = [Depends(require_permission("footage:upload")), Depends(require_permission("recording:read"))]
_HOLD = [Depends(require_permission("footage:view")), Depends(require_permission("evidence:hold:manage"))]
HELD = ("footage:upload", "footage:view", "footage:review", "footage:delete", "evidence:hold:manage")

#: How far ahead of the server's clock a "filmed at" may be: a camera's clock is rarely right.
CLOCK_SLACK = timedelta(minutes=10)
#: A search is heavier than a read, and the default limit counts reads only. Per person.
SEARCH_LIMIT = parse("60/minute")


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
    v.codec, v.frames_indexed, v.index_model, v.playable, v.recording_id,
    (SELECT count(*) FROM footage_findings ff
      WHERE ff.video_id = v.id AND ff.kind = 'ALTERCATION' AND ff.verdict IS NULL) AS altercations_to_review,
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
    v = {k: val for k, val in dict(row).items() if k not in ("total", "storage_path", "proxy_path")}
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
    if v["source"] != "UPLOAD":
        raise HTTPException(409, "This video is being copied from a recording.")
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
    if v["source"] != "UPLOAD":
        raise HTTPException(409, "This video is being copied from a recording.")
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
    if v["source"] != "UPLOAD":
        raise HTTPException(409, "This recording is still being copied. Delete it once the copy has finished.")
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


# ── analysing again ───────────────────────────────────────────────────────────

@router.post("/{video_id:uuid}/reanalyse", dependencies=_REVIEW)
async def reanalyse_video(
    video_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Have a video analysed again from the start: after a failure, or after the
    model has changed. What the last analysis found is replaced."""
    v = await _one(db, video_id, allowed, lock=True)
    if v["status"] not in ("READY", "FAILED"):
        raise HTTPException(409, "Only a video that is ready, or could not be analysed, can be analysed again.")
    await db.execute(text("""
        UPDATE footage_videos SET status = 'QUEUED', attempts = 0, progress = 0, error = NULL, updated_at = now()
         WHERE id = CAST(:id AS uuid)
    """), {"id": str(video_id)})
    await intel_audit.record(db, request, token, "footage.reanalyse", "footage_video", video_id,
                             site_id=v["site_id"], detail={"title": v["title"], "was": v["status"]})
    answer = _out(await _one(db, video_id, allowed), datetime.now(timezone.utc))
    await db.commit()
    answer["announced"] = await footage.announce(getattr(request.app.state, "redis", None), token.tenant_id, video_id)
    return answer


# ── searching by description ──────────────────────────────────────────────────

async def _paced(request: Request) -> None:
    """No more searches a minute from one person than a person makes. If the
    counter cannot be reached the search goes ahead, as with every other limit."""
    if not limiter.enabled:
        return
    try:
        allowed = await asyncio.wait_for(asyncio.to_thread(
            limiter.limiter.hit, SEARCH_LIMIT, caller_of(request), "footage-search"), STORE_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — the counter being down is never the caller's problem
        return
    if not allowed:
        raise HTTPException(429, "Too many searches in a minute. Wait a moment and try again.",
                            headers={"Retry-After": "60"})


class SearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=finder.MAX_TEXT)
    video_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    filmed_from: datetime | None = None
    filmed_to: datetime | None = None
    limit: int = Field(20, ge=1, le=50)


@router.post("/search", dependencies=[*_VIEW, Depends(_paced)])
async def search_footage(
    body: SearchBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """The moments that look most like the description, in one video or every
    ready video the person may see. The wording is kept in the audit log: a
    search for "a woman in a red dress" is a search for a person."""
    words = " ".join(body.text.split())
    if not words:
        raise HTTPException(422, "Describe what you are looking for.")
    for when in (body.filmed_from, body.filmed_to):
        if when is not None and when.tzinfo is None:
            raise HTTPException(422, "Say the time zone of the period searched.")
    params: dict = {}
    where = ["v.status = 'READY'"]
    scope = site_scope_clause(allowed, "v.site_id", params)
    if scope:
        where.append(scope)
    if body.video_id is not None:
        where.append("v.id = CAST(:vid AS uuid)")
        params["vid"] = str(body.video_id)
    if body.site_id is not None:
        where.append("v.site_id = CAST(:site AS uuid)")
        params["site"] = str(body.site_id)
    if body.filmed_from is not None:
        where.append("v.filmed_at IS NOT NULL AND v.filmed_at + make_interval(secs => coalesce(v.duration_seconds, 0)) "
                     ">= :from")
        params["from"] = body.filmed_from
    if body.filmed_to is not None:
        where.append("v.filmed_at IS NOT NULL AND v.filmed_at <= :to")
        params["to"] = body.filmed_to
    videos = (await db.execute(text(f"""
        SELECT v.id::text AS id, v.title, v.site_id, s.name AS site_name, v.place, v.filmed_at, v.index_model
          FROM footage_videos v LEFT JOIN sites s ON s.id = v.site_id
         WHERE {' AND '.join(where)}
    """), params)).mappings().all()
    if body.video_id is not None and not videos:
        raise HTTPException(404, "Video not found, or not ready to be searched.")
    try:
        read = await finder.embed(words)
    except finder.SearchUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    by_id = {v["id"]: v for v in videos}
    searched = [v["id"] for v in videos if v["index_model"] == read.model_version]
    found = await finder.search(db, read, searched, body.limit)
    moments = []
    for m in found["moments"]:
        v = by_id[m.video_id]
        moments.append({
            "video_id": m.video_id, "title": v["title"], "site_name": v["site_name"], "place": v["place"],
            "start": m.start, "end": m.end, "best": m.best, "likeness": round(m.likeness, 4),
            "match": round(m.match, 4), "label": m.label, "label_words": finder.STRENGTH_WORDS[m.label],
            "at": v["filmed_at"] + timedelta(seconds=m.best) if v["filmed_at"] else None,
        })
    await intel_audit.record(db, request, token, "footage.search", "footage_search", None, site_id=body.site_id,
                             detail={"text": words, "video_id": str(body.video_id) if body.video_id else None,
                                     "filmed_from": body.filmed_from.isoformat() if body.filmed_from else None,
                                     "filmed_to": body.filmed_to.isoformat() if body.filmed_to else None,
                                     "videos_searched": len(searched), "moments": len(moments)})
    answer = {
        "text": words, "model_version": read.model_version, "moments": moments,
        "strong": any(m["label"] != "LOW" for m in moments),
        "videos_searched": len(searched), "frames_searched": found["frames_searched"],
        # Ready videos indexed by another model are not searched until they are analysed again.
        "not_searched": len(videos) - len(searched),
        "thresholds": {"strong": finder.STRONG, "possible": finder.POSSIBLE, "weak": finder.WEAK},
    }
    await db.commit()
    return answer


# ── a frame, and the video itself, for the browser ────────────────────────────
# A plain <img> or <video> cannot send an Authorization header, so these take
# the access token in the address, as the evidence images and the live video do.

async def _media(video_id: uuid.UUID, token: str):
    """(the video's row, the caller) when the token's holder may watch it; else 401, 403 or 404."""
    try:
        payload = decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(401, str(exc)) from exc
    who = TokenPayload(user_id=payload["sub"], tenant_id=payload["tenant_id"], role_id=payload["role_id"],
                       support_session_id=payload.get("support_session_id"))
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": who.tenant_id})
        if "footage:view" not in await held_by(db, who.role_id, ("footage:view",)):
            raise HTTPException(403, "Missing permission: footage:view")
        allowed = await get_allowed_site_ids(who, db)
        row = (await db.execute(text("""
            SELECT id, site_id, title, status, storage_path, playable, proxy_path, content_type
              FROM footage_videos WHERE id = CAST(:id AS uuid)
        """), {"id": str(video_id)})).mappings().first()
        await db.rollback()
    if row is None or not is_site_allowed(allowed, row["site_id"]):
        raise HTTPException(404, "Video not found")
    return row, who


async def _serve(path: str, media_type: str):
    if settings.STORAGE_BACKEND == "s3":
        from app.core.object_store import presign_url

        return RedirectResponse(await presign_url(path, settings.S3_PRESIGN_TTL_SECONDS), status_code=307)
    full = footage.local_file(path)
    if not full.exists():
        raise HTTPException(404, "The file is not there.")
    return FileResponse(full, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{video_id:uuid}/frames/{second}.jpg")
async def frame_image(video_id: uuid.UUID, second: int, token: str = Query(..., description="JWT access token")):
    """The frame kept for one second of the video."""
    if second < 0:
        raise HTTPException(404, "No frame")
    row, _who = await _media(video_id, token)
    return await _serve(footage.frame_path(row["storage_path"], second), "image/jpeg")


@router.get("/{video_id:uuid}/video")
async def play_video(video_id: uuid.UUID, request: Request, token: str = Query(..., description="JWT access token")):
    """The video for the browser's player: the original when a browser plays it,
    else the copy the worker made. Opening it is audited; the player's later
    requests for other parts of the same file are not."""
    row, who = await _media(video_id, token)
    if row["status"] != "READY" or row["playable"] not in ("ORIGINAL", "PROXY"):
        raise HTTPException(409, "This video cannot be played in a browser. Its frames can still be searched.")
    path, media_type = ((row["storage_path"], row["content_type"] or "video/mp4") if row["playable"] == "ORIGINAL"
                        else (row["proxy_path"], "video/mp4"))
    asked = request.headers.get("range", "")
    if not asked or asked.replace(" ", "").startswith("bytes=0-"):
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": who.tenant_id})
            await write_audit_log(db, tenant_id=who.tenant_id, user_id=who.user_id, action="footage.play",
                                  resource_type="footage_video", resource_id=str(row["id"]),
                                  ip_address=_client_ip(request),
                                  detail={"title": row["title"], "copy": row["playable"],
                                          "site_id": str(row["site_id"]) if row["site_id"] else None})
            await db.commit()
    return await _serve(path, media_type)


# ── what the worker found: events, and the counts behind them ─────────────────

FINDING_WORDS = {"PEOPLE": "People present", "VEHICLES": "Vehicle present", "ALTERCATION": "Possible altercation"}
VERDICT_WORDS = {"CONFIRMED": "Confirmed", "REJECTED": "Not an altercation"}


@router.get("/{video_id:uuid}/findings", dependencies=_VIEW)
async def video_findings(
    video_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """What the worker found in one video: its events, in the order they happen,
    and for the timeline the seconds in which anything was counted. A count is
    how many were seen in one frame, never who."""
    v = await _one(db, video_id, allowed)
    rows = (await db.execute(text("""
        SELECT f.id, f.kind, f.start_second, f.end_second, f.best_second, f.score, f.most, f.reasons,
               f.model_version, f.verdict, f.verdict_note, f.verdict_at, u.full_name AS verdict_by_name, f.created_at
          FROM footage_findings f LEFT JOIN users u ON u.id = f.verdict_by_user_id
         WHERE f.video_id = CAST(:id AS uuid)
         ORDER BY f.start_second, f.kind, f.id
    """), {"id": str(video_id)})).mappings().all()
    counted = (await db.execute(text("""
        SELECT second, people, vehicles FROM footage_frames
         WHERE video_id = CAST(:id AS uuid) AND (coalesce(people, 0) > 0 OR coalesce(vehicles, 0) > 0)
         ORDER BY second
    """), {"id": str(video_id)})).all()
    findings = []
    for r in rows:
        f = dict(r)
        f["score"] = float(f["score"])
        f["kind_words"] = FINDING_WORDS[f["kind"]]
        f["verdict_words"] = VERDICT_WORDS.get(f["verdict"]) if f["verdict"] else None
        f["at"] = v["filmed_at"] + timedelta(seconds=f["best_second"]) if v["filmed_at"] else None
        findings.append(f)
    held = await held_by(db, token.role_id, HELD)
    return {"video_id": str(video_id), "status": v["status"], "duration_seconds": v["duration_seconds"],
            "findings": findings, "counts": [[r.second, r.people or 0, r.vehicles or 0] for r in counted],
            "may": _may(held)}


# ── a person's verdict on a possible altercation ──────────────────────────────

class VerdictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["CONFIRMED", "REJECTED"]
    note: str | None = Field(None, max_length=2000)


@router.post("/{video_id:uuid}/findings/{finding_id:uuid}/verdict", dependencies=_REVIEW)
async def judge_finding(
    video_id: uuid.UUID,
    finding_id: uuid.UUID,
    body: VerdictBody,
    request: Request,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Mark a possible altercation confirmed, or not an altercation, with a note.
    A verdict may be changed; each one is audited, with what it was before.
    Verdicts are what the thresholds are tuned against. Nothing else follows
    from one: no alert, no incident."""
    v = await _one(db, video_id, allowed)
    f = (await db.execute(text("""
        SELECT id, kind, verdict, start_second, end_second FROM footage_findings
         WHERE id = CAST(:f AS uuid) AND video_id = CAST(:v AS uuid) FOR UPDATE
    """), {"f": str(finding_id), "v": str(video_id)})).mappings().first()
    if f is None:
        raise HTTPException(404, "Finding not found")
    if f["kind"] != "ALTERCATION":
        raise HTTPException(409, "Only a possible altercation is given a verdict.")
    note = (body.note or "").strip() or None
    await db.execute(text("""
        UPDATE footage_findings
           SET verdict = :verdict, verdict_note = :note, verdict_by_user_id = CAST(:who AS uuid), verdict_at = now()
         WHERE id = CAST(:f AS uuid)
    """), {"verdict": body.verdict, "note": note, "who": token.user_id, "f": str(finding_id)})
    await intel_audit.record(db, request, token, "footage.verdict", "footage_finding", finding_id, site_id=v["site_id"],
                             detail={"video_id": str(video_id), "verdict": body.verdict, "was": f["verdict"],
                                     "note": note, "start_second": f["start_second"], "end_second": f["end_second"]})
    answer = {"id": str(finding_id), "verdict": body.verdict, "verdict_words": VERDICT_WORDS[body.verdict],
              "verdict_note": note}
    await db.commit()
    return answer


# ── a saved recording, copied in to be analysed ───────────────────────────────

class FromRecordingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recording_id: uuid.UUID
    title: str | None = Field(None, max_length=footage.TITLE_LIMIT)


@router.post("/from-recording", status_code=202, dependencies=_FROM_RECORDING)
async def analyse_recording(
    body: FromRecordingBody,
    request: Request,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db_with_tenant),
    token: TokenPayload = Depends(get_token_payload),
    allowed: list[str] | None = Depends(get_allowed_site_ids),
):
    """Copy a finished recording into Footage Review to be analysed. The copy is
    made after this answer, and the video waits as an upload until it is whole;
    from then on it is analysed like any other, and kept 30 days. The recording
    keeps its own retention."""
    rec = (await db.execute(text("""
        SELECT r.id, r.status, r.file_path, r.site_id, r.started_at, r.duration_seconds, c.name AS camera_name
          FROM recordings r LEFT JOIN cameras c ON c.id = r.camera_id
         WHERE r.id = CAST(:id AS uuid)
    """), {"id": str(body.recording_id)})).mappings().first()
    if rec is None or not is_site_allowed(allowed, rec["site_id"]):
        raise HTTPException(404, "Recording not found")
    if rec["status"] != "completed" or not rec["file_path"]:
        raise HTTPException(409, "Only a finished recording can be analysed.")
    try:
        source = footage.recording_file(rec["file_path"])
    except ValueError as exc:
        raise HTTPException(409, "The recording's file is not where recordings are kept.") from exc
    if not source.exists():
        raise HTTPException(409, "The recording's file is no longer kept.")
    already = (await db.execute(text("""
        SELECT id FROM footage_videos WHERE recording_id = CAST(:id AS uuid) AND status <> 'FAILED' LIMIT 1
    """), {"id": str(body.recording_id)})).first()
    if already is not None:
        raise HTTPException(409, {"message": "This recording is already in Footage Review.", "video_id": str(already.id)})
    camera = rec["camera_name"] or "Camera"
    title = (body.title or "").strip() or f"{camera} recording"
    video_id = uuid.uuid4()
    path = footage.original_path(token.tenant_id, video_id, ".mp4")
    size = source.stat().st_size
    await db.execute(text("""
        INSERT INTO footage_videos (id, tenant_id, source, recording_id, title, original_filename, content_type,
                                    size_bytes, part_size, storage_path, site_id, place, filmed_at,
                                    uploaded_by_user_id, expires_at)
        VALUES (:id, current_setting('app.current_tenant')::uuid, 'RECORDING', CAST(:rec AS uuid), :title, :name,
                'video/mp4', :size, :part, :path, :site, :place, :filmed, CAST(:who AS uuid),
                now() + make_interval(days => :days))
    """), {"id": video_id, "rec": str(body.recording_id), "title": title[:footage.TITLE_LIMIT],
           "name": source.name[:footage.FILENAME_LIMIT], "size": max(size, 1), "part": footage.PART_SIZE,
           "path": path, "site": rec["site_id"], "place": camera[:footage.PLACE_LIMIT], "filmed": rec["started_at"],
           "who": token.user_id, "days": footage.RETENTION_DAYS})
    await intel_audit.record(db, request, token, "footage.from_recording", "footage_video", video_id,
                             site_id=rec["site_id"], detail={"recording_id": str(body.recording_id), "title": title,
                                                             "camera": camera, "size_bytes": size})
    answer = _out(await _one(db, video_id, allowed), datetime.now(timezone.utc))
    await db.commit()
    background.add_task(_copy_recording, token.tenant_id, video_id, source, path, max(size, 1),
                        getattr(request.app.state, "redis", None))
    return answer


async def _copy_recording(tenant_id: str, video_id: uuid.UUID, source, path: str, size: int, redis) -> None:
    """After the answer: the copy, then the video waits to be analysed - or says it could not be copied."""
    try:
        await asyncio.to_thread(footage.copy_in, source, path)
        copied = True
    except Exception:  # noqa: BLE001 — said on the video, which stays until a person or the 30 days remove it
        logger.exception("footage %s: the recording could not be copied", video_id)
        copied = False
    async with AsyncSessionLocal() as db:
        await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
        if copied:
            await db.execute(text("""
                UPDATE footage_videos SET status = 'QUEUED', received_bytes = :size, upload_completed_at = now(),
                                          updated_at = now()
                 WHERE id = CAST(:id AS uuid) AND status = 'UPLOADING'
            """), {"id": str(video_id), "size": size})
        else:
            await db.execute(text("""
                UPDATE footage_videos SET status = 'FAILED', updated_at = now(),
                                          error = 'The recording could not be copied into Footage Review.'
                 WHERE id = CAST(:id AS uuid) AND status = 'UPLOADING'
            """), {"id": str(video_id)})
        await db.commit()
    if copied:
        await footage.announce(redis, tenant_id, video_id)
