"""S3-compatible object store client (MinIO in dev, any S3-compat in prod).

Only imported when settings.STORAGE_BACKEND == "s3" — boto3 is an optional
dep that need not be installed in local-disk deployments.
"""

import asyncio
import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import boto3 as _boto3

_s3_client = None


def _get_client():
    global _s3_client
    if _s3_client is None:
        import boto3

        from app.core.config import settings

        _s3_client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
        )
    return _s3_client


def _presign_sync(storage_path: str, ttl: int) -> str:
    from app.core.config import settings

    return _get_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": storage_path},
        ExpiresIn=ttl,
    )


async def presign_url(storage_path: str, ttl: int) -> str:
    """Generate a presigned GET URL; runs boto3 (sync) in a thread pool."""
    return await asyncio.to_thread(_presign_sync, storage_path, ttl)


def delete_object(storage_path: str) -> None:
    """Delete one object from the bucket (sync — called from the scheduler)."""
    from app.core.config import settings

    _get_client().delete_object(Bucket=settings.S3_BUCKET, Key=storage_path)


def put_object(storage_path: str, data: bytes, content_type: str = "image/jpeg") -> None:
    """Upload bytes to the bucket under the given key (sync — for the ai-worker)."""
    from app.core.config import settings

    _get_client().put_object(
        Bucket=settings.S3_BUCKET,
        Key=storage_path,
        Body=io.BytesIO(data),
        ContentType=content_type,
    )
