"""The AI module, phase 8: reading an IC on our own server, and the full number never reaching the model.

Sections:
  A — Reading the card's text (4 tests)
  B — The endpoints, and where the number goes (4 tests)
  C — The real reader, where it is installed (1 test)
"""
from __future__ import annotations

import importlib.util
import io
import json
import uuid

import pytest

from app.main import app  # noqa: F401
from app.services import ai_ic
from tests.test_ai_assistant import _assistant_world, _chat, _op, scripted  # noqa: F401  (the fixture)
from tests.test_ai_learning import ADMIN, OPERATOR, _client, _run, _sql

#: What RapidOCR returned for a printed NRIC (front and back), spaces dropped as it does.
FRONT = ["REPUBLICOFSINGAPORE", "IDENTITYCARDN0.S1234567D", "Name", "TANAHKOW", "Race", "CHINESE", "Dateofbirth",
         "Sex", "02-05-1990", "M", "Country/Placeofbirth", "SINGAPORE"]
BACK = ["Dateofissue15-06-2008", "Address", "BLK123JURONGWESTSTREET41", "#05-678", "SINGAPORE640123"]


# ─── A. Reading the card's text ──────────────────────────────────────────────

def test_the_fields_of_an_nric_are_read_from_its_lines():
    r = ai_ic.parse(FRONT, BACK)
    assert r.fields == {"nric_fin": "S1234567D", "full_name": "TANAHKOW", "race": "CHINESE",
                        "country_of_birth": "SINGAPORE", "date_of_birth": "1990-05-02", "sex": "M",
                        "address": "BLK123JURONGWESTSTREET41 #05-678 SINGAPORE 640123"}
    assert r.check_letter_ok is True and r.unread == ["nationality"]


def test_a_fin_card_and_a_front_alone_are_read_too():
    fin = ["WORKPERMIT", "FIN G1234567X", "Name", "RAVI KUMAR", "Date of Birth", "11-11-1991", "Sex", "M",
           "Nationality", "INDIAN"]
    r = ai_ic.parse(fin, [])
    assert r.fields["nric_fin"] == "G1234567X" and r.fields["nationality"] == "INDIAN"
    assert r.fields["full_name"] == "RAVI KUMAR" and r.fields["date_of_birth"] == "1991-11-11"
    assert "address" in r.unread


def test_the_check_letter_catches_a_misread_number():
    assert ai_ic.check_letter_ok("S1234567D") is True
    assert ai_ic.check_letter_ok("S1234567A") is False
    assert ai_ic.check_letter_ok("T1234567J") is True
    assert ai_ic.check_letter_ok("F1234567N") is True
    assert ai_ic.check_letter_ok("M1234567K") is None, "the M series is not checked here"


def test_spaces_are_put_back_where_letters_stand_well_apart():
    box = lambda l, r: [[l, 0], [r, 0], [r, 30], [l, 30]]  # noqa: E731
    boxes = [box(0, 18), box(19, 37), box(38, 56), box(80, 98), box(99, 117)]
    assert ai_ic.spaced("TANAH", boxes) == "TAN AH"
    assert ai_ic.spaced("TANAH", None) == "TANAH"


# ─── B. The endpoints, and where the number goes ─────────────────────────────

@pytest.fixture
def canned(monkeypatch):
    seen = []

    def lines(image: bytes):
        seen.append(len(image))
        return FRONT if len(seen) == 1 else BACK
    monkeypatch.setattr(ai_ic, "ocr_lines", lines)
    return seen


async def _ic_world(roles=(2, 8)):
    w = await _assistant_world()
    await _run([
        ("UPDATE ai_module_licenses SET features = array_append(features, 'ic_reading') WHERE tenant_id = :t",
         {"t": w["tenant"]}),
        ("INSERT INTO ai_feature_settings (tenant_id, feature_key, enabled, role_ids) "
         "VALUES (:t, 'ic_reading', TRUE, CAST(:r AS smallint[]))", {"t": w["tenant"], "r": list(roles)}),
    ])
    return w


def _photos():
    return {"front": ("front.jpg", b"\xff\xd8front-photo", "image/jpeg"),
            "back": ("back.jpg", b"\xff\xd8back-photo", "image/jpeg")}


