"""P3-D: Parking ↔ LPR auto-entry/exit tests.

Tests follow the same pattern as the rest of the suite:
- real DB session with RLS set to the test tenant
- no mocking of DB; only the Redis publish is mocked (it's a side-effect, not
  the thing being tested here)
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from httpx import AsyncClient


# ─── helpers ────────────────────────────────────────────────────────────────

async def _seed_carpark(client: AsyncClient) -> dict:
    r = await client.post("/api/v1/carparks", json={
        "name": "Test Carpark LPR",
        "total_capacity": 50,
        "levels": 1,
    })
    assert r.status_code == 200, r.text
    return r.json()


async def _seed_camera(client: AsyncClient) -> dict:
    r = await client.post("/api/v1/cameras", json={
        "name": "Entry Camera LPR",
        "location": "Main Gate",
        "ai_modules_enabled": ["lpr"],
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


# ─── LPR camera config CRUD ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_lpr_camera_config(auth_client: AsyncClient):
    carpark = await _seed_carpark(auth_client)
    camera  = await _seed_camera(auth_client)

    r = await auth_client.post("/api/v1/parking/lpr-cameras", json={
        "camera_id":   camera["id"],
        "car_park_id": carpark["id"],
        "trigger_type": "entry",
        "notes": "Main gate entry camera",
    })
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["trigger_type"] == "entry"
    assert cfg["camera_id"] == camera["id"]
    assert cfg["car_park_id"] == carpark["id"]


@pytest.mark.asyncio
async def test_list_lpr_cameras(auth_client: AsyncClient):
    carpark = await _seed_carpark(auth_client)
    camera  = await _seed_camera(auth_client)
    await auth_client.post("/api/v1/parking/lpr-cameras", json={
        "camera_id": camera["id"], "car_park_id": carpark["id"], "trigger_type": "both",
    })

    r = await auth_client.get("/api/v1/parking/lpr-cameras")
    assert r.status_code == 200
    items = r.json()
    assert any(i["camera_id"] == camera["id"] for i in items)


@pytest.mark.asyncio
async def test_invalid_trigger_type_rejected(auth_client: AsyncClient):
    carpark = await _seed_carpark(auth_client)
    camera  = await _seed_camera(auth_client)
    r = await auth_client.post("/api/v1/parking/lpr-cameras", json={
        "camera_id": camera["id"], "car_park_id": carpark["id"], "trigger_type": "INVALID",
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_delete_lpr_camera_config(auth_client: AsyncClient):
    carpark = await _seed_carpark(auth_client)
    camera  = await _seed_camera(auth_client)
    create_r = await auth_client.post("/api/v1/parking/lpr-cameras", json={
        "camera_id": camera["id"], "car_park_id": carpark["id"], "trigger_type": "exit",
    })
    cfg_id = create_r.json()["id"]

    del_r = await auth_client.delete(f"/api/v1/parking/lpr-cameras/{cfg_id}")
    assert del_r.status_code == 200

    list_r = await auth_client.get("/api/v1/parking/lpr-cameras")
    active_ids = [i["id"] for i in list_r.json() if i["is_active"]]
    assert cfg_id not in active_ids


@pytest.mark.asyncio
async def test_delete_nonexistent_config_404(auth_client: AsyncClient):
    r = await auth_client.delete(f"/api/v1/parking/lpr-cameras/{uuid4()}")
    assert r.status_code == 404


# ─── Automation service unit tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_lpr_entry_auto_creates_session(auth_client: AsyncClient, db_session):
    """When an lpr_plate_detected event arrives for an entry-configured camera
    and no active session exists for the plate, a new session is created."""
    from app.services.parking_lpr import handle_lpr_plate_detected

    carpark = await _seed_carpark(auth_client)
    camera  = await _seed_camera(auth_client)

    # Configure the camera as an entry trigger
    await auth_client.post("/api/v1/parking/lpr-cameras", json={
        "camera_id": camera["id"], "car_park_id": carpark["id"], "trigger_type": "entry",
    })

    mock_redis = AsyncMock()
    mock_redis.publish = AsyncMock()

    plate = "SGB1234C"
    with patch("app.services.parking_lpr._publish_parking_event", new=AsyncMock()):
        await handle_lpr_plate_detected(
            db_session=db_session,
            redis_client=mock_redis,
            tenant_id=str(db_session.info.get("tenant_id", uuid4())),
            data={
                "camera_id": camera["id"],
                "plate_number": plate,
                "direction": "unknown",
                "detection_id": str(uuid4()),
            },
        )

    # Verify session was created
    r = await auth_client.get("/api/v1/parking/sessions/lpr-triggered")
    assert r.status_code == 200
    sessions = r.json()
    matching = [s for s in sessions if s["vehicle_plate"] == plate]
    assert len(matching) >= 1
    assert matching[0]["status"] == "active"


@pytest.mark.asyncio
async def test_lpr_entry_no_duplicate_when_session_active(auth_client: AsyncClient, db_session):
    """If a plate already has an active session, a second entry event must not
    create a duplicate — the automation handler silently no-ops."""
    from app.services.parking_lpr import handle_lpr_plate_detected

    carpark = await _seed_carpark(auth_client)
    camera  = await _seed_camera(auth_client)
    await auth_client.post("/api/v1/parking/lpr-cameras", json={
        "camera_id": camera["id"], "car_park_id": carpark["id"], "trigger_type": "entry",
    })

    plate = "SHX9999Z"
    # Seed an existing active session manually
    await auth_client.post("/api/v1/parking/sessions/entry", json={
        "vehicle_plate": plate,
        "car_park_id": carpark["id"],
    })

    mock_redis = AsyncMock()
    with patch("app.services.parking_lpr._publish_parking_event", new=AsyncMock()):
        await handle_lpr_plate_detected(
            db_session=db_session,
            redis_client=mock_redis,
            tenant_id=str(db_session.info.get("tenant_id", uuid4())),
            data={
                "camera_id": camera["id"],
                "plate_number": plate,
                "direction": "unknown",
                "detection_id": str(uuid4()),
            },
        )

    r = await auth_client.get("/api/v1/parking/sessions", params={"status": "active"})
    plate_sessions = [s for s in r.json() if s.get("vehicle_plate") == plate]
    assert len(plate_sessions) == 1, "Expected exactly one active session, not a duplicate"


@pytest.mark.asyncio
async def test_lpr_exit_auto_closes_session(auth_client: AsyncClient, db_session):
    """Exit-type camera event closes the most-recent active session for the plate."""
    from app.services.parking_lpr import handle_lpr_plate_detected

    carpark = await _seed_carpark(auth_client)
    entry_cam = await _seed_camera(auth_client)
    # Create a separate exit camera
    exit_cam_r = await auth_client.post("/api/v1/cameras", json={
        "name": "Exit Camera LPR", "location": "Exit Gate", "ai_modules_enabled": ["lpr"],
    })
    exit_cam = exit_cam_r.json()

    # Configure exit camera
    await auth_client.post("/api/v1/parking/lpr-cameras", json={
        "camera_id": exit_cam["id"], "car_park_id": carpark["id"], "trigger_type": "exit",
    })

    plate = "SDA5678B"
    # Create an active session first
    await auth_client.post("/api/v1/parking/sessions/entry", json={
        "vehicle_plate": plate, "car_park_id": carpark["id"],
    })

    mock_redis = AsyncMock()
    with patch("app.services.parking_lpr._publish_parking_event", new=AsyncMock()):
        await handle_lpr_plate_detected(
            db_session=db_session,
            redis_client=mock_redis,
            tenant_id=str(db_session.info.get("tenant_id", uuid4())),
            data={
                "camera_id": exit_cam["id"],
                "plate_number": plate,
                "direction": "out",
                "detection_id": str(uuid4()),
            },
        )

    r = await auth_client.get("/api/v1/parking/sessions/lpr-triggered")
    completed = [s for s in r.json() if s["vehicle_plate"] == plate and s["status"] == "completed"]
    assert len(completed) >= 1


@pytest.mark.asyncio
async def test_lpr_both_direction_hint_entry(auth_client: AsyncClient, db_session):
    """'both' trigger type + direction='in' → creates entry session."""
    from app.services.parking_lpr import handle_lpr_plate_detected

    carpark = await _seed_carpark(auth_client)
    camera  = await _seed_camera(auth_client)
    await auth_client.post("/api/v1/parking/lpr-cameras", json={
        "camera_id": camera["id"], "car_park_id": carpark["id"], "trigger_type": "both",
    })

    plate = "SCA1111A"
    mock_redis = AsyncMock()
    with patch("app.services.parking_lpr._publish_parking_event", new=AsyncMock()):
        await handle_lpr_plate_detected(
            db_session=db_session,
            redis_client=mock_redis,
            tenant_id=str(db_session.info.get("tenant_id", uuid4())),
            data={
                "camera_id": camera["id"],
                "plate_number": plate,
                "direction": "in",
                "detection_id": str(uuid4()),
            },
        )

    r = await auth_client.get("/api/v1/parking/sessions/lpr-triggered")
    sessions = [s for s in r.json() if s["vehicle_plate"] == plate]
    assert len(sessions) >= 1


@pytest.mark.asyncio
async def test_unconfigured_camera_noop(auth_client: AsyncClient, db_session):
    """A camera not in parking_lpr_cameras must not trigger any session change."""
    from app.services.parking_lpr import handle_lpr_plate_detected

    mock_redis = AsyncMock()
    random_camera_id = str(uuid4())
    plate = "SSS0001S"

    with patch("app.services.parking_lpr._publish_parking_event", new=AsyncMock()) as pub:
        await handle_lpr_plate_detected(
            db_session=db_session,
            redis_client=mock_redis,
            tenant_id=str(db_session.info.get("tenant_id", uuid4())),
            data={
                "camera_id": random_camera_id,
                "plate_number": plate,
                "direction": "in",
                "detection_id": str(uuid4()),
            },
        )
        pub.assert_not_awaited()


# ─── LPR-triggered sessions endpoint ────────────────────────────────────────

@pytest.mark.asyncio
async def test_lpr_triggered_sessions_endpoint(auth_client: AsyncClient):
    r = await auth_client.get("/api/v1/parking/sessions/lpr-triggered")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.asyncio
async def test_lpr_triggered_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/parking/sessions/lpr-triggered")
    assert r.status_code == 401


# ─── P3-E: Shift briefing endpoint ──────────────────────────────────────────

async def _seed_shift(client: AsyncClient) -> dict:
    """Create a scheduled shift and return the response JSON."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    r = await client.post("/api/v1/shifts", json={
        "scheduled_start": (now - timedelta(hours=1)).isoformat(),
        "scheduled_end":   (now + timedelta(hours=7)).isoformat(),
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.mark.asyncio
async def test_briefing_returns_200_and_shape(auth_client: AsyncClient):
    """GET /{shift_id}/briefing returns the expected envelope."""
    shift = await _seed_shift(auth_client)
    r = await auth_client.get(f"/api/v1/shifts/{shift['id']}/briefing")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "shift" in body
    assert "active_work_permits" in body
    assert "expected_visitors" in body
    assert "open_alerts" in body
    assert "pending_deliveries" in body
    assert "previous_handover" in body
    assert "summary" in body
    summary = body["summary"]
    assert "work_permits_count" in summary
    assert "visitors_count" in summary
    assert "open_alerts_count" in summary
    assert "deliveries_count" in summary
    assert "has_previous_handover" in summary


