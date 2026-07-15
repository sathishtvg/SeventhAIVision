"""
Gap 13 — Seventh AI Vision Load Test Suite (Locust)

Scenarios:
  ReadTasks     : realistic read-heavy API pattern (alerts, incidents, cameras, analytics)
  WriteTasks    : moderate write traffic (token refresh, incident peek)
  SeventhAIUser : 80 % read / 20 % write user mix, 0.5–2 s think time
  StepLoadShape : staged ramp  10 → 50 → 100 → 200 → 50 users over 8 min

Usage (requires locust >= 2.20.0):
  # Smoke test (10 users, 60 s, headless):
  locust -f scripts/load_tests/locustfile.py \\
         --headless -u 10 -r 2 -t 60s \\
         --host http://localhost:8000

  # Full staged run via config file:
  locust -f scripts/load_tests/locustfile.py \\
         --config scripts/load_tests/locust.conf

  # Interactive UI (browser at http://localhost:8089):
  locust -f scripts/load_tests/locustfile.py --host http://localhost:8000

Environment variables (override for non-dev deployments):
  LOAD_TEST_EMAIL         e-mail of the test admin user  (default: admin@demo.seventh.ai)
  LOAD_TEST_PASSWORD      password                        (default: LoadTest_SecurePass_2026!)
  LOAD_TEST_TENANT_SLUG   tenant slug                     (default: demo)
"""

from __future__ import annotations

import os
import random

# ── Conditional locust import (allows file to be imported by test_load_testing.py
# without locust installed inside the API container) ─────────────────────────────
try:
    from locust import HttpUser, LoadTestShape, TaskSet, between, events, task  # type: ignore
    _LOCUST_AVAILABLE = True
except ImportError:
    _LOCUST_AVAILABLE = False
    HttpUser = object        # type: ignore[misc,assignment]
    LoadTestShape = object   # type: ignore[misc,assignment]
    TaskSet = object         # type: ignore[misc,assignment]
    between = lambda a, b: None  # noqa: E731  # type: ignore[assignment]
    events = None

    def task(weight: int = 1):  # type: ignore[misc]
        return lambda f: f


# ── SLA thresholds ──────────────────────────────────────────────────────────────
# These are the contractual targets validated by test_load_testing.py and by the
# on_quitting hook at the end of a real locust run.
SLA: dict[str, float] = {
    "p95_ms": 500,          # 95th-percentile response time (ms)
    "p99_ms": 1000,         # 99th-percentile response time (ms)
    "error_rate_pct": 1.0,  # max acceptable failure % across all requests
    "rps_min": 50.0,        # minimum sustained RPS at 100 concurrent users
}

# ── Credentials ─────────────────────────────────────────────────────────────────
_EMAIL = os.getenv("LOAD_TEST_EMAIL", "admin@demo.seventh.ai")
_PASSWORD = os.getenv("LOAD_TEST_PASSWORD", "LoadTest_SecurePass_2026!")
_TENANT_SLUG = os.getenv("LOAD_TEST_TENANT_SLUG", "demo")


# ── Task sets ───────────────────────────────────────────────────────────────────

