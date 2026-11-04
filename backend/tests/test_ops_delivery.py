"""Phase 4 of the known limits (4 November 2026): the daily briefing emailed when published, and the operations
reports emailed on a schedule (services/ops_delivery.py, services/ops_documents.py, routers/ops_delivery.py,
migration 0178).

A. A briefing: publishing queues an email for each person on its lists who may read it; whoever may not is left out
   with why; it is sent with its text and a PDF, audited; a mail server that is down leaves it FAILED, to try again;
   a revision is said to be one.
B. A schedule: Admin and Manager set it; at its time each person named gets the report as they could take it out -
   their own sites only, or left out with why - as CSV and PDF, audited; a missed time sends once, for the period
   that was due; another organisation is untouched.
C. The timing of a schedule, where the organisation is.
"""
from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.main import app  # noqa: F401 - imported first, so the first test does not absorb the import
from app.db.session import AsyncSessionLocal
from app.services import ops_delivery
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _client, _run, _sql, _world
from tests.test_investigation_search import _audit

BASE = "/api/v1/ops-delivery"
CONTENT = {"sections": [{"key": "INCIDENTS", "title": "Incidents", "figures": {},
                         "lines": [{"text": "3 incidents opened, 2 resolved.", "as_at": "PERIOD"}]}], "not_read": []}


class Mailbox:
    """A stand-in for the mail server: what was handed to it, or a failure."""

    def __init__(self, fail: Exception | None = None):
        self.sent: list[dict] = []
        self.fail = fail

    async def __call__(self, to, subject, body, attachments):
        if self.fail:
            raise self.fail
        self.sent.append({"to": to, "subject": subject, "body": body,
                          "files": {name: (content, sub) for name, content, _, sub in attachments}})


async def _briefing(w: dict, *, site: str | None = "site_a", revision: int = 1, day: date | None = None) -> uuid.UUID:
    bid = uuid.uuid4()
    on = day or date(2026, 11, 3)
    await _sql("INSERT INTO daily_briefings (id, tenant_id, site_id, briefing_date, revision, state, period_start, "
               "period_end, timezone, content, drafted_by_user_id) VALUES (:i,:t,:s,:d,:r,'DRAFT',:a,:b,'Asia/Singapore',"
               "CAST(:c AS jsonb),:u)",
               {"i": bid, "t": w["tenant"], "s": w[site] if site else None, "d": on, "r": revision,
                "a": datetime(2026, 11, 2, 16, tzinfo=timezone.utc), "b": datetime(2026, 11, 3, 16, tzinfo=timezone.utc),
                "c": json.dumps(CONTENT), "u": w["users"][MANAGER]})
    return bid


async def _emails(w: dict) -> list[dict]:
    return await _sql("SELECT e.kind, e.status, e.last_error, e.user_id, e.subject, e.attempts, e.revised, e.rows_sent "
                      "FROM ops_emails e WHERE e.tenant_id = :t ORDER BY e.created_at, e.user_id", {"t": w["tenant"]})


async def _send(mailbox, now: datetime | None = None) -> dict:
    return await ops_delivery.run(AsyncSessionLocal, now=now or datetime.now(timezone.utc), deliver=mailbox)


# ─── A. A briefing ───────────────────────────────────────────────────────────

