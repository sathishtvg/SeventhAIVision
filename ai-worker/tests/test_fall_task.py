"""Slip/fall detection pipeline tests."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np

from shared.events import FrameJob

JPEG_1X1_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
    "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgN"
    "DRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjL/wAARCAABAAEDASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAA"
    "AAAAAAAAAAAAAP/EABQBAQAAAAAAAAAAAAAAAAAAAAD/xAAUEQEAAAAAAAAAAAAAAAAAAAAA"
    "/9oADAMBAAIRAxEAPwCwABmX/9k="
)

_FRAME = np.zeros((4, 4, 3), dtype=np.uint8)
_FALL_DET = {
    "fall_confidence": 0.8,
    "pose_keypoints": None,
    "person_bbox": {"x1": 10, "y1": 10, "x2": 100, "y2": 200},
}


def _make_job(modules=("fall",)) -> FrameJob:
    return FrameJob(
        job_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        camera_id=uuid.uuid4(),
        frame_jpeg_b64=JPEG_1X1_B64,
        frame_width=4,
        frame_height=4,
        captured_at=datetime.now(timezone.utc),
        ai_modules_enabled=list(modules),
    )


def _setup_conn(mock_session):
    """Wire mock_session so `with get_tenant_session(...) as conn:` yields a mock conn."""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    mock_session.return_value.__enter__.return_value = conn
    return conn


# ── Pipeline tests ────────────────────────────────────────────────────────────

@patch("worker.tasks.fall_task.publish_alert_created")
def test_module_not_enabled_returns_early(mock_alert):
    from worker.tasks.fall_task import process_frame_job
    process_frame_job(_make_job(modules=["lpr"]))
    mock_alert.assert_not_called()


@patch("worker.tasks.fall_task.publish_alert_created")
@patch("worker.tasks.fall_task.detect_falls", return_value=[])
@patch("worker.tasks.fall_task.decode_frame", return_value=_FRAME)
def test_no_falls_no_alert(mock_decode, mock_detect, mock_alert):
    """No falls detected → no alert or incident published."""
    from worker.tasks.fall_task import process_frame_job
    process_frame_job(_make_job())
    mock_alert.assert_not_called()


@patch("worker.tasks.fall_task.publish_incident_created")
@patch("worker.tasks.fall_task.publish_alert_created")
@patch("worker.storage.save_evidence_snapshot", return_value=("path/e.jpg", "abc"))
@patch("worker.tasks.fall_task.get_tenant_setting", return_value=0.45)
@patch("worker.tasks.fall_task.get_tenant_session")
@patch("worker.tasks.fall_task.detect_falls", return_value=[_FALL_DET])
@patch("worker.tasks.fall_task.decode_frame", return_value=_FRAME)
def test_fall_detected_fires_high_alert_and_incident(
    mock_decode, mock_detect, mock_session, mock_setting, mock_evidence, mock_alert, mock_incident,
):
    """Confirmed fall above confidence threshold → high-severity alert + auto-incident."""
    _setup_conn(mock_session)
    from worker.tasks.fall_task import process_frame_job
    process_frame_job(_make_job())

    mock_alert.assert_called_once()
    mock_incident.assert_called_once()
    _, _, module_type, severity = mock_alert.call_args[0]
    assert module_type == "fall"
    assert severity == "high"


@patch("worker.tasks.fall_task.publish_alert_created")
@patch("worker.tasks.fall_task.get_tenant_setting", return_value=0.45)
@patch("worker.tasks.fall_task.get_tenant_session")
@patch(
    "worker.tasks.fall_task.detect_falls",
    return_value=[{"fall_confidence": 0.3, "pose_keypoints": None, "person_bbox": {"x1": 0, "y1": 0, "x2": 50, "y2": 100}}],
)
@patch("worker.tasks.fall_task.decode_frame", return_value=_FRAME)
def test_below_confidence_threshold_no_alert(mock_decode, mock_detect, mock_session, mock_setting, mock_alert):
    """A detection below the confidence threshold is discarded — no alert or incident."""
    _setup_conn(mock_session)
    from worker.tasks.fall_task import process_frame_job
    process_frame_job(_make_job())
    mock_alert.assert_not_called()


# ── Pure function tests (worker.models.fall_model._is_fallen) ─────────────────

def test_is_fallen_wide_bbox():
    """Bounding box aspect_ratio > 2.0 indicates a prone (fallen) person."""
    from worker.models.fall_model import _is_fallen
    # w=300, h=100 → aspect_ratio = 3.0 > 2.0
    fallen, conf = _is_fallen(None, [0, 0, 300, 100])
    assert fallen is True
    assert conf >= 0.5


def test_is_fallen_standing_person_returns_false():
    """A standing person with nose well above hip level is not fallen."""
    from worker.models.fall_model import _is_fallen
    kp = np.zeros((17, 3), dtype=float)
    kp[0] = [100, 50, 0.9]    # nose at y=50 (high up)
    kp[11] = [90, 200, 0.9]   # left hip at y=200
    kp[12] = [110, 200, 0.9]  # right hip at y=200
    # hip_y = 200; hip_y * 0.95 = 190 → nose_y (50) < 190 → not fallen
    fallen, conf = _is_fallen(kp, [0, 0, 50, 250])
    assert fallen is False
    assert conf == 0.0


def test_is_fallen_head_below_hips():
    """Person with nose at or below hip level (horizontal) is detected as fallen."""
    from worker.models.fall_model import _is_fallen
    kp = np.zeros((17, 3), dtype=float)
    kp[0] = [100, 200, 0.9]   # nose at y=200 (low — below hips)
    kp[11] = [90, 150, 0.9]   # left hip at y=150
    kp[12] = [110, 150, 0.9]  # right hip at y=150
    # hip_y = 150; hip_y * 0.95 = 142.5 → nose_y (200) >= 142.5 → fallen
    fallen, conf = _is_fallen(kp, [0, 0, 200, 200])
    assert fallen is True
    assert conf > 0.0
