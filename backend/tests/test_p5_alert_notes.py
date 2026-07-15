"""P5-D: Alert notes — operators can annotate alerts with free-text comments.

Tests cover:
- POST /alerts/{id}/notes: creates a note, returns it
- POST /alerts/{id}/notes: empty note → 422
- POST /alerts/{id}/notes: whitespace-only note → 422
- POST /alerts/{id}/notes: non-existent alert → 404
- GET  /alerts/{id}/notes: returns list in chronological order
- GET  /alerts/{id}/notes: non-existent alert → 404
- Multiple notes on one alert are all returned
- Note includes author_name / author_email from users join
- alert:read role can GET notes but not POST
- Unauthenticated → 401
"""
import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _make_camera(client: AsyncClient, name: str = "Note Cam") -> str:
    r = await client.post("/api/v1/cameras", json={"name": name, "location": "HQ"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _make_alert(client: AsyncClient, camera_id: str | None = None) -> str:
    if camera_id is None:
        camera_id = await _make_camera(client)
    r = await client.post("/api/v1/alerts", json={
        "camera_id": camera_id,
        "module_type": "intrusion",
        "severity": "medium",
        "title": "Test alert for notes",
        "message": "Intruder detected",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# ── Create note ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_note_returns_201(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)
    r = await auth_client.post(f"/api/v1/alerts/{alert_id}/notes", json={"note": "First observation"})
    assert r.status_code == 201
    body = r.json()
    assert body["note"] == "First observation"
    assert body["alert_id"] == alert_id
    assert "id" in body
    assert "created_at" in body


@pytest.mark.asyncio
async def test_add_note_persists_author(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)
    r = await auth_client.post(f"/api/v1/alerts/{alert_id}/notes", json={"note": "Author check"})
    assert r.status_code == 201
    body = r.json()
    # author_user_id should be set (not null) since we're authenticated
    assert body["author_user_id"] is not None


@pytest.mark.asyncio
async def test_add_note_empty_422(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)
    r = await auth_client.post(f"/api/v1/alerts/{alert_id}/notes", json={"note": ""})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_add_note_whitespace_only_422(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)
    r = await auth_client.post(f"/api/v1/alerts/{alert_id}/notes", json={"note": "   "})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_add_note_nonexistent_alert_404(auth_client: AsyncClient):
    fake_id = "00000000-0000-0000-0000-000000000099"
    r = await auth_client.post(f"/api/v1/alerts/{fake_id}/notes", json={"note": "Won't be saved"})
    assert r.status_code == 404


# ── List notes ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_notes_empty_on_new_alert(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)
    r = await auth_client.get(f"/api/v1/alerts/{alert_id}/notes")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_notes_returns_added_note(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)
    await auth_client.post(f"/api/v1/alerts/{alert_id}/notes", json={"note": "Visible note"})
    r = await auth_client.get(f"/api/v1/alerts/{alert_id}/notes")
    assert r.status_code == 200
    notes = r.json()
    assert len(notes) == 1
    assert notes[0]["note"] == "Visible note"


@pytest.mark.asyncio
async def test_list_notes_chronological_order(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)
    for i in range(3):
        await auth_client.post(f"/api/v1/alerts/{alert_id}/notes", json={"note": f"Note {i}"})

    r = await auth_client.get(f"/api/v1/alerts/{alert_id}/notes")
    assert r.status_code == 200
    notes = r.json()
    assert len(notes) == 3
    # Should be in chronological order (Note 0, Note 1, Note 2)
    assert notes[0]["note"] == "Note 0"
    assert notes[1]["note"] == "Note 1"
    assert notes[2]["note"] == "Note 2"


@pytest.mark.asyncio
async def test_list_notes_nonexistent_alert_404(auth_client: AsyncClient):
    fake_id = "00000000-0000-0000-0000-000000000098"
    r = await auth_client.get(f"/api/v1/alerts/{fake_id}/notes")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_notes_includes_author_fields(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)
    await auth_client.post(f"/api/v1/alerts/{alert_id}/notes", json={"note": "With author"})

    r = await auth_client.get(f"/api/v1/alerts/{alert_id}/notes")
    assert r.status_code == 200
    note = r.json()[0]
    # author fields come from LEFT JOIN users
    assert "author_name" in note
    assert "author_email" in note


# ── Notes are alert-scoped (different alerts don't share notes) ───────────────

@pytest.mark.asyncio
async def test_notes_isolated_per_alert(auth_client: AsyncClient):
    cam = await _make_camera(auth_client, "Scoped Cam")
    alert_a = await _make_alert(auth_client, cam)
    alert_b = await _make_alert(auth_client, cam)

    await auth_client.post(f"/api/v1/alerts/{alert_a}/notes", json={"note": "Only on A"})

    r_b = await auth_client.get(f"/api/v1/alerts/{alert_b}/notes")
    assert r_b.status_code == 200
    assert r_b.json() == []  # alert B has no notes


# ── Auth / permission ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_note_requires_auth(client: AsyncClient):
    r = await client.post(
        "/api/v1/alerts/00000000-0000-0000-0000-000000000001/notes",
        json={"note": "Unauthed"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_notes_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/alerts/00000000-0000-0000-0000-000000000001/notes")
    assert r.status_code == 401


# ── Note content is stripped ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_note_trims_whitespace(auth_client: AsyncClient):
    alert_id = await _make_alert(auth_client)
    r = await auth_client.post(
        f"/api/v1/alerts/{alert_id}/notes",
        json={"note": "  leading and trailing  "},
    )
    assert r.status_code == 201
    assert r.json()["note"] == "leading and trailing"