async def test_a_published_briefing_is_emailed_to_those_on_its_list_who_may_read_it():
    w = await _world()
    u = w["users"]
    async with _client() as c:
        # Site A's list: the operator and the guard by name, and the Manager role. The supervisor is held to site A.
        put = await c.put(f"{BASE}/briefing-recipients", headers=w["h"][MANAGER], json={
            "site_id": str(w["site_a"]), "user_ids": [str(u[OPERATOR]), str(u[GUARD]), str(u[SUPERVISOR])],
            "role_ids": [8]})
        assert put.status_code == 200, put.text
        (shown,) = put.json()["lists"]
        assert {x["name"] for x in shown["users"]} == {"Role 4 User", "Role 5 User", "Role 3 User"}
        for role in (SUPERVISOR, OPERATOR, VIEWER):
            assert (await c.get(f"{BASE}/briefing-recipients", headers=w["h"][role])).status_code == 403, role
        bid = await _briefing(w)
        published = await c.post(f"/api/v1/daily-briefings/{bid}/publish", headers=w["h"][MANAGER])
        assert published.status_code == 200, published.text
    rows = {r["user_id"]: r for r in await _emails(w)}
    assert rows[u[OPERATOR]]["status"] == "PENDING" and rows[u[MANAGER]]["status"] == "PENDING"
    assert rows[u[SUPERVISOR]]["status"] == "PENDING", "held to site A, and this is site A's briefing"
    assert (rows[u[GUARD]]["status"], rows[u[GUARD]]["last_error"]) == ("LEFT_OUT", "Does not hold briefing:read")
    assert rows[u[OPERATOR]]["subject"] == "Daily briefing: Factory A, Tue 03 Nov 2026"

    box = Mailbox()
    counts = await _send(box)
    assert counts["sent"] >= 3
    mine = [m for m in box.sent if m["subject"] == "Daily briefing: Factory A, Tue 03 Nov 2026"]
    assert len(mine) == 3 and all(len(m["to"]) == 1 for m in mine)
    one = mine[0]
    assert "3 incidents opened, 2 resolved." in one["body"] and "INCIDENTS" in one["body"]
    (pdf, sub), = one["files"].values()
    assert sub == "pdf" and pdf.startswith(b"%PDF")
    assert {r["status"] for r in await _emails(w) if r["user_id"] != u[GUARD]} == {"SENT"}
    assert len(await _audit(w, "briefing.email")) == 3


async def test_a_briefing_for_another_site_or_every_site_leaves_out_who_is_not_shown_it_and_a_revision_says_so():
    w = await _world()
    u = w["users"]
    async with _client() as c:
        await c.put(f"{BASE}/briefing-recipients", headers=w["h"][ADMIN],
                    json={"site_id": None, "user_ids": [str(u[SUPERVISOR]), str(u[ADMIN])], "role_ids": []})
        b_site = await _briefing(w, site="site_b")
        assert (await c.post(f"/api/v1/daily-briefings/{b_site}/publish", headers=w["h"][ADMIN])).status_code == 200
        everyone = await _briefing(w, site=None, day=date(2026, 11, 2))
        assert (await c.post(f"/api/v1/daily-briefings/{everyone}/publish", headers=w["h"][ADMIN])).status_code == 200
        again = await _briefing(w, site="site_b", revision=2)
        assert (await c.post(f"/api/v1/daily-briefings/{again}/publish", headers=w["h"][ADMIN])).status_code == 200
    rows = await _sql("SELECT briefing_id, user_id, status, last_error, revised, subject FROM ops_emails WHERE tenant_id = :t",
                      {"t": w["tenant"]})
    by = {(r["briefing_id"], r["user_id"]): r for r in rows}
    assert by[(b_site, u[SUPERVISOR])]["last_error"] == "Not shown Factory B"
    assert by[(everyone, u[SUPERVISOR])]["last_error"] == "Held to particular sites, and this briefing is for every site"
    assert by[(b_site, u[ADMIN])]["status"] == "PENDING" and by[(everyone, u[ADMIN])]["status"] == "PENDING"
    assert by[(again, u[ADMIN])]["revised"] is True and by[(again, u[ADMIN])]["subject"].endswith("(revised)")
    async with _client() as c:
        status = await c.get(f"{BASE}/briefings/{b_site}/emails", headers=w["h"][MANAGER])
        assert status.status_code == 200 and {e["status"] for e in status.json()["emails"]} == {"PENDING", "LEFT_OUT"}


async def test_a_mail_server_that_is_down_leaves_the_email_failed_to_try_again():
    w = await _world()
    async with _client() as c:
        await c.put(f"{BASE}/briefing-recipients", headers=w["h"][ADMIN],
                    json={"site_id": None, "user_ids": [str(w["users"][ADMIN])], "role_ids": []})
        bid = await _briefing(w)
        assert (await c.post(f"/api/v1/daily-briefings/{bid}/publish", headers=w["h"][ADMIN])).status_code == 200
    await _send(Mailbox(fail=OSError("connection refused")))
    (row,) = await _sql("SELECT status, attempts, last_error, scheduled_at FROM ops_emails WHERE briefing_id = :b", {"b": bid})
    assert (row["status"], row["attempts"], row["last_error"]) == ("FAILED", 1, "The mail server could not be reached.")
    assert row["scheduled_at"] > datetime.now(timezone.utc), "tried again later, not at once"
    box = Mailbox()
    await _send(box, now=row["scheduled_at"] + timedelta(seconds=1))
    assert [m["subject"] for m in box.sent if "Factory A" in m["subject"]] == ["Daily briefing: Factory A, Tue 03 Nov 2026"]


