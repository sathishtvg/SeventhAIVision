"""The response clocks, and who is told when one runs out.

  A — The rules, with nothing running: the three clocks, and when a policy's step falls due
  B — Switching the clocks on: off by default, and nothing older is judged
  C — The pass, as the application's role: a breach is recorded once and told
  D — Which clock, and when it counts
  E — Escalation policies: who else is told, and that they may see the incident
  F — Writing a policy
  G — Reading what was told

The pass runs as the scheduler runs it: `response_sla.run` on the application's
own sessions, as svc_app with RLS enforced, one organisation at a time.

The claims, each with tests: the clocks are off until an organisation switches
them on and never reach back; a breach and a step are each recorded once; whoever
is told may see the incident; and nothing here reassigns, re-dispatches, closes
or raises the severity of anything.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

# Module level on purpose: app.main pulls the ML stack.
from app.main import app
from app.core.config_keys import SETTING_VALIDATORS
from app.db.session import AsyncSessionLocal
from app.dependencies.auth import TokenPayload, get_token_payload
from app.notifications import dispatch as rules
from app.services import response_notify as notify
from app.services import response_sla as sla
from tests.test_drone_api import ADMIN, GUARD, MANAGER, OPERATOR, SUPERVISOR, VIEWER, _auth, _client, _run, _sql, _world
from tests.test_incident_responses import BASE, FakeRedis, _dispatch, _guard, _incident, _scene, told  # noqa: F401
from tests.test_investigation_search import _audit

T0 = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
TIMES = {"ack_within_seconds": 120, "dispatch_within_seconds": 300, "resolve_within_seconds": 1800}


async def _times(w: dict, severity: str = "high", *, ack: int = 120, dispatch: int = 300, resolve: int = 1800,
                 tell=None) -> None:
    await _sql("INSERT INTO sla_configs (tenant_id, severity, ack_within_seconds, dispatch_within_seconds, "
               "resolve_within_seconds, escalation_user_id) VALUES (:t,:s,:a,:d,:r,:u)",
               {"t": w["tenant"], "s": severity, "a": ack, "d": dispatch, "r": resolve, "u": tell})


async def _switch_on(w: dict, *, ago: timedelta = timedelta(hours=1)) -> datetime:
    """The clocks, switched on some while ago."""
    since = datetime.now(timezone.utc) - ago
    await _sql("INSERT INTO tenant_settings (tenant_id, setting_key, setting_value, updated_by_user_id, updated_at) "
               "VALUES (:t, 'response.sla_enabled', 'true'::jsonb, :u, :at)",
               {"t": w["tenant"], "u": w["users"][ADMIN], "at": since})
    return since


async def _policy(w: dict, name: str, trigger: str = "NOT_ACKNOWLEDGED", after: int = 60, *, role: int | None = None,
                  person=None, site: str | None = None, severity: str | None = None, active: bool = True) -> uuid.UUID:
    pid = uuid.uuid4()
    await _sql("INSERT INTO escalation_policies (id, tenant_id, name, site_id, severity, trigger, after_seconds, "
               "notify_role_id, notify_user_id, is_active) VALUES (:i,:t,:n,:s,:sev,:tr,:a,:r,:p,:on)",
               {"i": pid, "t": w["tenant"], "n": name, "s": w[site] if site else None, "sev": severity, "tr": trigger,
                "a": after, "r": role, "p": person, "on": active})
    return pid


async def _told(w: dict) -> list[dict]:
    return [dict(r) for r in await _sql(
        "SELECT e.incident_id, e.kind, e.clock, e.policy_name, e.sending_at, e.due_at, e.notify_role_id, "
        "e.notify_user_id, e.recipients, ev.escalation_type, ev.escalated_to_user_id, ev.notification_sent "
        "FROM incident_escalations e LEFT JOIN escalation_events ev ON ev.id = e.escalation_event_id "
        "WHERE e.tenant_id = :t ORDER BY e.created_at, e.clock", {"t": w["tenant"]})]


async def _pass(redis=None, now: datetime | None = None) -> dict:
    """One pass, as the scheduler makes it — and as the application's role."""
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(text(
            "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar()
    return await sla.run(AsyncSessionLocal, redis, now)


# ─── A. The rules, with nothing running ──────────────────────────────────────

def _i(**over) -> dict:
    return {"id": uuid.uuid4(), "severity": "high", "site_id": uuid.uuid4(), "created_at": T0, "acknowledged_at": None,
            "dispatched_at": None, "sla_deadline_at": None, "guard_arrived_at": None, "resolved_at": None, **over}


def test_three_clocks_each_with_a_start_a_deadline_and_whether_it_ran_out():
    minute = timedelta(minutes=1)
    fresh = sla.clocks(_i(), TIMES, T0 + minute)
    assert fresh["ACKNOWLEDGE"] == {"started_at": T0, "due_at": T0 + 2 * minute, "met_at": None, "breached": False,
                                    "running": True, "seconds_left": 60}
    assert fresh["RESOLVE"]["due_at"] == T0 + 30 * minute and fresh["RESOLVE"]["seconds_left"] == 29 * 60
    assert fresh["ARRIVAL"] == {"started_at": None, "due_at": None, "met_at": None, "breached": False,
                                "running": False, "seconds_left": None}, "nobody has been sent: there is no clock"

    late = sla.clocks(_i(), TIMES, T0 + 3 * minute)["ACKNOWLEDGE"]
    assert late["breached"] and late["running"] and late["seconds_left"] == -60
    in_time = sla.clocks(_i(acknowledged_at=T0 + minute), TIMES, T0 + 10 * minute)["ACKNOWLEDGE"]
    assert not in_time["breached"] and not in_time["running"] and in_time["seconds_left"] is None
    met_late = sla.clocks(_i(acknowledged_at=T0 + 5 * minute), TIMES, T0 + 10 * minute)["ACKNOWLEDGE"]
    assert met_late["breached"] and not met_late["running"], "met, but after it had run out"
    on_the_dot = sla.clocks(_i(acknowledged_at=T0 + 2 * minute), TIMES, T0 + 10 * minute)["ACKNOWLEDGE"]
    assert not on_the_dot["breached"]

    sent = _i(dispatched_at=T0 + minute, sla_deadline_at=T0 + 6 * minute)
    assert sla.clocks(sent, TIMES, T0 + 4 * minute)["ARRIVAL"]["seconds_left"] == 120
    assert sla.clocks(sent, TIMES, T0 + 7 * minute)["ARRIVAL"]["breached"]
    assert not sla.clocks({**sent, "guard_arrived_at": T0 + 5 * minute}, TIMES, T0 + 9 * minute)["ARRIVAL"]["breached"]
    # A guard who arrived, and was then stood down: no sending, so no arrival clock, whatever was stamped before.
    assert sla.clocks(_i(guard_arrived_at=T0 + minute), TIMES, T0 + 9 * minute)["ARRIVAL"]["met_at"] is None


def test_with_no_times_set_for_a_severity_there_is_nothing_to_be_late_for():
    long_ago = sla.clocks(_i(dispatched_at=T0, sla_deadline_at=T0), None, T0 + timedelta(days=30))
    assert set(long_ago) == set(sla.CLOCKS)
    assert not any(c["breached"] or c["running"] or c["due_at"] for c in long_ago.values())


def test_when_a_policys_step_falls_due():
    minute = timedelta(minutes=1)

    def p(trigger, after=300, **over):
        return {"id": uuid.uuid4(), "name": "P", "site_id": None, "severity": None, "trigger": trigger,
                "after_seconds": after, **over}

    assert sla.step_due_at(_i(), p("NOT_ACKNOWLEDGED")) == T0 + 5 * minute
    assert sla.step_due_at(_i(acknowledged_at=T0 + minute), p("NOT_ACKNOWLEDGED")) is None
    assert sla.step_due_at(_i(), p("NOT_RESOLVED", 3600)) == T0 + 60 * minute
    assert sla.step_due_at(_i(resolved_at=T0 + minute), p("NOT_RESOLVED")) is None
    assert sla.step_due_at(_i(), p("NOT_ARRIVED")) is None, "nobody has been sent"
    sent = _i(dispatched_at=T0 + 10 * minute)
    assert sla.step_due_at(sent, p("NOT_ARRIVED")) == T0 + 15 * minute, "from the sending, not from the opening"
    assert sla.step_due_at({**sent, "guard_arrived_at": T0 + 12 * minute}, p("NOT_ARRIVED")) is None

    here = _i()
    for_here, elsewhere = p("NOT_ACKNOWLEDGED", site_id=here["site_id"]), p("NOT_ACKNOWLEDGED", site_id=uuid.uuid4())
    graver, this_grave = p("NOT_ACKNOWLEDGED", severity="critical"), p("NOT_ACKNOWLEDGED", severity="high")
    assert [sla.applies(x, here) for x in (for_here, elsewhere, graver, this_grave)] == [True, False, False, True]
    later = p("NOT_ACKNOWLEDGED", 600)
    due = sla.due_steps(here, [for_here, elsewhere, graver, this_grave, later], T0 + 6 * minute)
    assert [x["id"] for x, _ in due] == [for_here["id"], this_grave["id"]], "not yet the ten-minute one"
    assert all(at == T0 + 5 * minute for _, at in due)
    assert sla.due_steps(here, [later], T0 + 10 * minute)[0][1] == T0 + 10 * minute, "due on the dot"


def test_the_setting_is_one_the_settings_screen_knows():
    check = SETTING_VALIDATORS[sla.ENABLED_KEY]
    check(True), check(False)
    for not_a_switch in ("true", 1, None, {}):
        with pytest.raises(ValueError):
            check(not_a_switch)
    assert set(sla.TRIGGER_CLOCK) == set(sla.TRIGGERS) and set(sla.TRIGGER_CLOCK.values()) == set(sla.CLOCKS)
    assert 1 not in sla.NOTIFY_ROLES and 7 not in sla.NOTIFY_ROLES, "never the platform owner, never a client"
    assert all(len(kind) <= 30 for kind in (*sla.BREACH_TYPE.values(), sla.STEP_TYPE)), "the old log's column is 30 wide"


# ─── B. Switching the clocks on ──────────────────────────────────────────────

async def test_the_clocks_are_off_until_somebody_switches_them_on(told):
    w = await _world()
    await _times(w, tell=w["users"][MANAGER])
    await _policy(w, "Tell the supervisors", role=3)
    s = await _scene(w, minutes_ago=10)          # long past its two minutes
    async with _client() as c:
        settings = (await c.get(f"{BASE}/settings", headers=w["h"][VIEWER])).json()
        desk = (await c.get(f"{BASE}/desk", headers=w["h"][OPERATOR])).json()
    await _pass(told)
    assert settings["sla_enabled"] is False and settings["sla_since"] is None and settings["can_manage"] is False
    assert desk["sla_enabled"] is False
    (row,) = desk["items"]
    assert row["late"] == ["ACKNOWLEDGE"] and row["judged"] is False, "shown as it stands, and judged by nobody"
    assert await _told(w) == [] and told.published == [] and told.pushes == []
    assert await _sql("SELECT 1 FROM escalation_events WHERE tenant_id = :t", {"t": w["tenant"]}) == []
    (incident,) = await _sql("SELECT sla_breached, escalated_at FROM incidents WHERE id = :i", {"i": s["incident"]})
    assert incident["sla_breached"] is False and incident["escalated_at"] is None


async def test_switching_them_on_is_one_persons_act_and_judges_nothing_older(told):
    w = await _world()
    await _times(w)
    older = await _scene(w, tag="Older", minutes_ago=30)
    async with _client() as c:
        url = f"{BASE}/settings"
        for who, why in ((OPERATOR, "Missing permission: sla:manage"), (VIEWER, "Missing permission: sla:manage"),
                         (GUARD, "Missing permission: response:read")):
            r = await c.put(url, headers=w["h"][who], json={"sla_enabled": True})
            assert r.status_code == 403 and r.json()["detail"] == why, who
        held = await c.put(url, headers=w["h"][SUPERVISOR], json={"sla_enabled": True})
        assert held.status_code == 403 and "whole organisation" in held.json()["detail"]
        assert (await c.put(url, headers=w["h"][ADMIN], json={"sla_enabled": "yes"})).status_code == 422
        assert (await c.put(url, headers=w["h"][ADMIN], json={"sla_enabled": True, "x": 1})).status_code == 422
        off = await c.put(url, headers=w["h"][ADMIN], json={"sla_enabled": False})
        assert off.json() == {"sla_enabled": False, "sla_since": None, "changed": False}, "it was off already"
        on = await c.put(url, headers=w["h"][MANAGER], json={"sla_enabled": True})
        assert on.status_code == 200 and on.json()["sla_enabled"] and on.json()["changed"], on.text
        again = await c.put(url, headers=w["h"][ADMIN], json={"sla_enabled": True})
        assert again.json()["changed"] is False and again.json()["sla_since"] == on.json()["sla_since"], \
            "saying what is already so does not move the moment they judge from"
        settings = (await c.get(url, headers=w["h"][OPERATOR])).json()
    assert settings["sla_enabled"] and settings["sla_since"] == on.json()["sla_since"]
    assert [t["severity"] for t in settings["times"]] == ["critical", "high", "medium", "low", "info"]
    high = settings["times"][1]
    assert high["set"] and high["ack_within_seconds"] == 120 and high["resolve_within_seconds"] == 1800
    assert settings["times"][0] == {"severity": "critical", "set": False}
    assert settings["triggers"] == list(sla.TRIGGERS) and {r["role_id"] for r in settings["notify_roles"]} == {2, 3, 4, 5, 6, 8}
    (entry,) = await _audit(w, "response.sla.enable")
    assert entry["user_id"] == w["users"][MANAGER] and await _audit(w, "response.sla.disable") == []

    newer = await _scene(w, tag="Newer", minutes_ago=0, on_shift=False)
    # Ten minutes on: both are long unacknowledged. Only the one opened after the switch is judged.
    await _pass(told, datetime.now(timezone.utc) + timedelta(minutes=10))
    assert [(t["incident_id"], t["clock"]) for t in await _told(w)] == [(newer["incident"], "ACKNOWLEDGE")]
    flags = {r["title"]: r["sla_breached"] for r in await _sql(
        "SELECT title, sla_breached FROM incidents WHERE tenant_id = :t", {"t": w["tenant"]})}
    assert flags == {"Forced gate Older": False, "Forced gate Newer": True}
    assert older

    async with _client() as c:
        assert (await c.put(url, headers=w["h"][ADMIN], json={"sla_enabled": False})).json()["changed"] is True
        generic = await c.put(f"/api/v1/settings/{sla.ENABLED_KEY}", headers=w["h"][ADMIN], json={"setting_value": "on"})
    assert generic.status_code == 422, "the settings screen knows it is a switch"
    assert len(await _audit(w, "response.sla.disable")) == 1
    third = await _scene(w, tag="Third", minutes_ago=0, on_shift=False)
    await _pass(told, datetime.now(timezone.utc) + timedelta(minutes=20))
    assert third["incident"] not in [t["incident_id"] for t in await _told(w)], "switched off, nothing is judged"


async def test_an_api_key_does_not_switch_the_clocks():
    w = await _world()
    key = TokenPayload(user_id=str(w["users"][ADMIN]), tenant_id=str(w["tenant"]), role_id=ADMIN, via_api_key=True)
    app.dependency_overrides[get_token_payload] = lambda: key
    try:
        async with _client() as c:
            r = await c.put(f"{BASE}/settings", json={"sla_enabled": True})
            assert r.status_code == 403 and "not by an API key" in r.json()["detail"]
            made = await c.post(f"{BASE}/policies", json={"name": "P", "trigger": "NOT_ARRIVED", "after_seconds": 60,
                                                          "notify_role_id": 3})
            assert made.status_code == 403
            assert (await c.get(f"{BASE}/settings")).status_code == 200, "an integration may read what it may read"
    finally:
        app.dependency_overrides.pop(get_token_payload, None)
    assert await _sql("SELECT 1 FROM tenant_settings WHERE tenant_id = :t", {"t": w["tenant"]}) == []


# ─── C. The pass, as the application's role ──────────────────────────────────

async def test_a_breach_is_recorded_once_told_to_the_person_named_and_changes_nothing_else(told):
    w, quiet = await _world(), await _world()
    manager = w["users"][MANAGER]
    await _times(w, tell=manager)
    await _times(quiet, tell=quiet["users"][MANAGER])
    since = await _switch_on(w)
    s = await _scene(w, minutes_ago=5)                 # two minutes to acknowledge; five have gone
    other = await _scene(quiet, minutes_ago=5)         # the same, in an organisation that has not switched on
    told.tokens[f"push_tokens:{w['tenant']}:{manager}"] = {"ExponentPushToken[manager]"}
    before = await _incident(s["incident"])
    first = await _pass(told)
    second = await _pass(told)
    assert first["escalations"] >= 1 and second["escalations"] == 0

    (row,) = await _told(w)
    assert (row["kind"], row["clock"], row["policy_name"], row["sending_at"]) == ("SLA_BREACH", "ACKNOWLEDGE", None, None)
    assert row["due_at"] == pytest.approx(s["now"] - timedelta(minutes=3), abs=timedelta(seconds=2))
    assert row["notify_user_id"] == manager and row["notify_role_id"] is None and row["recipients"] == 1
    # The platform's own escalation log, which nothing wrote to before.
    assert row["escalation_type"] == "sla_acknowledge" and row["escalated_to_user_id"] == manager
    assert row["notification_sent"] is True
    (incident,) = await _sql("SELECT sla_breached, escalated_at, escalated_to_user_id FROM incidents WHERE id = :i",
                             {"i": s["incident"]})
    assert incident["sla_breached"] is True and incident["escalated_at"] and incident["escalated_to_user_id"] == manager
    assert await _incident(s["incident"]) == before, "not reassigned, not dispatched, not closed, not raised"

    (event,) = told.events(sla.BREACH_EVENT)
    assert event["incident_id"] == str(s["incident"]) and event["clock"] == "ACKNOWLEDGE"
    assert event["message"] == "“Forced gate A” was not acknowledged in time."
    assert event["notify_user_ids"] == [str(manager)] and event["site_name"] == "Factory A"
    assert "escalation_event_id" not in event
    assert [channel for channel, _ in told.published] == [f"tenant_events:{w['tenant']}"]
    (push,) = told.pushes
    assert push["tokens"] == ["ExponentPushToken[manager]"] and push["body"] == event["message"]
    assert push["data"] == {"type": sla.BREACH_EVENT, "incident_id": str(s["incident"])}

    assert await _told(quiet) == []
    (theirs,) = await _sql("SELECT sla_breached FROM incidents WHERE id = :i", {"i": other["incident"]})
    assert theirs["sla_breached"] is False
    assert since


async def test_a_telling_that_cannot_go_out_is_marked_as_not_sent_and_the_breach_is_still_recorded():
    w = await _world()
    await _times(w)
    await _switch_on(w)
    await _scene(w, minutes_ago=5)
    await _pass(None)                                  # no channel to the screens, nobody named, no phones
    (row,) = await _told(w)
    assert row["clock"] == "ACKNOWLEDGE" and row["recipients"] == 0 and row["notify_user_id"] is None
    assert row["notification_sent"] is False, "recorded, and honestly marked as told to nobody"
    await _pass(FakeRedis())
    assert len(await _told(w)) == 1, "and it is not told again later as if it were new"


async def test_the_organisations_own_notification_rules_hear_of_a_breach(told, monkeypatch):
    w = await _world()
    await _times(w)
    await _switch_on(w)
    s = await _scene(w, minutes_ago=5)
    channel, other = uuid.uuid4(), uuid.uuid4()
    await _run([
        ("INSERT INTO notification_channels (id, tenant_id, name, channel_type, config) "
         "VALUES (:i,:t,'Control room','webhook','{\"url\": \"https://hooks.example.test/x\"}'::jsonb)",
         {"i": channel, "t": w["tenant"]}),
        ("INSERT INTO notification_channels (id, tenant_id, name, channel_type, config) "
         "VALUES (:i,:t,'Alerts only','webhook','{\"url\": \"https://hooks.example.test/y\"}'::jsonb)",
         {"i": other, "t": w["tenant"]}),
        ("INSERT INTO notification_rules (tenant_id, channel_id, min_severity, trigger_events) "
         "VALUES (:t,:c,'low','[\"incident_sla_breached\"]'::jsonb)", {"t": w["tenant"], "c": channel}),
        # A rule that never asked for this kind of event hears nothing of it.
        ("INSERT INTO notification_rules (tenant_id, channel_id, min_severity, trigger_events) "
         "VALUES (:t,:c,'low','[]'::jsonb)", {"t": w["tenant"], "c": other}),
    ])
    sent = []

    async def hook(config, payload):
        sent.append((config, payload))

    monkeypatch.setitem(rules._PROVIDER_MAP, "webhook", hook)
    await _pass(told)
    ((config, payload),) = sent
    assert config == {"url": "https://hooks.example.test/x"}
    assert payload["event_type"] == sla.BREACH_EVENT and payload["severity"] == "high"
    assert payload["title"] == "Forced gate A" and payload["message"] == "“Forced gate A” was not acknowledged in time."
    assert payload["incident_id"] == str(s["incident"])
    (log,) = await _sql("SELECT channel_id, status, event_type FROM notification_logs WHERE tenant_id = :t",
                        {"t": w["tenant"]})
    assert dict(log) == {"channel_id": channel, "status": "sent", "event_type": sla.BREACH_EVENT}


async def test_the_pass_tells_a_guard_who_has_just_been_sent_and_not_one_sent_hours_ago(told):
    w = await _world()
    fresh = await _scene(w, tag="Fresh")
    stale = await _scene(w, tag="Stale", on_shift=False)
    veteran, _ = await _guard(w, "Veteran Guard")
    for uid, token in ((fresh["guard"], "ExponentPushToken[fresh]"), (veteran, "ExponentPushToken[veteran]")):
        told.tokens[f"push_tokens:{w['tenant']}:{uid}"] = {token}
    async with _client() as c:
        await _dispatch(c, w, fresh["incident"], fresh["guard"])
        await _dispatch(c, w, stale["incident"], veteran)
    await _sql("UPDATE incidents SET dispatched_at = now() - interval '3 hours' WHERE id = :i", {"i": stale["incident"]})
    counts = await _pass(told)
    await _pass(told)
    assert counts["sendings"] >= 2, "both are given their record"
    states = await _sql("SELECT guard_user_id, state FROM incident_responses WHERE tenant_id = :t", {"t": w["tenant"]})
    assert {(r["guard_user_id"], r["state"]) for r in states} == {(fresh["guard"], "SENT"), (veteran, "SENT")}
    assert [p["tokens"] for p in told.pushes] == [["ExponentPushToken[fresh]"]], "once, and only the one just sent"
    assert [e["title"] for e in told.events(notify.SENT_EVENT)] == ["Forced gate Fresh"]


# ─── D. Which clock, and when it counts ──────────────────────────────────────

async def test_acknowledging_in_time_is_any_first_thing_done_with_it(told):
    w = await _world()
    await _times(w, ack=120, resolve=86400)
    await _switch_on(w)
    in_time = await _scene(w, tag="In time", minutes_ago=10)
    too_late = await _scene(w, tag="Too late", minutes_ago=10, on_shift=False)
    sent = await _scene(w, tag="Sent", minutes_ago=10, on_shift=False)
    untouched = await _scene(w, tag="Untouched", minutes_ago=10, on_shift=False)
    t = w["tenant"]
    history = ("INSERT INTO incident_status_history (tenant_id, incident_id, changed_by_user_id, from_status, to_status, "
               "changed_at) VALUES (:t,:i,:u,'open','investigating',:at)")
    await _run([
        (history, {"t": t, "i": in_time["incident"], "u": w["users"][OPERATOR],
                   "at": in_time["now"] - timedelta(minutes=9)}),
        (history, {"t": t, "i": too_late["incident"], "u": w["users"][OPERATOR],
                   "at": too_late["now"] - timedelta(minutes=4)}),
        # Sending a guard is doing something with it.
        ("UPDATE incidents SET dispatched_guard_id = :g, dispatched_at = :at, sla_deadline_at = :due, "
         "status = 'in_progress' WHERE id = :i",
         {"g": in_time["guard"], "at": sent["now"] - timedelta(minutes=9), "due": sent["now"] + timedelta(hours=1),
          "i": sent["incident"]}),
    ])
    await _pass(told)
    late = {r["incident_id"]: r["clock"] for r in await _told(w)}
    assert late == {too_late["incident"]: "ACKNOWLEDGE", untouched["incident"]: "ACKNOWLEDGE"}, \
        "one acknowledged late, one never; the other two were in time"


async def test_the_arrival_clock_runs_from_each_sending_to_the_deadline_the_dispatch_stamped(told):
    w = await _world()
    await _times(w, ack=3600, dispatch=300, resolve=86400)
    await _switch_on(w)
    s = await _scene(w, minutes_ago=30)
    second, second_h = await _guard(w, "Second Guard")
    async with _client() as c:
        first = await _dispatch(c, w, s["incident"], s["guard"])
        stamped = await _incident(s["incident"])
        assert stamped["sla_deadline_at"] - stamped["dispatched_at"] == timedelta(seconds=300), \
            "the existing dispatch stamps the deadline, as it always has"
        await _pass(told)
        assert await _told(w) == [], "five minutes to arrive, and none has passed"
        # Seven minutes on, the guard has not arrived.
        await _pass(told, datetime.now(timezone.utc) + timedelta(minutes=7))
        await _pass(told, datetime.now(timezone.utc) + timedelta(minutes=8))
        (one,) = await _told(w)
        assert (one["kind"], one["clock"], one["escalation_type"]) == ("SLA_BREACH", "ARRIVAL", "sla_arrival")
        assert one["sending_at"] == stamped["dispatched_at"] and one["due_at"] == stamped["sla_deadline_at"]
        # Called off, and somebody else sent: a sending of its own, with a clock of its own.
        assert (await c.post(f"{BASE}/{s['incident']}/stand-down", headers=w["h"][OPERATOR],
                             json={"reason": "Sending somebody nearer"})).status_code == 200
        await _pass(told, datetime.now(timezone.utc) + timedelta(minutes=9))
        assert len(await _told(w)) == 1, "nobody is sent, so no arrival clock is running"
        await _dispatch(c, w, s["incident"], second)
        again = await _incident(s["incident"])
        assert (await c.post(f"{BASE}/{s['incident']}/arrived", headers=second_h)).status_code == 200
        await _pass(told, datetime.now(timezone.utc) + timedelta(minutes=30))
        assert len(await _told(w)) == 1, "the second guard arrived in time"
    assert first and again["dispatched_at"] > stamped["dispatched_at"]
    assert (await _sql("SELECT sla_breached FROM incidents WHERE id = :i", {"i": s["incident"]}))[0]["sla_breached"]


async def test_an_incident_sent_twice_can_be_late_twice():
    w = await _world()
    await _times(w, ack=3600, dispatch=60, resolve=86400)
    await _switch_on(w)
    s = await _scene(w, minutes_ago=30)
    second, _ = await _guard(w, "Second Guard")
    async with _client() as c:
        await _dispatch(c, w, s["incident"], s["guard"])
        await _pass(None, datetime.now(timezone.utc) + timedelta(minutes=5))
        await c.post(f"{BASE}/{s['incident']}/stand-down", headers=w["h"][OPERATOR], json={"reason": "No answer"})
        await _dispatch(c, w, s["incident"], second)
        await _pass(None, datetime.now(timezone.utc) + timedelta(minutes=10))
        await _pass(None, datetime.now(timezone.utc) + timedelta(minutes=11))
    rows = await _told(w)
    assert [r["clock"] for r in rows] == ["ARRIVAL", "ARRIVAL"]
    assert rows[0]["sending_at"] < rows[1]["sending_at"]


async def test_resolved_after_the_time_ran_out_is_a_breach_all_the_same_and_old_ones_are_left_alone():
    w = await _world()
    await _times(w, "low", ack=86400, dispatch=300, resolve=600)
    await _switch_on(w, ago=timedelta(days=5))
    late = await _scene(w, tag="Late", severity="low", minutes_ago=60)
    quick = await _scene(w, tag="Quick", severity="low", minutes_ago=60, on_shift=False)
    ancient = await _scene(w, tag="Ancient", severity="low", minutes_ago=60 * 72, on_shift=False)
    close = "UPDATE incidents SET status = 'resolved', resolved_at = :at WHERE id = :i"
    await _run([
        (close, {"i": late["incident"], "at": late["now"] - timedelta(minutes=20)}),      # after 40 min, with 10 allowed
        (close, {"i": quick["incident"], "at": quick["now"] - timedelta(minutes=55)}),    # after 5
        (close, {"i": ancient["incident"], "at": ancient["now"] - timedelta(hours=70)}),  # late, but days ago
    ])
    await _policy(w, "Tell an admin", "NOT_RESOLVED", 60, role=2)
    await _pass(None)
    rows = await _told(w)
    assert [(r["incident_id"], r["kind"], r["clock"]) for r in rows] == [(late["incident"], "SLA_BREACH", "RESOLVE")], \
        "and no policy step: there is nobody to chase about something that is resolved"


# ─── E. Escalation policies ──────────────────────────────────────────────────

async def test_a_step_tells_the_people_in_a_role_who_may_see_the_incident_and_only_once(told):
    w = await _world()
    await _switch_on(w)                                 # no times are set: a policy needs none
    s = await _scene(w, minutes_ago=5)
    held_to_a = w["users"][SUPERVISOR]                  # the world's supervisor: site A only
    held_to_b, _ = await _guard(w, "Supervisor At B", role=SUPERVISOR)
    anywhere, _ = await _guard(w, "Roving Supervisor", role=SUPERVISOR)
    gone, _ = await _guard(w, "Former Supervisor", role=SUPERVISOR)
    await _run([
        ("INSERT INTO user_sites (user_id, site_id, tenant_id) VALUES (:u,:s,:t)",
         {"u": held_to_b, "s": w["site_b"], "t": w["tenant"]}),
        ("UPDATE users SET is_active = FALSE WHERE id = :u", {"u": gone}),
    ])
    for uid in (held_to_a, held_to_b, gone):
        told.tokens[f"push_tokens:{w['tenant']}:{uid}"] = {f"ExponentPushToken[{uid.hex[:6]}]"}
    await _policy(w, "Unacknowledged after a minute", after=60, role=3)
    await _policy(w, "Unacknowledged after an hour", after=3600, role=2)
    await _policy(w, "Site B only", after=60, role=3, site="site_b")
    await _policy(w, "Critical only", after=60, role=3, severity="critical")
    await _policy(w, "Retired", after=60, role=3, active=False)
    await _policy(w, "Nobody sent, so nobody late to arrive", "NOT_ARRIVED", 60, role=3)
    before = await _incident(s["incident"])
    await _pass(told)
    await _pass(told)

    (row,) = await _told(w)
    assert (row["kind"], row["clock"], row["policy_name"]) == ("POLICY_STEP", "ACKNOWLEDGE", "Unacknowledged after a minute")
    assert row["notify_role_id"] == 3 and row["notify_user_id"] is None and row["recipients"] == 2
    assert row["due_at"] == pytest.approx(s["now"] - timedelta(minutes=4), abs=timedelta(seconds=2))
    assert row["escalation_type"] == "policy_step" and row["notification_sent"] is True
    (event,) = told.events(sla.ESCALATION_EVENT)
    assert set(event["notify_user_ids"]) == {str(held_to_a), str(anywhere)}, "not the one held to site B, not the one who left"
    assert event["message"] == "“Forced gate A” is still not acknowledged after 1 min (Unacknowledged after a minute)."
    assert event["policy"] == "Unacknowledged after a minute" and event["trigger"] == "NOT_ACKNOWLEDGED"
    (push,) = told.pushes
    assert push["tokens"] == [f"ExponentPushToken[{held_to_a.hex[:6]}]"], "the phones of those addressed, and no others"
    (incident,) = await _sql("SELECT sla_breached, escalated_at FROM incidents WHERE id = :i", {"i": s["incident"]})
    assert incident["escalated_at"] and incident["sla_breached"] is False, "a step is not a clock running out"
    assert await _incident(s["incident"]) == before


async def test_a_step_stops_being_due_once_the_thing_it_watches_has_happened():
    w = await _world()
    await _switch_on(w)
    s = await _scene(w, minutes_ago=30)
    await _policy(w, "Unacknowledged", after=600, role=3)
    await _policy(w, "Unresolved", "NOT_RESOLVED", 600, person=w["users"][MANAGER])
    await _policy(w, "Not there", "NOT_ARRIVED", 120, role=4)
    async with _client() as c:
        # Somebody takes it up, through the incident's own endpoint, before the pass.
        assert (await c.put(f"/api/v1/incidents/{s['incident']}/status", headers=w["h"][OPERATOR],
                            json={"status": "investigating"})).status_code == 200
        await _pass(None)
        assert [r["policy_name"] for r in await _told(w)] == ["Unresolved"]
        await _dispatch(c, w, s["incident"], s["guard"])
        await _pass(None, datetime.now(timezone.utc) + timedelta(minutes=1))
        assert len(await _told(w)) == 1, "sent a minute ago: two are allowed"
        await _pass(None, datetime.now(timezone.utc) + timedelta(minutes=3))
    rows = await _told(w)
    assert [(r["policy_name"], r["clock"]) for r in rows] == [("Unresolved", "RESOLVE"), ("Not there", "ARRIVAL")]
    assert rows[0]["notify_user_id"] == w["users"][MANAGER] and rows[0]["recipients"] == 1
    assert rows[1]["sending_at"] is not None and rows[1]["notify_role_id"] == 4


async def test_a_step_addressed_to_somebody_who_may_not_see_the_site_reaches_nobody_and_says_so(told):
    w = await _world()
    await _switch_on(w)
    s = await _scene(w, site="site_b", tag="B", minutes_ago=5, on_shift=False)
    held_to_a = w["users"][SUPERVISOR]
    told.tokens[f"push_tokens:{w['tenant']}:{held_to_a}"] = {"ExponentPushToken[a]"}
    await _policy(w, "Tell the site A supervisor", after=60, person=held_to_a)
    await _pass(told)
    (row,) = await _told(w)
    assert row["notify_user_id"] == held_to_a and row["recipients"] == 0
    assert told.pushes == [], "an incident at a site they may not see is not put on their phone"
    (event,) = told.events(sla.ESCALATION_EVENT)
    assert event["notify_user_ids"] == [] and event["incident_id"] == str(s["incident"])


async def test_one_organisations_policies_do_not_reach_anothers_incidents():
    w, other = await _world(), await _world()
    await _switch_on(w), await _switch_on(other)
    await _policy(w, "Ours", after=60, role=3)
    mine, theirs = await _scene(w, minutes_ago=5), await _scene(other, minutes_ago=5)
    await _pass(None)
    assert [r["incident_id"] for r in await _told(w)] == [mine["incident"]]
    assert await _told(other) == [] and theirs


# ─── F. Writing a policy ─────────────────────────────────────────────────────

async def test_a_policy_is_written_changed_retired_and_restored_and_each_is_on_the_record():
    w = await _world()
    manager = w["users"][MANAGER]
    async with _client() as c:
        r = await c.post(f"{BASE}/policies", headers=w["h"][ADMIN], json={
            "name": "  Unacknowledged criticals  ", "severity": "critical", "trigger": "NOT_ACKNOWLEDGED",
            "after_seconds": 120, "notify_role_id": 3})
        assert r.status_code == 201, r.text
        made = r.json()
        url = f"{BASE}/policies/{made['id']}"
        changed = await c.patch(url, headers=w["h"][MANAGER], json={"after_seconds": 300, "notify_user_id": str(manager)})
        assert changed.status_code == 200, changed.text
        back = await c.patch(url, headers=w["h"][ADMIN], json={"notify_role_id": 4, "site_id": str(w["site_a"])})
        assert (await c.patch(url, headers=w["h"][ADMIN], json={})).status_code == 422
        assert (await c.patch(url, headers=w["h"][ADMIN], json={"trigger": "NOT_ARRIVED"})).status_code == 422, \
            "what a policy watches is what it is"
        assert (await c.post(f"{url}/retire", headers=w["h"][ADMIN])).json() == {"is_active": False}
        assert (await c.post(f"{url}/retire", headers=w["h"][ADMIN])).status_code == 409
        assert (await c.patch(url, headers=w["h"][ADMIN], json={"name": "X"})).status_code == 409
        active = (await c.get(f"{BASE}/policies", headers=w["h"][OPERATOR])).json()
        every = (await c.get(f"{BASE}/policies", headers=w["h"][ADMIN], params={"include_retired": True})).json()
        asked = (await c.get(f"{BASE}/policies", headers=w["h"][OPERATOR], params={"include_retired": True})).json()
        assert (await c.post(f"{url}/restore", headers=w["h"][ADMIN])).json() == {"is_active": True}
        assert (await c.post(f"{url}/restore", headers=w["h"][ADMIN])).status_code == 409
        assert (await c.delete(url, headers=w["h"][ADMIN])).status_code == 405, "a policy is retired, not removed"
    assert made["name"] == "Unacknowledged criticals" and made["site_id"] is None and made["is_active"]
    assert (made["notify_role_id"], made["notify_user_id"]) == (3, None)
    assert (changed.json()["notify_role_id"], changed.json()["notify_user_id"]) == (None, str(manager))
    assert changed.json()["notify_user_name"] == f"Role {MANAGER} User" and changed.json()["after_seconds"] == 300
    assert (back.json()["notify_role_id"], back.json()["notify_user_id"]) == (4, None), "naming a role removes the person"
    assert back.json()["site_name"] == "Factory A"
    assert active == {"items": [], "can_manage": False} and asked["items"] == [], "the retired are shown to who manages them"
    assert [p["name"] for p in every["items"]] == ["Unacknowledged criticals"] and every["can_manage"]
    for action, n in (("create", 1), ("update", 2), ("retire", 1), ("restore", 1)):
        assert len(await _audit(w, f"response.policy.{action}")) == n, action
    (entry,) = await _audit(w, "response.policy.create")
    assert entry["detail"]["trigger"] == "NOT_ACKNOWLEDGED" and entry["detail"]["after_seconds"] == 120


async def test_what_a_policy_has_to_be():
    w, other = await _world(), await _world()
    client, _ = await _guard(w, "A Client", role=7)
    left, _ = await _guard(w, "Left", role=OPERATOR)
    await _sql("UPDATE users SET is_active = FALSE WHERE id = :u", {"u": left})
    good = {"name": "P", "trigger": "NOT_ARRIVED", "after_seconds": 120, "notify_role_id": 3}
    async with _client() as c:
        for change, status, why in (
            ({"notify_role_id": None}, 422, "one of the two"),
            ({"notify_user_id": str(w["users"][MANAGER])}, 422, "one of the two"),
            ({"notify_role_id": 1}, 422, "cannot be addressed to that role"),
            ({"notify_role_id": 7}, 422, "cannot be addressed to that role"),
            ({"notify_role_id": None, "notify_user_id": str(client)}, 422, "cannot be told"),
            ({"notify_role_id": None, "notify_user_id": str(left)}, 422, "cannot be told"),
            ({"notify_role_id": None, "notify_user_id": str(other["users"][ADMIN])}, 422, "cannot be told"),
            ({"notify_role_id": None, "notify_user_id": str(w["users"][SUPERVISOR]), "site_id": str(w["site_b"])},
             422, "may not see this site"),
            ({"trigger": "NOT_HAPPY"}, 422, "Unknown trigger"),
            ({"severity": "dire"}, 422, "Unknown severity"),
            ({"name": "   "}, 422, "has a name"),
            ({"site_id": str(other["site_a"])}, 404, "Site not found"),
            ({"site_id": str(uuid.uuid4())}, 404, "Site not found"),
        ):
            r = await c.post(f"{BASE}/policies", headers=w["h"][ADMIN], json={**good, **change})
            assert r.status_code == status and why in str(r.json()["detail"]), (change, r.text)
        for bounds in ({"after_seconds": 29}, {"after_seconds": 604801}, {"name": ""}, {"extra": 1}):
            assert (await c.post(f"{BASE}/policies", headers=w["h"][ADMIN], json={**good, **bounds})).status_code == 422
        for who in (OPERATOR, VIEWER, GUARD):
            assert (await c.post(f"{BASE}/policies", headers=w["h"][who], json=good)).status_code == 403, who
        assert (await c.get(f"{BASE}/policies", headers=w["h"][GUARD])).status_code == 403
        assert (await c.patch(f"{BASE}/policies/{uuid.uuid4()}", headers=w["h"][ADMIN],
                              json={"name": "X"})).status_code == 404
    assert await _sql("SELECT 1 FROM escalation_policies WHERE tenant_id = :t", {"t": w["tenant"]}) == []


async def test_somebody_held_to_particular_sites_writes_policies_for_those_sites_only():
    w, other = await _world(), await _world()
    everywhere = await _policy(w, "Every site", role=3)
    at_b = await _policy(w, "Site B", role=3, site="site_b")
    good = {"name": "Mine", "trigger": "NOT_ARRIVED", "after_seconds": 120, "notify_role_id": 4}
    async with _client() as c:
        h = w["h"][SUPERVISOR]                           # holds sla:manage, and is held to site A
        nowhere = await c.post(f"{BASE}/policies", headers=h, json=good)
        assert nowhere.status_code == 422 and "names one of them" in nowhere.json()["detail"]
        assert (await c.post(f"{BASE}/policies", headers=h, json={**good, "site_id": str(w["site_b"])})).status_code == 404
        mine = await c.post(f"{BASE}/policies", headers=h, json={**good, "site_id": str(w["site_a"])})
        assert mine.status_code == 201, mine.text
        seen = (await c.get(f"{BASE}/policies", headers=h)).json()["items"]
        assert (await c.patch(f"{BASE}/policies/{at_b}", headers=h, json={"name": "X"})).status_code == 404
        wide = await c.patch(f"{BASE}/policies/{everywhere}", headers=h, json={"name": "X"})
        assert wide.status_code == 403 and "every site" in wide.json()["detail"]
        assert (await c.post(f"{BASE}/policies/{everywhere}/retire", headers=h)).status_code == 403
        assert (await c.patch(f"{BASE}/policies/{mine.json()['id']}", headers=h,
                              json={"site_id": None})).status_code == 422, "nor may they widen their own"
        assert (await c.post(f"{BASE}/policies/{mine.json()['id']}/retire", headers=h)).status_code == 200
        assert (await c.get(f"{BASE}/policies", headers=other["h"][ADMIN])).json()["items"] == []
        assert (await c.patch(f"{BASE}/policies/{everywhere}", headers=other["h"][ADMIN],
                              json={"name": "X"})).status_code == 404
    assert sorted(p["name"] for p in seen) == ["Every site", "Mine"], "their site's, and the ones for every site"


# ─── G. Reading what was told ────────────────────────────────────────────────

async def test_what_was_told_is_read_on_the_desk_on_the_incident_and_in_a_list(told):
    w, other = await _world(), await _world()
    manager = w["users"][MANAGER]
    await _times(w, tell=manager)
    await _switch_on(w)
    a = await _scene(w, minutes_ago=5)
    b = await _scene(w, site="site_b", tag="B", minutes_ago=5, on_shift=False)
    await _policy(w, "Tell the operators", after=60, role=4)
    await _pass(told)
    async with _client() as c:
        desk = (await c.get(f"{BASE}/desk", headers=w["h"][OPERATOR], params={"view": "late"})).json()
        detail = (await c.get(f"{BASE}/{a['incident']}", headers=w["h"][VIEWER])).json()
        listed = (await c.get(f"{BASE}/escalations", headers=w["h"][ADMIN])).json()["items"]
        one = (await c.get(f"{BASE}/escalations", headers=w["h"][ADMIN],
                           params={"incident_id": str(b["incident"])})).json()["items"]
        held = (await c.get(f"{BASE}/escalations", headers=w["h"][SUPERVISOR])).json()["items"]
        assert (await c.get(f"{BASE}/escalations", headers=w["h"][GUARD])).status_code == 403
        assert (await c.get(f"{BASE}/escalations", headers=other["h"][ADMIN])).json()["items"] == []
        assert (await c.get(f"{BASE}/escalations", headers=w["h"][ADMIN], params={"hours": 0})).status_code == 422
    assert desk["sla_enabled"] and desk["counts"]["late"] == 2
    assert {i["title"]: (i["late"], i["judged"], i["escalations"], i["sla_breached"]) for i in desk["items"]} == {
        "Forced gate A": (["ACKNOWLEDGE"], True, 2, True), "Forced gate B": (["ACKNOWLEDGE"], True, 2, True)}
    assert detail["judged"] and detail["sla_enabled"] and detail["clocks"]["ACKNOWLEDGE"]["breached"]
    assert detail["incident"]["sla_breached"] is True and detail["incident"]["escalated_at"]
    told_of_a = {e["kind"]: e for e in detail["escalations"]}
    assert set(told_of_a) == {"SLA_BREACH", "POLICY_STEP"}
    assert told_of_a["SLA_BREACH"]["notify_user_name"] == f"Role {MANAGER} User" and told_of_a["SLA_BREACH"]["recipients"] == 1
    assert told_of_a["POLICY_STEP"]["notify_role_name"] == "Operators" and told_of_a["POLICY_STEP"]["notification_sent"]
    assert told_of_a["POLICY_STEP"]["policy_name"] == "Tell the operators"
    assert len(listed) == 4 and len(one) == 2 and {e["incident_title"] for e in one} == {"Forced gate B"}
    assert {e["site_name"] for e in held} == {"Factory A"} and len(held) == 2, "held to site A"


async def test_the_scheduler_makes_this_pass_every_minute():
    import inspect

    from app import scheduler_main
    source = inspect.getsource(scheduler_main.main)
    assert "response_sla.run(AsyncSessionLocal, redis)" in source
    before_sleep = source[:source.index("await asyncio.sleep(60)")]
    assert "response_sla.run" in before_sleep and "if now - last_" not in before_sleep[before_sleep.index("response_sla"):
                                                                                    before_sleep.index("response_sla") + 200]
    assert _auth and GUARD
