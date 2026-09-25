"""Drone patrol, phase 5: the pure parts — the gateway's local store, the
credential format, and the recording-policy rules. No database, no network.
"""
from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.drone_edge.store import EdgeStore
from app.services.drone_edge_sync import in_window, media_wanted_centrally
from app.services.drone_edge_wire import MAX_SAMPLES_PER_BATCH, SyncBatch, parse_key

T0 = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)
SNAP = {"route": {}, "waypoints": []}


def _store(tmp_path) -> EdgeStore:
    return EdgeStore(tmp_path / "edge.sqlite3")


def _claim(store: EdgeStore, sid: str = "s-1", drone: str = "d-1") -> None:
    store.add_session({"id": sid, "drone_id": drone, "config_snapshot": SNAP}, T0)


def _update(n_samples: int = 2, outcome: str | None = None) -> dict:
    return {"phase": "LANDED" if outcome else "ACTIVE", "outcome": outcome, "provider_state": {"n": n_samples},
            "samples": [{"i": i} for i in range(n_samples)], "events": []}


# ─── The local store ─────────────────────────────────────────────────────────

def test_updates_are_numbered_in_order_and_queued_with_the_session_state(tmp_path):
    s = _store(tmp_path)
    _claim(s)
    assert [s.record_update("s-1", _update(), at=T0 + timedelta(seconds=i)) for i in range(3)] == [1, 2, 3]
    local = s.session("s-1")
    assert local.seq == 3 and local.started and local.provider_state == {"n": 2}
    _, items = s.open_batch(max_items=10, max_samples=100)
    assert [i.payload["seq"] for i in items] == [1, 2, 3]
    assert items[0].payload["session_id"] == "s-1" and items[0].samples == 2


def test_the_outcome_ends_the_flight_here(tmp_path):
    s = _store(tmp_path)
    _claim(s)
    s.record_update("s-1", _update(outcome="COMPLETED"), at=T0)
    assert s.live_sessions() == [] and s.session("s-1").outcome == "COMPLETED"


def test_everything_survives_a_restart(tmp_path):
    s = _store(tmp_path)
    _claim(s)
    s.record_update("s-1", _update(), at=T0)
    s.set_meta("assignment", {"drones": [{"id": "d-1"}]})
    s.close()
    again = _store(tmp_path)
    assert again.session("s-1").seq == 1 and again.depth()[0] == 1
    assert again.get_meta("assignment") == {"drones": [{"id": "d-1"}]}


def test_an_unanswered_batch_is_offered_again_unchanged(tmp_path):
    s = _store(tmp_path)
    _claim(s)
    s.record_update("s-1", _update(), at=T0)
    first_id, first = s.open_batch(max_items=10, max_samples=100)
    s.record_update("s-1", _update(), at=T0 + timedelta(seconds=2))       # more arrives meanwhile
    again_id, again = s.open_batch(max_items=10, max_samples=100)
    assert again_id == first_id and [i.id for i in again] == [i.id for i in first]
    s.close_batch(again, {}, T0)
    next_id, rest = s.open_batch(max_items=10, max_samples=100)
    assert next_id != first_id and [i.payload["seq"] for i in rest] == [2]


def test_a_batch_stops_before_the_sample_limit_but_always_carries_something(tmp_path):
    s = _store(tmp_path)
    _claim(s)
    for i in range(3):
        s.record_update("s-1", _update(n_samples=50), at=T0 + timedelta(seconds=i))
    _, items = s.open_batch(max_items=10, max_samples=120)
    assert len(items) == 2
    s.drop_open_batch()
    _, one = s.open_batch(max_items=10, max_samples=10)
    assert len(one) == 1, "an item bigger than the limit still goes, alone"


def test_refused_items_are_kept_aside_with_the_reason(tmp_path):
    s = _store(tmp_path)
    s.put_event({"client_ref": "e-1"}, T0)
    s.put_event({"client_ref": "e-2"}, T0)
    _, items = s.open_batch(max_items=10, max_samples=10)
    s.close_batch(items, {("event", "e-2"): "Unknown drone."}, T0)
    assert s.depth()[0] == 0
    assert [(r["ref"], r["reason"]) for r in s.rejected()] == [("e-2", "Unknown drone.")]


