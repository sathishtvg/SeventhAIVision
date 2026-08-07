import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import InvalidTokenError, decode_access_token
from app.db.session import AsyncSessionLocal
from app.dependencies.permissions import require_permission
from app.dependencies.tenant import get_db_with_tenant

router = APIRouter(prefix="/api/v1/evidence", tags=["evidence"])


@router.get("", dependencies=[Depends(require_permission("evidence:read"))])
async def list_evidence(db: AsyncSession = Depends(get_db_with_tenant), limit: int = 50):
    result = await db.execute(
        text(
            """
            SELECT id, detection_id, incident_id, media_type, storage_path, checksum_sha256, captured_at
            FROM evidence ORDER BY captured_at DESC LIMIT :limit
            """
        ),
        {"limit": min(limit, 200)},
    )
    return [dict(row._mapping) for row in result]


@router.get("/{evidence_id}/file", dependencies=[Depends(require_permission("evidence:read"))])
async def download_evidence_file(
    evidence_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    row = (
        await db.execute(
            text("SELECT storage_path, media_type FROM evidence WHERE id = :id"),
            {"id": evidence_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence record not found")

    if settings.STORAGE_BACKEND == "s3":
        from app.core.object_store import presign_url

        url = await presign_url(row.storage_path, settings.S3_PRESIGN_TTL_SECONDS)
        return RedirectResponse(url=url, status_code=307)

    file_path = Path(settings.EVIDENCE_ROOT) / row.storage_path
    if not file_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence file not found on disk")

    media_type = "image/jpeg" if row.media_type == "image" else "video/mp4"
    return FileResponse(str(file_path), media_type=media_type)


@router.get("/{evidence_id}/image")
async def get_evidence_image(
    evidence_id: uuid.UUID,
    token: str = Query(..., description="JWT access token"),
    w: int | None = Query(
        None, ge=16, le=2048,
        description="Max width in px. Returns a downscaled JPEG for thumbnail use.",
    ),
):
    """Same bytes as /file, but authenticated by query-param JWT.

    WHY THIS EXISTS
        A plain <img src> cannot set an Authorization header, so /file returns
        401 to a browser image request. The Evidence gallery has been rendering
        <img src="/api/v1/evidence/{id}/file"> with no token since it was
        written — verified 401 against a live evidence id — which is why that
        page has an imgError fallback that always fires.

        This is the same query-param-JWT pattern already used by
        shifts.py::get_checkin_photo and the streams live/HLS endpoints, added
        here rather than reworked into /file so existing API callers that do
        send the header keep working unchanged.

    The token travels in the URL, so it lands in access logs — acceptable for
    the same reason it is on the other image endpoints: access tokens are
    short-lived (15 min) and this is the only way a browser can render a
    protected image inline.
    """
    try:
        payload = decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc))

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tid, true)"),
            {"tid": payload["tenant_id"]},
        )
        perm = await session.execute(
            text(
                "SELECT 1 FROM role_permissions rp "
                "JOIN permissions p ON p.id = rp.permission_id "
                "WHERE rp.role_id = :role_id AND p.code = 'evidence:read'"
            ),
            {"role_id": payload["role_id"]},
        )
        if perm.first() is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing permission: evidence:read")

        # RLS is set above, so a cross-tenant id simply returns no row.
        row = (await session.execute(
            text("SELECT storage_path, media_type FROM evidence WHERE id = :id"),
            {"id": evidence_id},
        )).first()

    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence record not found")

    if settings.STORAGE_BACKEND == "s3":
        from app.core.object_store import presign_url

        url = await presign_url(row.storage_path, settings.S3_PRESIGN_TTL_SECONDS)
        return RedirectResponse(url=url, status_code=307)

    file_path = Path(settings.EVIDENCE_ROOT) / row.storage_path
    if not file_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidence file not found on disk")

    # Thumbnail path. A detections list renders ~100 rows, and a full frame is
    # ~270KB, so serving originals as 40px thumbnails costs ~27MB per page view
    # for pixels nobody sees. Downscaling here rather than in CSS is the whole
    # point — the browser must not download the full frame to shrink it.
    #
    # Only images resize; a clip has no meaningful still to scale, so `w` is
    # ignored for video and the original is served.
    if w is not None and row.media_type == "image":
        import io

        from PIL import Image

        def _thumb() -> bytes:
            with Image.open(file_path) as im:
                im = im.convert("RGB")
                im.thumbnail((w, w))  # preserves aspect ratio, never upscales
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=80, optimize=True)
                return buf.getvalue()

        # Pillow is synchronous CPU work — keep it off the event loop, same as
        # the face/liveness model calls elsewhere in this codebase.
        data = await asyncio.to_thread(_thumb)
        return Response(
            content=data,
            media_type="image/jpeg",
            # Evidence bytes are immutable once written, so this is safe to
            # cache hard; it is what stops a re-render refetching every row.
            headers={"Cache-Control": "private, max-age=86400"},
        )

    media_type = "image/jpeg" if row.media_type == "image" else "video/mp4"
    return FileResponse(str(file_path), media_type=media_type)


@router.get("/by-detection/{detection_id}", dependencies=[Depends(require_permission("evidence:read"))])
async def evidence_for_detection(
    detection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
):
    """Resolve a detection's captures to evidence ids.

    Exists for the visitor entry prompt, which arrives over the websocket
    carrying a detection id rather than evidence ids: the worker writes the
    plate crop on a separate path, so at publish time the evidence row may not
    have committed. The operator's dialog calls this when it opens — strictly
    later — and can retry if the crop is still in flight.

    Returns nulls rather than 404 for a detection with no evidence: "this read
    has no picture yet" is a normal state the caller renders, not an error.
    """
    row = (
        await db.execute(
            text(
                """
                SELECT
                  (array_agg(id) FILTER (WHERE capture_kind = 'frame'))[1]      AS frame_evidence_id,
                  (array_agg(id) FILTER (WHERE capture_kind = 'plate_crop'))[1] AS plate_evidence_id
                FROM evidence
                WHERE detection_id = :did
                """
            ),
            {"did": detection_id},
        )
    ).first()
    return {
        "frame_evidence_id": str(row.frame_evidence_id) if row and row.frame_evidence_id else None,
        "plate_evidence_id": str(row.plate_evidence_id) if row and row.plate_evidence_id else None,
    }