@pytest.mark.asyncio
async def test_briefing_summary_counts_match_arrays(auth_client: AsyncClient):
    """summary.* counts must equal the length of their corresponding arrays."""
    shift = await _seed_shift(auth_client)
    r = await auth_client.get(f"/api/v1/shifts/{shift['id']}/briefing")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["work_permits_count"] == len(body["active_work_permits"])
    assert body["summary"]["visitors_count"]      == len(body["expected_visitors"])
    assert body["summary"]["open_alerts_count"]   == len(body["open_alerts"])
    assert body["summary"]["deliveries_count"]    == len(body["pending_deliveries"])


@pytest.mark.asyncio
async def test_briefing_nonexistent_shift_404(auth_client: AsyncClient):
    """A shift_id that does not exist must return 404."""
    from uuid import uuid4
    r = await auth_client.get(f"/api/v1/shifts/{uuid4()}/briefing")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_briefing_requires_auth(client: AsyncClient):
    """Unauthenticated request must be rejected."""
    from uuid import uuid4
    r = await client.get(f"/api/v1/shifts/{uuid4()}/briefing")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_briefing_includes_active_permits_for_shift(auth_client: AsyncClient):
    """A work permit active during the shift window must appear in active_work_permits."""
    from datetime import datetime, timedelta, timezone
    shift = await _seed_shift(auth_client)

    now = datetime.now(timezone.utc)
    wp_r = await auth_client.post("/api/v1/work-permits", json={
        "contractor_name": "Briefing Test Contractor",
        "work_description": "Electrical maintenance",
        "start_at": (now - timedelta(hours=2)).isoformat(),
        "end_at":   (now + timedelta(hours=2)).isoformat(),
    })
    # Accept 200 or 201; skip if endpoint not available yet
    if wp_r.status_code not in (200, 201):
        pytest.skip("work-permits endpoint not available")

    r = await auth_client.get(f"/api/v1/shifts/{shift['id']}/briefing")
    assert r.status_code == 200
    permits = r.json()["active_work_permits"]
    names = [p["contractor_name"] for p in permits]
    assert "Briefing Test Contractor" in names


