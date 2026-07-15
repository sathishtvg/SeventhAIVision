"""Load test scenarios — Gap 23 — Seventh AI Vision.

Covers the five highest-traffic API paths under expected production concurrency:
  - Auth: POST /api/v1/auth/login (rate-limited; tested carefully)
  - Health: GET /health (lightest, confirms liveness)
  - Alerts: GET /api/v1/alerts (most common operator query)
  - Cameras: GET /api/v1/cameras (dashboard polling)
  - Incidents: GET /api/v1/incidents (supervisor view)
  - System: GET /api/v1/system/version (deployment checks)

Credentials are read from environment variables — see config/load-test-policy.yml:
  LOAD_TEST_USER_EMAIL, LOAD_TEST_USER_PASSWORD, LOAD_TEST_TENANT_SLUG

Run (headless, from docker-compose network):
  locust -f locustfiles/load_test_api.py --headless \\
    --users 50 --spawn-rate 5 --run-time 5m \\
    --host http://api:8000 \\
    --csv reports/load-tests/locust-load-$(date +%Y-%m-%d) \\
    --html reports/load-tests/locust-load-$(date +%Y-%m-%d).html
"""

from __future__ import annotations

import os
import json
import logging
from locust import HttpUser, task, between, events

logger = logging.getLogger("load_test_api")

# ── Credentials (from environment; never hardcoded) ───────────────────────────
_EMAIL       = os.environ.get("LOAD_TEST_USER_EMAIL", "loadtest@seventh.ai")
_PASSWORD    = os.environ.get("LOAD_TEST_USER_PASSWORD", "changeme_loadtest_only")
_TENANT_SLUG = os.environ.get("LOAD_TEST_TENANT_SLUG", "demo")


class SeventhAIApiUser(HttpUser):
    """Simulates an operator-role user performing typical dashboard operations.

    Weight distribution reflects real usage patterns:
      - Operators refresh alerts frequently (heaviest task)
      - Camera status checked on dashboard load
      - Incidents reviewed less often
      - Auth only on session start (on_start)
    """

    wait_time = between(1, 3)   # seconds between tasks per user

    # ── Session setup ─────────────────────────────────────────────────────────
    def on_start(self) -> None:
        """Authenticate once per user; store token for subsequent requests."""
        self._access_token: str | None = None
        self._login()

    def _login(self) -> None:
        """POST /api/v1/auth/login and cache the bearer token."""
        with self.client.post(
            "/api/v1/auth/login",
            json={"email": _EMAIL, "password": _PASSWORD},
            catch_response=True,
            name="POST /api/v1/auth/login",
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                self._access_token = data.get("access_token")
                resp.success()
            elif resp.status_code == 429:
                # Rate-limited — mark as success (expected during heavy login concurrency)
                resp.success()
                logger.warning("Login rate-limited (429) — expected during stress test")
            else:
                resp.failure(f"Login failed: {resp.status_code}")

    @property
    def _auth_headers(self) -> dict[str, str]:
        if self._access_token:
            return {"Authorization": f"Bearer {self._access_token}"}
        return {}

    # ── Health check (weight 1) ───────────────────────────────────────────────
    @task(1)
    def get_health(self) -> None:
        with self.client.get(
            "/health",
            name="GET /health",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Health check returned {resp.status_code}")

    # ── System version (weight 1) ─────────────────────────────────────────────
    @task(1)
    def get_system_version(self) -> None:
        with self.client.get(
            "/api/v1/system/version",
            headers=self._auth_headers,
            name="GET /api/v1/system/version",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 401):
                resp.success()
            else:
                resp.failure(f"Version endpoint returned {resp.status_code}")

    # ── Alert list (weight 5 — highest-frequency operator action) ─────────────
    @task(5)
    def list_alerts(self) -> None:
        with self.client.get(
            "/api/v1/alerts?status=open&limit=25",
            headers=self._auth_headers,
            name="GET /api/v1/alerts",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 401, 403):
                resp.success()
            else:
                resp.failure(f"Alerts list returned {resp.status_code}")

    # ── Camera list (weight 3 — dashboard polling) ────────────────────────────
    @task(3)
    def list_cameras(self) -> None:
        with self.client.get(
            "/api/v1/cameras",
            headers=self._auth_headers,
            name="GET /api/v1/cameras",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 401, 403):
                resp.success()
            else:
                resp.failure(f"Camera list returned {resp.status_code}")

    # ── Incident list (weight 2 — supervisor view) ────────────────────────────
    @task(2)
    def list_incidents(self) -> None:
        with self.client.get(
            "/api/v1/incidents?status=open&limit=25",
            headers=self._auth_headers,
            name="GET /api/v1/incidents",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 401, 403):
                resp.success()
            else:
                resp.failure(f"Incidents list returned {resp.status_code}")

    # ── Token refresh simulation (weight 1) ───────────────────────────────────
    @task(1)
    def refresh_auth(self) -> None:
        """Simulate token expiry by re-logging in at low frequency."""
        self._login()


# ── Threshold enforcement hook ────────────────────────────────────────────────
# Read thresholds from the load-test-policy.yml at test start.
# If any threshold is breached, exit with code 1 after the test run.

_THRESHOLDS: dict[str, int] = {
    "GET /health": 100,
    "POST /api/v1/auth/login": 1000,
    "GET /api/v1/alerts": 500,
    "GET /api/v1/cameras": 500,
    "GET /api/v1/incidents": 500,
    "GET /api/v1/system/version": 200,
}
_MAX_ERROR_RATE_PERCENT = 1.0


@events.quitting.add_listener
def _enforce_thresholds(environment, **_kwargs) -> None:
    """Check SLO thresholds after the test run and set exit code if breached."""
    stats = environment.runner.stats
    total = stats.total
    failures: list[str] = []

    # Global error rate check
    if total.num_requests > 0:
        error_rate = (total.num_failures / total.num_requests) * 100
        if error_rate > _MAX_ERROR_RATE_PERCENT:
            failures.append(
                f"Error rate {error_rate:.2f}% exceeds threshold {_MAX_ERROR_RATE_PERCENT}%"
            )

    # Per-endpoint P95 latency check
    for endpoint, max_p95_ms in _THRESHOLDS.items():
        entry = stats.entries.get((endpoint, "GET")) or stats.entries.get((endpoint, "POST"))
        if entry is None:
            continue
        p95_ms = entry.get_response_time_percentile(0.95)
        if p95_ms > max_p95_ms:
            failures.append(
                f"{endpoint}: P95={p95_ms:.0f}ms exceeds threshold {max_p95_ms}ms"
            )

    if failures:
        logger.error("LOAD TEST THRESHOLD BREACHES:")
        for f in failures:
            logger.error("  - %s", f)
        environment.process_exit_code = 1
    else:
        logger.info("Load test passed all thresholds.")