@pytest.mark.asyncio
async def test_an_ic_is_read_and_the_assistant_is_given_only_the_last_four_characters(canned):
    w = await _ic_world()
    async with _client() as c:
        r = await c.post("/api/v1/ai/assistant/ic", headers=w["h"][ADMIN], files=_photos())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fields"]["nric_fin"] == "S1234567D" and body["check_letter_ok"] is True
    assert "S1234567D" not in body["message"] and "*****567D" in body["message"]
    assert "TANAHKOW" in body["message"] and "one question at a time" in body["message"]
    assert canned == [len(b"\xff\xd8front-photo"), len(b"\xff\xd8back-photo")]
    drafts = await _sql("SELECT fields FROM ai_ic_drafts WHERE tenant_id = :t", {"t": w["tenant"]})
    assert len(drafts) == 1 and drafts[0]["fields"]["nric_fin"] == "S1234567D"
    # No photo anywhere: the draft holds text only.
    assert set(drafts[0]["fields"]) <= set(ai_ic.FIELDS)


@pytest.mark.asyncio
async def test_the_full_number_is_filled_in_only_when_the_person_confirms_and_the_draft_then_goes(canned, scripted):
    w = await _ic_world()
    async with _client() as c:
        read = (await c.post("/api/v1/ai/assistant/ic", headers=w["h"][ADMIN], files=_photos())).json()
        target = w["users"][OPERATOR]
        model = scripted([{"tool": "use_function", "args": {
            "operation_id": _op("PUT", "/api/v1/users/{user_id}"), "path_params": {"user_id": str(target)},
            "body": {"nric_fin": "*****567D", "date_of_birth": "1990-05-02"}}}, {"text": "Prepared."}])
        _, events = await _chat(c, w, ADMIN, read["message"])
        card = next(d["action"] for k, d in events if k == "confirm")
        ok = await c.post(f"/api/v1/ai/assistant/actions/{card['id']}/confirm", headers=w["h"][ADMIN])
    assert "S1234567D" not in json.dumps([model.seen, model.results], default=str), "the model saw the full number"
    shown = {f["name"]: f["value"] for f in card["fields"]}
    assert shown["nric_fin"] == "*****567D (from the IC you scanned)"
    assert ok.status_code == 200 and ok.json()["status"] == "CONFIRMED", ok.text
    saved = await _sql("SELECT nric_fin FROM users WHERE id = :u", {"u": target})
    assert saved[0]["nric_fin"] == "S1234567D"
    assert await _sql("SELECT 1 FROM ai_ic_drafts WHERE tenant_id = :t", {"t": w["tenant"]}) == []


@pytest.mark.asyncio
async def test_the_person_corrects_what_was_read_before_it_goes_anywhere(canned):
    w = await _ic_world()
    async with _client() as c:
        read = (await c.post("/api/v1/ai/assistant/ic", headers=w["h"][ADMIN], files=_photos())).json()
        fixed = await c.put(f"/api/v1/ai/assistant/ic/{read['draft_id']}", headers=w["h"][ADMIN],
                            json={**read["fields"], "full_name": "TAN AH KOW", "nric_fin": "s1234567d"})
        other = await c.put(f"/api/v1/ai/assistant/ic/{uuid.uuid4()}", headers=w["h"][ADMIN], json={"full_name": "X"})
    assert fixed.status_code == 200 and fixed.json()["fields"]["full_name"] == "TAN AH KOW"
    assert fixed.json()["fields"]["nric_fin"] == "S1234567D" and "TAN AH KOW" in fixed.json()["message"]
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_only_people_who_may_create_staff_and_whose_role_is_chosen_read_an_ic(canned):
    w = await _ic_world(roles=(2,))
    async with _client() as c:
        manager = await c.post("/api/v1/ai/assistant/ic", headers=w["h"][8], files=_photos())
        supervisor = await c.post("/api/v1/ai/assistant/ic", headers=w["h"][3], files=_photos())
    assert manager.status_code == 403 and "not switched on for your role" in manager.json()["detail"]
    assert supervisor.status_code == 403
    assert canned == [], "a photo was read for someone not allowed"


# ─── C. The real reader ──────────────────────────────────────────────────────

@pytest.mark.skipif(importlib.util.find_spec("rapidocr_onnxruntime") is None,
                    reason="the IC reader is installed with the image (pyproject), not on every machine")
def test_the_real_reader_reads_a_printed_card():
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1000, 640), (235, 230, 220))
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=34)
    for i, line in enumerate(["REPUBLIC OF SINGAPORE", "IDENTITY CARD No.  S1234567D", "Name", "TAN AH KOW",
                              "Date of birth", "02-05-1990"]):
        d.text((40, 30 + 58 * i), line, fill=(20, 20, 20), font=font)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    r = ai_ic.parse(ai_ic.ocr_lines(buf.getvalue()), [])
    assert r.fields["nric_fin"] == "S1234567D" and r.check_letter_ok is True
    assert r.fields["date_of_birth"] == "1990-05-02"
    assert r.fields["full_name"].replace(" ", "") == "TANAHKOW"