@pytest.mark.asyncio
async def test_briefing_includes_expected_visitors(auth_client: AsyncClient):
    """A visitor with status 'pending' must appear in expected_visitors."""
    from datetime import datetime, timedelta, timezone
    shift = await _seed_shift(auth_client)

    now = datetime.now(timezone.utc)
    v_r = await auth_client.post("/api/v1/visitors", json={
        "full_name": "Briefing Visitor Test",
        "expected_from": (now - timedelta(hours=1)).isoformat(),
        "expected_until": (now + timedelta(hours=3)).isoformat(),
    })
    if v_r.status_code not in (200, 201):
        pytest.skip("visitors endpoint not available")

    r = await auth_client.get(f"/api/v1/shifts/{shift['id']}/briefing")
    assert r.status_code == 200
    visitors = r.json()["expected_visitors"]
    names = [v["full_name"] for v in visitors]
    assert "Briefing Visitor Test" in names


@pytest.mark.asyncio
async def test_briefing_shift_info_matches(auth_client: AsyncClient):
    """The shift sub-object must include id and status fields."""
    shift = await _seed_shift(auth_client)
    r = await auth_client.get(f"/api/v1/shifts/{shift['id']}/briefing")
    assert r.status_code == 200
    shift_info = r.json()["shift"]
    assert shift_info["id"] == shift["id"]
    assert "status" in shift_info


