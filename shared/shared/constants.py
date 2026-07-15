"""Naming conventions shared by backend (publishes/subscribes) and ai-worker
(publishes/consumes) so the two codebases can't drift on a string literal."""

FRAME_JOBS_STREAM = "frame_jobs"

CONSUMER_GROUPS: dict[str, str] = {
    "lpr": "lpr_workers",
    "face": "face_workers",
    "intrusion": "intrusion_workers",
    "ppe": "ppe_workers",
    "crowd": "crowd_workers",
    "fire_smoke": "fire_smoke_workers",
    "weapon": "weapon_workers",
    "behavior": "behavior_workers",
    # Phase 5: advanced safety modules
    "tampering": "tampering_workers",
    "abandoned": "abandoned_workers",
    "fall": "fall_workers",
}

TENANT_EVENTS_CHANNEL_PREFIX = "tenant_events:"


def tenant_events_channel(tenant_id: str) -> str:
    return f"{TENANT_EVENTS_CHANNEL_PREFIX}{tenant_id}"


# Crash-recovery tuning (plan §6, §10): pending messages idle longer than this
# are considered crashed-owner and reclaimed via XCLAIM on the next loop pass.
IDLE_CLAIM_MS = 30_000
