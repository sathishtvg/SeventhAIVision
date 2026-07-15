from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class FrameJob(BaseModel):
    """Payload for a single XADD to the `frame_jobs` Redis Stream.

    Frame is carried as inline base64 JPEG, not a shared-volume file path: this
    keeps every ai-worker-* consumer fully decoupled from the ingestion
    container's filesystem, which matters once workers scale across hosts.
    """

    job_id: UUID
    tenant_id: UUID
    camera_id: UUID
    frame_jpeg_b64: str
    frame_width: int
    frame_height: int
    captured_at: datetime
    ai_modules_enabled: list[str]

    def to_redis_fields(self) -> dict[str, str]:
        """XADD requires a flat str->str field mapping."""
        return {"payload": self.model_dump_json()}

    @classmethod
    def from_redis_fields(cls, fields: dict[Any, Any]) -> "FrameJob":
        """`fields` may have str or bytes keys/values depending on the redis
        client's decode_responses setting — handle both without assuming one."""
        payload = fields.get("payload", fields.get(b"payload"))
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        return cls.model_validate_json(payload)