# ─── B. A schedule ───────────────────────────────────────────────────────────

async def _packages(w: dict, made: datetime) -> None:
    for site, n in (("site_a", 1), ("site_b", 2)):
        await _run([("INSERT INTO evidence_packages (tenant_id, site_id, package_number, title, purpose, created_at, "
                     "created_by_user_id) VALUES (:t,:s,:n,'Gate','For the insurer.',:at,:u)",
                     {"t": w["tenant"], "s": w[site], "n": f"EVP-{site[-1].upper()}-{n}", "at": made,
                      "u": w["users"][ADMIN]})])


async def test_a_scheduled_report_goes_to_each_person_as_they_could_take_it_out():
    w, other = await _world(), await _world()
    u = w["users"]
    async with _client() as c:
        body = {"report_key": "evidence", "frequency": "DAILY", "hour": 7,
                "user_ids": [str(u[SUPERVISOR]), str(u[ADMIN]), str(u[OPERATOR])], "role_ids": []}
        assert (await c.post(f"{BASE}/schedules", headers=w["h"][SUPERVISOR], json=body)).status_code == 403
        for bad in ({**body, "report_key": "nothing"}, {**body, "frequency": "WEEKLY"}, {**body, "user_ids": []},
                    {**body, "hour": 24}):
            assert (await c.post(f"{BASE}/schedules", headers=w["h"][MANAGER], json=bad)).status_code in (409, 422), bad
        made = await c.post(f"{BASE}/schedules", headers=w["h"][MANAGER], json=body)
        assert made.status_code == 201, made.text
        sid = made.json()["id"]
        assert made.json()["report_title"] == "Evidence packages" and made.json()["next_run_at"]
    # It falls due at 07:00 on 4 Nov in Singapore: the period is 3 Nov, there.
    due = datetime(2026, 11, 3, 23, 0, tzinfo=timezone.utc)
    await _sql("UPDATE ops_report_schedules SET next_run_at = :d WHERE id = :s", {"d": due, "s": sid})
    await _packages(w, datetime(2026, 11, 3, 4, tzinfo=timezone.utc))
    await _packages(other, datetime(2026, 11, 3, 4, tzinfo=timezone.utc))
    box = Mailbox()
    await _send(box, now=due + timedelta(minutes=2))
    rows = {r["user_id"]: r for r in await _emails(w)}
    assert rows[u[OPERATOR]]["last_error"] == "Does not hold opsreport:export"
    assert rows[u[ADMIN]]["status"] == "SENT" and rows[u[ADMIN]]["rows_sent"] == 2
    assert rows[u[SUPERVISOR]]["status"] == "SENT" and rows[u[SUPERVISOR]]["rows_sent"] == 1, "site A only"
    subjects = {m["subject"] for m in box.sent if "Evidence" in m["subject"]}
    assert subjects == {"Evidence packages: every site you are shown, Tue 03 Nov 2026"}
    sup = next(m for m in box.sent if m["body"].startswith("Evidence packages") and "1 row." in m["body"])
    names = sorted(sup["files"])
    assert names == ["operations-evidence-20261103.csv", "operations-evidence-20261103.pdf"]
    table = list(csv.reader(io.StringIO(sup["files"][names[0]][0].decode("utf-8-sig"))))
    assert table[0][0] == "Package" and [r[0] for r in table[1:]] == ["EVP-A-1"]
    assert sup["files"][names[1]][0].startswith(b"%PDF")
    audits = await _audit(w, "report.email")
    assert len(audits) == 2 and {a["detail"]["rows"] for a in audits} == {1, 2}
    # Moved on to its next time; another pass sends nothing more. The other organisation has no schedule and no email.
    (s,) = await _sql("SELECT next_run_at FROM ops_report_schedules WHERE id = :s", {"s": sid})
    assert s["next_run_at"] > due + timedelta(minutes=2)
    before = len(box.sent)
    await _send(box, now=due + timedelta(minutes=10))
    assert len([m for m in box.sent[before:] if "Evidence" in m["subject"]]) == 0
    assert await _emails(other) == []


