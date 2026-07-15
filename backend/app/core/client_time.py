"""Client-supplied event timestamps for offline sync (Gap 88).

Guards scan checkpoints in signal-dead basements; the mobile outbox replays
the request later carrying the ORIGINAL event time. These helpers validate
that a client timestamp is plausible: parseable ISO-8601, not meaningfully
in the future (small clock-skew allowance), and not older than the replay
window.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

MAX_AGE_HOURS = 48
FUTURE_SKEW_MINUTES = 5


def parse_client_timestamp(value: str | None, field: str = "timestamp") -> datetime | None:
    """None passes through (server uses now()). Otherwise validate bounds;
    raises 422 on garbage, future times, or times past the replay window."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"{field} must be an ISO-8601 datetime")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if parsed > now + timedelta(minutes=FUTURE_SKEW_MINUTES):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"{field} cannot be in the future")
    if parsed < now - timedelta(hours=MAX_AGE_HOURS):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"{field} is older than the {MAX_AGE_HOURS}h offline sync window")
    return parsed
