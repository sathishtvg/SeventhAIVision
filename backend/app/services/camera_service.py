"""Pure stream-health state machine, deliberately separated from any cv2/RTSP
I/O so it's unit-testable without a real camera (plan §13's test_cameras.py
targets exactly this logic). ingestion_main.py wires real capture calls around
an instance of this per camera."""

from dataclasses import dataclass


@dataclass
class HealthTransition:
    new_status: str
    event_type: str  # matches CameraHealthEventType in shared/shared/enums.py


class StreamHealthTracker:
    DEGRADED_AFTER_FAILURES = 3
    OFFLINE_AFTER_FAILURES = 10
    MAX_BACKOFF_SECONDS = 60

    def __init__(self) -> None:
        self.consecutive_failures = 0
        # Deliberately not "online" — a fresh tracker (e.g. after an ingestion
        # restart) hasn't actually observed a successful read yet, so it must
        # not assume health state that matches the DB's last-known status by
        # luck. If it defaulted to "online", the first read after a restart
        # that happens to succeed would see previous_status == "online"
        # already and skip writing the transition, leaving streams.status
        # stuck at a stale "offline" in the DB forever even while frames are
        # flowing live.
        self.status = "unknown"

    def record_success(self) -> HealthTransition | None:
        """A camera_health_events row is written only on a *transition* (plan
        §5), not on every successful read — avoids table bloat on a healthy
        camera streaming for years."""
        previous_status = self.status
        self.consecutive_failures = 0
        self.status = "online"
        if previous_status != "online":
            return HealthTransition(new_status="online", event_type="stream_reconnected")
        return None

    def record_failure(self) -> HealthTransition | None:
        self.consecutive_failures += 1
        previous_status = self.status

        if self.consecutive_failures >= self.OFFLINE_AFTER_FAILURES:
            self.status = "offline"
        elif self.consecutive_failures >= self.DEGRADED_AFTER_FAILURES:
            self.status = "degraded"

        if self.status == previous_status:
            return None
        event_type = "stream_degraded" if self.status == "degraded" else "stream_disconnected"
        return HealthTransition(new_status=self.status, event_type=event_type)

    def backoff_seconds(self) -> float:
        """1, 2, 4, 8, 16, 32, 60, 60, ... (plan §5) — 2**(n-1) capped at 60,
        not 2**n, so the first failure waits 1s rather than 2s."""
        if self.consecutive_failures < 1:
            return 0
        return min(2 ** (self.consecutive_failures - 1), self.MAX_BACKOFF_SECONDS)