async def test_a_missed_time_sends_once_for_the_period_that_was_due_and_a_paused_schedule_sends_nothing():
    w = await _world()
    async with _client() as c:
        made = await c.post(f"{BASE}/schedules", headers=w["h"][ADMIN], json={
            "report_key": "evidence", "frequency": "DAILY", "hour": 7, "user_ids": [str(w["users"][ADMIN])],
            "role_ids": []})
        sid = made.json()["id"]
        paused = await c.patch(f"{BASE}/schedules/{sid}", headers=w["h"][ADMIN], json={"is_active": False})
        assert paused.status_code == 200 and paused.json()["is_active"] is False
    due = datetime(2026, 11, 1, 23, 0, tzinfo=timezone.utc)
    await _sql("UPDATE ops_report_schedules SET next_run_at = :d WHERE id = :s", {"d": due, "s": sid})
    await _send(Mailbox(), now=due + timedelta(days=3))
    assert await _emails(w) == [], "paused: nothing"
    async with _client() as c:
        assert (await c.patch(f"{BASE}/schedules/{sid}", headers=w["h"][ADMIN], json={"is_active": True})).status_code == 200
    await _sql("UPDATE ops_report_schedules SET next_run_at = :d WHERE id = :s", {"d": due, "s": sid})
    box = Mailbox()
    await _send(box, now=due + timedelta(days=3))
    rows = await _sql("SELECT period_start, period_end FROM ops_emails WHERE schedule_id = :s", {"s": sid})
    assert len(rows) == 1, "three times missed, one email"
    # Due at 07:00 on 2 Nov in Singapore: the day it reports is 1 Nov there, from 31 Oct 16:00 UTC.
    assert rows[0]["period_start"] == datetime(2026, 10, 31, 16, tzinfo=timezone.utc)
    async with _client() as c:
        gone = await c.post(f"{BASE}/schedules/{sid}/remove", headers=w["h"][ADMIN])
        assert gone.status_code == 200
        assert (await c.get(f"{BASE}/schedules", headers=w["h"][ADMIN])).json()["schedules"] == []
        sent = await c.get(f"{BASE}/schedules/{sid}/emails", headers=w["h"][ADMIN])
        assert sent.status_code == 200 and len(sent.json()["emails"]) == 1, "what it sent is kept"


# ─── C. Timing ───────────────────────────────────────────────────────────────

def test_a_schedule_falls_due_at_its_hour_where_the_organisation_is():
    zone = "Asia/Singapore"
    at = datetime(2026, 11, 4, 1, 0, tzinfo=timezone.utc)  # Wed 4 Nov 09:00 in Singapore
    daily = {"frequency": "DAILY", "hour": 7, "weekday": None, "month_day": None}
    assert ops_delivery.next_after(daily, at, zone) == datetime(2026, 11, 4, 23, 0, tzinfo=timezone.utc)
    assert ops_delivery.next_after({**daily, "hour": 10}, at, zone) == datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)
    weekly = {"frequency": "WEEKLY", "hour": 7, "weekday": 0, "month_day": None}
    assert ops_delivery.next_after(weekly, at, zone) == datetime(2026, 11, 8, 23, 0, tzinfo=timezone.utc)  # Mon 9 Nov
    monthly = {"frequency": "MONTHLY", "hour": 7, "weekday": None, "month_day": 1}
    assert ops_delivery.next_after(monthly, at, zone) == datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc)  # 1 Dec
    dec = datetime(2026, 12, 15, 1, 0, tzinfo=timezone.utc)
    assert ops_delivery.next_after(monthly, dec, zone) == datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)  # 1 Jan
    due = datetime(2026, 11, 8, 23, 0, tzinfo=timezone.utc)
    assert ops_delivery.period_for(daily, due, zone) == (datetime(2026, 11, 7, 16, tzinfo=timezone.utc),
                                                         datetime(2026, 11, 8, 16, tzinfo=timezone.utc))
    assert ops_delivery.period_for(weekly, due, zone) == (datetime(2026, 11, 1, 16, tzinfo=timezone.utc),
                                                          datetime(2026, 11, 8, 16, tzinfo=timezone.utc))
    assert ops_delivery.period_for(monthly, datetime(2026, 11, 30, 23, tzinfo=timezone.utc), zone) == (
        datetime(2026, 10, 31, 16, tzinfo=timezone.utc), datetime(2026, 11, 30, 16, tzinfo=timezone.utc))