# ─── P3-F: Alarm arm/disarm tied to shift schedule ──────────────────────────

async def _seed_site(client: AsyncClient) -> dict:
    r = await client.post("/api/v1/sites", json={"name": "Test Site P3F"})
    if r.status_code not in (200, 201):
        pytest.skip("sites endpoint not available")
    return r.json()


async def _seed_alarm_panel(client: AsyncClient, site_id: str) -> dict:
    r = await client.post("/api/v1/alarms/panels", json={
        "name": "P3F Panel",
        "site_id": site_id,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _seed_site_shift(client: AsyncClient, site_id: str) -> dict:
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    r = await client.post("/api/v1/shifts", json={
        "scheduled_start": (now - timedelta(hours=1)).isoformat(),
        "scheduled_end":   (now + timedelta(hours=7)).isoformat(),
        "site_id": site_id,
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.mark.asyncio
async def test_shift_start_disarms_site_panels(auth_client: AsyncClient):
    """Starting a shift must set all site panels to arm_state='disarmed'."""
    site   = await _seed_site(auth_client)
    panel  = await _seed_alarm_panel(auth_client, site["id"])

    # Pre-arm the panel so the disarm action is observable
    arm_r = await auth_client.put(
        f"/api/v1/alarms/panels/{panel['id']}/arm",
        json={"mode": "away"},
    )
    assert arm_r.status_code == 200

    shift = await _seed_site_shift(auth_client, site["id"])
    start_r = await auth_client.post(f"/api/v1/shifts/{shift['id']}/start")
    assert start_r.status_code == 200

    # Panel arm_state must now be 'disarmed'
    panel_r = await auth_client.get(f"/api/v1/alarms/panels/{panel['id']}")
    assert panel_r.status_code == 200
    assert panel_r.json()["arm_state"] == "disarmed"


@pytest.mark.asyncio
async def test_shift_end_arms_site_panels(auth_client: AsyncClient):
    """Ending a shift must set all site panels to 'armed_away'."""
    site   = await _seed_site(auth_client)
    panel  = await _seed_alarm_panel(auth_client, site["id"])
    shift  = await _seed_site_shift(auth_client, site["id"])

    # Start then immediately end
    await auth_client.post(f"/api/v1/shifts/{shift['id']}/start")
    end_r = await auth_client.post(f"/api/v1/shifts/{shift['id']}/end")
    assert end_r.status_code == 200

    panel_r = await auth_client.get(f"/api/v1/alarms/panels/{panel['id']}")
    assert panel_r.status_code == 200
    assert panel_r.json()["arm_state"] == "armed_away"


@pytest.mark.asyncio
async def test_shift_start_logs_disarm_event(auth_client: AsyncClient):
    """Shift start must produce a 'panel_disarmed' alarm_event row."""
    site   = await _seed_site(auth_client)
    panel  = await _seed_alarm_panel(auth_client, site["id"])

    # Pre-arm
    await auth_client.put(f"/api/v1/alarms/panels/{panel['id']}/arm", json={"mode": "away"})

    shift = await _seed_site_shift(auth_client, site["id"])
    await auth_client.post(f"/api/v1/shifts/{shift['id']}/start")

    events_r = await auth_client.get(f"/api/v1/alarms/panels/{panel['id']}/events")
    assert events_r.status_code == 200
    types = [e["event_type"] for e in events_r.json()]
    assert "panel_disarmed" in types


@pytest.mark.asyncio
async def test_shift_end_logs_arm_event(auth_client: AsyncClient):
    """Shift end must produce a 'panel_armed_away' alarm_event row."""
    site   = await _seed_site(auth_client)
    panel  = await _seed_alarm_panel(auth_client, site["id"])
    shift  = await _seed_site_shift(auth_client, site["id"])

    await auth_client.post(f"/api/v1/shifts/{shift['id']}/start")
    await auth_client.post(f"/api/v1/shifts/{shift['id']}/end")

    events_r = await auth_client.get(f"/api/v1/alarms/panels/{panel['id']}/events")
    assert events_r.status_code == 200
    types = [e["event_type"] for e in events_r.json()]
    assert "panel_armed_away" in types


@pytest.mark.asyncio
async def test_shift_without_site_no_panel_change(auth_client: AsyncClient):
    """A shift with no site_id must not affect any alarm panel."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)

    site  = await _seed_site(auth_client)
    panel = await _seed_alarm_panel(auth_client, site["id"])

    # Create shift with NO site_id — tenant-wide shift
    shift_r = await auth_client.post("/api/v1/shifts", json={
        "scheduled_start": (now - timedelta(hours=1)).isoformat(),
        "scheduled_end":   (now + timedelta(hours=7)).isoformat(),
    })
    assert shift_r.status_code in (200, 201), shift_r.text
    shift = shift_r.json()

    panel_state_before = (await auth_client.get(f"/api/v1/alarms/panels/{panel['id']}")).json()["arm_state"]

    await auth_client.post(f"/api/v1/shifts/{shift['id']}/start")

    panel_state_after = (await auth_client.get(f"/api/v1/alarms/panels/{panel['id']}")).json()["arm_state"]
    assert panel_state_before == panel_state_after, "Panel state should not change for a site-less shift"


@pytest.mark.asyncio
async def test_briefing_includes_alarm_panels(auth_client: AsyncClient):
    """Briefing endpoint must include alarm_panels array and summary counts."""
    site  = await _seed_site(auth_client)
    await _seed_alarm_panel(auth_client, site["id"])
    shift = await _seed_site_shift(auth_client, site["id"])

    r = await auth_client.get(f"/api/v1/shifts/{shift['id']}/briefing")
    assert r.status_code == 200
    body = r.json()
    assert "alarm_panels" in body
    assert isinstance(body["alarm_panels"], list)
    assert "panels_armed" in body["summary"]
    assert "panels_total" in body["summary"]
    assert body["summary"]["panels_total"] >= 1