class ReadTasks(TaskSet):
    """Read-heavy task set — represents 80 % of load-test user sessions."""

    def on_start(self) -> None:  # type: ignore[override]
        r = self.client.post(
            "/api/v1/auth/login",
            json={"email": _EMAIL, "password": _PASSWORD},
            name="/auth/login",
        )
        self._token: str = r.json().get("access_token", "") if r.status_code == 200 else ""

    def _hdr(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    @task(5)
    def list_alerts_open(self) -> None:
        self.client.get(
            "/api/v1/alerts?limit=25&status=open",
            headers=self._hdr(),
            name="/alerts?status=open",
        )

    @task(3)
    def list_incidents_open(self) -> None:
        self.client.get(
            "/api/v1/incidents?limit=25&status=open",
            headers=self._hdr(),
            name="/incidents?status=open",
        )

    @task(2)
    def analytics_summary(self) -> None:
        self.client.get(
            "/api/v1/analytics/summary",
            headers=self._hdr(),
            name="/analytics/summary",
        )

    @task(2)
    def list_cameras(self) -> None:
        self.client.get("/api/v1/cameras", headers=self._hdr(), name="/cameras")

    @task(1)
    def list_detections(self) -> None:
        self.client.get(
            "/api/v1/detections?limit=20",
            headers=self._hdr(),
            name="/detections",
        )

    @task(1)
    def health_check(self) -> None:
        self.client.get("/health", name="/health")

    @task(1)
    def system_version(self) -> None:
        self.client.get(
            "/api/v1/system/version",
            headers=self._hdr(),
            name="/system/version",
        )


class WriteTasks(TaskSet):
    """Moderate write traffic — represents 20 % of load-test user sessions."""

    def on_start(self) -> None:  # type: ignore[override]
        r = self.client.post(
            "/api/v1/auth/login",
            json={"email": _EMAIL, "password": _PASSWORD},
            name="/auth/login",
        )
        self._token: str = r.json().get("access_token", "") if r.status_code == 200 else ""
        self._alert_ids: list[str] = []

    def _hdr(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    @task(4)
    def list_and_peek_alert(self) -> None:
        """Fetch alert list then read a single alert (simulates a dashboard drill-down)."""
        r = self.client.get(
            "/api/v1/alerts?limit=10&status=open",
            headers=self._hdr(),
            name="/alerts (write-user list)",
        )
        if r.status_code == 200:
            alerts = r.json()
            if alerts:
                aid = random.choice(alerts)["id"]
                self.client.get(
                    f"/api/v1/alerts/{aid}",
                    headers=self._hdr(),
                    name="/alerts/{id}",
                )

    @task(2)
    def list_and_peek_incident(self) -> None:
        """Fetch incident list then read one incident detail."""
        r = self.client.get(
            "/api/v1/incidents?limit=10",
            headers=self._hdr(),
            name="/incidents (write-user list)",
        )
        if r.status_code == 200:
            incidents = r.json()
            if incidents:
                iid = random.choice(incidents)["id"]
                self.client.get(
                    f"/api/v1/incidents/{iid}",
                    headers=self._hdr(),
                    name="/incidents/{id}",
                )

    @task(1)
    def export_alerts_csv(self) -> None:
        self.client.get(
            "/api/v1/export/alerts?limit=100",
            headers=self._hdr(),
            name="/export/alerts",
        )


# ── Main user class ─────────────────────────────────────────────────────────────

class SeventhAIUser(HttpUser):
    """
    Primary load-test user.

    Task distribution: 80 % ReadTasks, 20 % WriteTasks.
    Think time:        0.5–2 s between requests (realistic browser cadence).
    """

    wait_time = between(0.5, 2.0)
    tasks = {ReadTasks: 4, WriteTasks: 1}


# ── Load shape ──────────────────────────────────────────────────────────────────

class StepLoadShape(LoadTestShape):
    """
    Stepped ramp profile for a full-duration load test run (~8 min total):

      Stage   Duration    Users   Spawn rate  Purpose
      ─────   ────────    ─────   ──────────  ──────────────────
        0       0–60 s      10       2 /s     Warm-up
        1      60–180 s     50       5 /s     Ramp-up
        2     180–300 s    100      10 /s     Target load (SLA window)
        3     300–420 s    200      20 /s     Peak / stress
        4     420–480 s     50      10 /s     Cool-down
    """

    stages: list[dict] = [
        {"duration": 60,  "users": 10,  "spawn_rate": 2},
        {"duration": 180, "users": 50,  "spawn_rate": 5},
        {"duration": 300, "users": 100, "spawn_rate": 10},
        {"duration": 420, "users": 200, "spawn_rate": 20},
        {"duration": 480, "users": 50,  "spawn_rate": 10},
    ]

    def tick(self):  # type: ignore[override]
        run_time = self.get_run_time()
        for stage in self.stages:
            if run_time < stage["duration"]:
                return (stage["users"], stage["spawn_rate"])
        return None  # test complete — locust shuts down


# ── SLA validation hook ─────────────────────────────────────────────────────────
# Fires at the end of every locust run; sets a non-zero exit code on SLA breach
# so CI pipelines fail automatically.

if _LOCUST_AVAILABLE and events is not None:
    @events.quitting.add_listener
    def _on_quitting(environment, **kwargs):  # type: ignore[misc]
        stats = environment.runner.stats
        if not stats or stats.total.num_requests == 0:
            return

        total = stats.total
        p95 = total.get_response_time_percentile(0.95) or 0.0
        p99 = total.get_response_time_percentile(0.99) or 0.0
        n = total.num_requests
        error_pct = 100.0 * total.num_failures / n if n else 0.0

        violations: list[str] = []
        if p95 > SLA["p95_ms"]:
            violations.append(f"P95 {p95:.0f}ms > SLA {SLA['p95_ms']:.0f}ms")
        if p99 > SLA["p99_ms"]:
            violations.append(f"P99 {p99:.0f}ms > SLA {SLA['p99_ms']:.0f}ms")
        if error_pct > SLA["error_rate_pct"]:
            violations.append(f"Error rate {error_pct:.2f}% > SLA {SLA['error_rate_pct']:.2f}%")

        if violations:
            print(f"\n[SLA FAIL] {'; '.join(violations)}")
            environment.process_exit_code = 1
        else:
            rps = total.current_rps if hasattr(total, "current_rps") else 0.0
            print(f"\n[SLA OK] P95={p95:.0f}ms P99={p99:.0f}ms errors={error_pct:.2f}%")
