import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
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
