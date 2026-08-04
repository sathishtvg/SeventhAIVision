from fastapi import HTTPException, UploadFile, status

# Single check-in/enrollment photo (JPEG/PNG) — generous for a phone camera shot.
MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024
# Employee/leave document attachments (PDF, scanned images, etc.).
MAX_DOCUMENT_UPLOAD_BYTES = 20 * 1024 * 1024
# Watchlist bulk-import CSV — row count is capped separately, this bounds raw bytes.
MAX_CSV_UPLOAD_BYTES = 5 * 1024 * 1024

_CHUNK_SIZE = 1024 * 1024


async def read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Reads an UploadFile in chunks, aborting with 413 the instant max_bytes
    is exceeded. Unlike `await file.read()` (no cap at all), this never
    buffers more than max_bytes + one chunk in memory or on disk."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"File exceeds maximum allowed size of {max_bytes // (1024 * 1024)} MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)
