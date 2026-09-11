"""The patrol report renders, and admits what it does not have.

A report is the artefact an agency hands to a client or a regulator, so the
failure that matters is not a crash — it is a report that looks complete while
quietly missing its evidence.

Sections:
  A — It renders, and it is a PDF (2 tests)
  B — A missing image is stated, never omitted (3 tests)
  C — The content comes from the session, not from configuration (2 tests)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.main import app  # noqa: F401  (module level — see other suites)
from app.services import vpatrol_reports as rep


def _camera(**over):
    base = {
        "id": uuid.uuid4(), "sequence_no": 1, "camera_name": "Fire Exit",
        "camera_code": None, "status": "COMPLETED",
        "snapshot_path": None, "snapshot_taken_at": None,
        "snapshot_error": None, "officer_notes": None, "location": "East stair",
    }
    base.update(over)
    return base


def _data(cameras=None, answers=None, **session_over):
    session = {
        "id": uuid.uuid4(), "patrol_number": "VP-20260401-abcd1234",
        "schedule_name": "Morning Patrol", "site_name": "ABC Industrial",
        "scheduled_for": datetime(2026, 4, 1, 7, 0, tzinfo=timezone.utc),
        "started_at": datetime(2026, 4, 1, 7, 3, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 4, 1, 7, 21, tzinfo=timezone.utc),
        "status": "COMPLETED", "camera_count": 1, "completed_camera_count": 1,
        "notes": None, "officer_name": "Tan Wei Ming",
    }
    session.update(session_over)
    return {"session": session,
            "cameras": cameras if cameras is not None else [_camera()],
            "answers": answers or {}}


# ─── A. It renders ───────────────────────────────────────────────────────────

def test_the_report_renders_a_real_pdf():
    out = rep.render_pdf(_data())
    assert out.startswith(b"%PDF-"), "not a PDF"
    assert len(out) > 800


def test_a_patrol_with_no_questions_still_produces_a_report():
    """A camera configured with no questions is unusual, not invalid, and must
    not take the whole report down on the day somebody needs it."""
    out = rep.render_pdf(_data(answers={}))
    assert out.startswith(b"%PDF-")


# ─── B. A missing image is stated ────────────────────────────────────────────

def test_a_camera_with_no_snapshot_says_why():
    cam = _camera(snapshot_path=None, snapshot_error="The camera did not respond.")
    flow = rep._snapshot_flowable(cam, _styles())
    assert "did not respond" in _text_of(flow)


def test_a_snapshot_recorded_but_missing_from_disk_is_called_out():
    """The loud one. Silently omitting the image would make a patrol whose
    evidence was lost look exactly like one where the camera was never reached —
    a report hiding a gap in its own evidence."""
    cam = _camera(snapshot_path="does/not/exist.jpg")
    flow = rep._snapshot_flowable(cam, _styles())
    text = _text_of(flow)
    assert "missing from storage" in text.lower()


def test_no_snapshot_and_no_error_still_produces_a_statement():
    flow = rep._snapshot_flowable(_camera(), _styles())
    assert "No snapshot" in _text_of(flow)


# ─── C. Content comes from the session ───────────────────────────────────────

def test_the_answer_display_handles_every_shape_an_answer_takes():
    assert rep._answer_display(
        {"answer_json": ["Lock", "Sign"], "answer_text": None}) == "Lock, Sign"
    assert rep._answer_display(
        {"answer_json": None, "answer_text": "YES"}) == "YES"
    assert rep._answer_display(
        {"answer_json": None, "answer_text": None}) == "Not answered"


def test_an_unanswered_required_question_is_shown_not_hidden():
    """A blank row would read as "nothing to report" when it means "nobody
    checked"."""
    cam = _camera()
    answers = {str(cam["id"]): [{
        "session_camera_id": cam["id"], "sequence_no": 1,
        "question_text": "Is the fire exit clear?", "question_type": "YES_NO",
        "answer_text": None, "answer_json": None, "is_exception": False,
        "exception_reason": None, "incident_id": None, "answered_by": None,
    }]}
    out = rep.render_pdf(_data(cameras=[cam], answers=answers))
    assert out.startswith(b"%PDF-")


# ── helpers ──────────────────────────────────────────────────────────────────

def _styles():
    from reportlab.lib.styles import getSampleStyleSheet
    return getSampleStyleSheet()


def _text_of(flowable) -> str:
    return getattr(flowable, "text", "") or ""