def test_only_the_latest_health_per_drone_is_kept(tmp_path):
    s = _store(tmp_path)
    s.put_health("d-1", {"battery_level": 90}, T0)
    s.put_health("d-1", {"battery_level": 88}, T0 + timedelta(seconds=15))
    _, items = s.open_batch(max_items=10, max_samples=10)
    assert [i.payload for i in items] == [{"battery_level": 88}]


def test_a_command_is_received_once_and_only_for_a_flight_flown_here(tmp_path):
    s = _store(tmp_path)
    _claim(s)
    cmds = [{"id": "c-1", "session_id": "s-1", "command": "ABORT"},
            {"id": "c-2", "session_id": "not-mine", "command": "PAUSE"}]
    assert s.add_commands(cmds, T0) == 1
    assert s.add_commands(cmds, T0) == 0
    s.finish_command("c-1", "DONE", "Aborted.", T0)
    assert s.open_commands("s-1") == []
    _, items = s.open_batch(max_items=10, max_samples=10)
    assert items[-1].kind == "command" and items[-1].payload["status"] == "DONE"


def test_files_are_pruned_only_once_the_centre_has_them_or_never_wanted_them(tmp_path):
    s = _store(tmp_path)
    for ref in ("uploaded", "unwanted", "waiting"):
        s.put_media({"client_ref": ref, "media_kind": "SNAPSHOT", "checksum_sha256": "0" * 64, "size_bytes": 1,
                     "captured_at": T0.isoformat()}, f"/tmp/{ref}.jpg", T0)
    s.mark_media(["uploaded"], uploaded_at=T0)
    s.mark_media(["unwanted"], wanted=False)
    s.mark_media(["waiting"], wanted=True)
    assert {m["client_ref"] for m in s.prunable_media(T0 + timedelta(days=1))} == {"uploaded", "unwanted"}


def test_a_finished_flight_is_forgotten_only_after_all_of_it_was_delivered(tmp_path):
    s = _store(tmp_path)
    _claim(s)
    s.record_update("s-1", _update(outcome="COMPLETED"), at=T0)
    s.prune_sessions(T0 + timedelta(days=2))
    assert s.session("s-1") is not None, "its last update is still waiting to be sent"
    _, items = s.open_batch(max_items=10, max_samples=10)
    s.close_batch(items, {}, T0)
    s.prune_sessions(T0 + timedelta(days=2))
    assert s.session("s-1") is None


# ─── The credential, the batch, the policy ───────────────────────────────────

def test_a_gateway_credential_names_its_tenant_and_nothing_else_parses():
    tenant = uuid.uuid4()
    parsed = parse_key(f"deg.{tenant}.secret-part")
    assert parsed is not None and parsed[0] == str(tenant) and len(parsed[1]) == 64
    for bad in (None, "", "deg", "deg.x.y", f"xyz.{tenant}.s", f"deg.{tenant}.", "deg." + "a" * 300):
        assert parse_key(bad) is None, bad


def test_a_batch_is_bounded():
    sample = {"recorded_at": T0.isoformat(), "latitude": 1, "longitude": 2, "altitude_m": 3, "speed_mps": 1,
              "battery_pct": 50, "mission_state": "ACTIVE"}
    per = 600
    updates = [{"session_id": str(uuid.uuid4()), "seq": 1, "at": T0.isoformat(),
                "update": {"phase": "ACTIVE", "samples": [sample] * per}}
               for _ in range(MAX_SAMPLES_PER_BATCH // per + 1)]
    with pytest.raises(ValidationError, match="at most"):
        SyncBatch(batch_id=uuid.uuid4(), sent_at=T0, updates=updates)
    with pytest.raises(ValidationError):
        SyncBatch(batch_id=uuid.uuid4(), sent_at=T0, surprise="field")


@pytest.mark.parametrize("mode, event, wanted", [
    (None, True, True), (None, False, False),
    ("central", False, True), ("incident_only", True, True), ("incident_only", False, False),
    ("scheduled", True, True), ("local_only", True, False), ("manual", True, False),
])
def test_which_files_the_centre_wants(mode, event, wanted):
    assert media_wanted_centrally(mode, event_linked=event) is wanted


def test_upload_windows_including_overnight():
    assert in_window(time(3, 0), time(1, 0), time(5, 0))
    assert not in_window(time(6, 0), time(1, 0), time(5, 0))
    assert in_window(time(23, 30), time(22, 0), time(6, 0)) and in_window(time(2, 0), time(22, 0), time(6, 0))
    assert not in_window(time(12, 0), time(22, 0), time(6, 0))
    assert in_window(time(12, 0), None, None)
