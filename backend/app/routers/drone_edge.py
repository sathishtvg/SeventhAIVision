"""The API a site edge gateway calls — and nothing else calls.

  POST /api/v1/drone-edge/sync                    everything since the last batch; answered
                                                  with the gateway's current work
  POST /api/v1/drone-edge/sessions/{id}/claim     take a ready session to fly
  PUT  /api/v1/drone-edge/media/{client_ref}      the bytes of a file the centre asked for

AUTHENTICATED BY THE GATEWAY'S OWN CREDENTIAL (X-Gateway-Key), issued once when
the gateway is registered and stored only as a hash. It names its tenant, so the
lookup runs under that tenant's row-level security like every other request — no
SECURITY DEFINER lookup, no cross-tenant query. A user's token is not accepted
here, and a gateway credential is accepted nowhere else.

NOT LICENCE-GATED, EXCEPT TO START A FLIGHT. A lapsed licence must not strand a
drone in the air or throw away what a gateway recorded: syncing is always
accepted. Claiming a session runs pre-flight, which refuses an unlicensed launch.

Rate limited per address, generously: one gateway syncs every few seconds and
uploads its files, and several may share a site's one public address.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.limiter import limiter
from app.db.session import AsyncSessionLocal
from app.services import drone_edge_sync as es
from app.services import drone_sessions as ds
from app.services.drone_edge_wire import SyncBatch
from app.services.drone_runner import RedisPublisher, announce

router = APIRouter(prefix="/api/v1/drone-edge", tags=["drone-edge"])

_UNAUTHORISED = "Invalid edge gateway credential."
#: Media bytes are checked against what the gateway declared; these are the
#: formats a drone file may be.
_MAGIC = {"SNAPSHOT": ((0, b"\xff\xd8\xff"), (0, b"\x89PNG")),
          "CLIP": ((4, b"ftyp"),), "PRE_CLIP": ((4, b"ftyp"),), "EVENT_CLIP": ((4, b"ftyp"),),
          "POST_CLIP": ((4, b"ftyp"),)}


@dataclass
class Gateway:
    db: AsyncSession
    row: dict
    tenant_id: str


async def get_gateway(
    x_gateway_key: str | None = Header(None, alias="X-Gateway-Key"),
) -> AsyncGenerator[Gateway, None]:
    parsed = es.parse_key(x_gateway_key)
    if parsed is None:
        raise HTTPException(401, _UNAUTHORISED)
    tenant_id, digest = parsed
    async with AsyncSessionLocal() as db:
        try:
            active = (await db.execute(text("SELECT is_active FROM tenants WHERE id = CAST(:t AS uuid)"),
                                       {"t": tenant_id})).scalar()
            if not active:
                raise HTTPException(401, _UNAUTHORISED)
            await db.execute(text("SELECT set_config('app.current_tenant', :t, true)"), {"t": tenant_id})
            gw = await es.find_gateway(db, digest)
            if gw is None:
                raise HTTPException(401, _UNAUTHORISED)
            if not gw["is_active"]:
                raise HTTPException(403, "This edge gateway is disabled.")
            yield Gateway(db, gw, tenant_id)
        finally:
            await db.rollback()


def _publisher(request: Request):
    redis = getattr(request.app.state, "redis", None)
    return RedisPublisher(redis) if redis is not None else None


@router.post("/sync")
@limiter.limit("600/minute")
async def sync(request: Request, batch: SyncBatch, gw: Gateway = Depends(get_gateway)):
    """Take in a batch — telemetry and progress of the flights this gateway
    flies, drone health, command outcomes, events, file descriptions — and
    answer with what the gateway should be doing now. An empty batch is a
    heartbeat. Resending a batch returns the first answer."""
    now = ds.utcnow()
    out = ds.Announcements()
    result = await es.process_batch(gw.db, gw.row, batch, now, out)
    work = await es.assignment(gw.db, gw.row, batch.have_sessions, now)
    await gw.db.commit()
    await announce(_publisher(request), gw.tenant_id, out)
    return {**result, "assignment": work}


@router.post("/sessions/{session_id}/claim")
@limiter.limit("120/minute")
async def claim(session_id: uuid.UUID, request: Request, gw: Gateway = Depends(get_gateway)):
    """Take a ready session to fly. Pre-flight runs again first; a refusal is
    recorded on the session (BLOCKED, with every reason) and answered 409."""
    now = ds.utcnow()
    out = ds.Announcements()
    try:
        claimed = await es.claim(gw.db, gw.row, session_id, now, out)
    except es.ClaimRefused as refused:
        await gw.db.commit()          # a blocked launch is on the record
        await announce(_publisher(request), gw.tenant_id, out)
        return JSONResponse(status_code=refused.status,
                            content={"detail": refused.reason, "preflight": refused.preflight})
    await gw.db.commit()
    await announce(_publisher(request), gw.tenant_id, out)
    return claimed


@router.put("/media/{client_ref}")
@limiter.limit("300/minute")
async def upload_media(
    client_ref: uuid.UUID, request: Request,
    x_checksum: str = Header(..., alias="X-Checksum-Sha256"),
    gw: Gateway = Depends(get_gateway),
):
    """The bytes of a file this gateway described and the centre asked for.
    Accepted only if they are exactly the file described: same size, same
    SHA-256, and the format its kind says. Sending a stored file again is
    harmless."""
    row = (await gw.db.execute(text(
        "SELECT * FROM drone_event_media WHERE client_ref = :r FOR UPDATE"), {"r": client_ref})).mappings().first()
    if row is None or row["edge_gateway_id"] != gw.row["id"]:
        raise HTTPException(404, "No such file described by this gateway.")
    if x_checksum.strip().lower() != row["checksum_sha256"]:
        raise HTTPException(409, "That checksum is not the one this file was described with.")
    if row["sync_state"] == "synced":
        return {"client_ref": str(client_ref), "stored": False, "duplicate": True}
    if row["sync_state"] == "not_required":
        raise HTTPException(409, "The recording policy keeps this file at the site; the centre did not ask for it.")

    captured = row["captured_at"]
    ext = ".jpg" if row["media_kind"] == "SNAPSHOT" else ".mp4"
    rel = f"drone/{gw.tenant_id}/{captured:%Y/%m/%d}/{client_ref}{ext}"
    local = settings.STORAGE_BACKEND != "s3"
    if local:
        final = Path(settings.EVIDENCE_ROOT) / rel
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir = str(final.parent)
    else:
        tmp_dir = None
    fd, tmp = tempfile.mkstemp(prefix=".upload-", dir=tmp_dir)
    digest, size, head = hashlib.sha256(), 0, b""
    try:
        with os.fdopen(fd, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > row["size_bytes"]:
                    raise HTTPException(413, "The file is larger than it was described.")
                if len(head) < 16:
                    head += chunk[:16 - len(head)]
                digest.update(chunk)
                fh.write(chunk)
        if size != row["size_bytes"] or digest.hexdigest() != row["checksum_sha256"]:
            raise HTTPException(422, "The bytes received are not the file described (size or checksum "
                                     "differ). Send it again.")
        if not any(head[off:off + len(sig)] == sig for off, sig in _MAGIC[row["media_kind"]]):
            raise HTTPException(415, f"That is not a {row['media_kind'].lower().replace('_', ' ')} "
                                     "file this system stores (JPEG or PNG images, MP4 video).")
        if local:
            os.replace(tmp, final)
        else:
            from app.core.object_store import put_object
            import asyncio
            data = Path(tmp).read_bytes()
            await asyncio.to_thread(put_object, rel, data,
                                    "image/jpeg" if ext == ".jpg" else "video/mp4")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    await gw.db.execute(text("""
        UPDATE drone_event_media
           SET storage_path = :p, storage_location = 'both', sync_state = 'synced', synced_at = now()
         WHERE id = :id
    """), {"p": rel, "id": row["id"]})
    out = ds.Announcements()
    out.add("drone_media_synced", {"media_id": str(row["id"]), "event_id": str(row["event_id"]) if row["event_id"] else None,
                                   "session_id": str(row["session_id"]) if row["session_id"] else None,
                                   "media_kind": row["media_kind"], "size_bytes": size})
    await gw.db.commit()
    await announce(_publisher(request), gw.tenant_id, out)
    return {"client_ref": str(client_ref), "stored": True, "duplicate": False, "size_bytes": size}
