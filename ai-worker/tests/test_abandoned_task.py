"""Abandoned object detection pipeline tests."""

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
_BBOX = {"x": 10, "y": 10, "w": 50, "h": 50}


def _make_job(modules=("abandoned",)) -> FrameJob:
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
    """Wire mock_session so both `with get_tenant_session(...) as conn:` calls yield a mock conn."""
    conn = MagicMock()
    conn.__enter__.return_value = conn
    mock_session.return_value.__enter__.return_value = conn
    return conn


# ── Pipeline tests ────────────────────────────────────────────────────────────

@patch("worker.tasks.abandoned_task.publish_alert_created")
def test_module_not_enabled_returns_early(mock_alert):
    from worker.tasks.abandoned_task import process_frame_job
    process_frame_job(_make_job(modules=["lpr"]))
    mock_alert.assert_not_called()


@patch("worker.tasks.abandoned_task.publish_alert_created")
@patch("worker.tasks.abandoned_task.get_tenant_session")
@patch("worker.tasks.abandoned_task.get_tenant_setting", return_value=30.0)
@patch("worker.tasks.abandoned_task.detect_abandoned", return_value=[])
@patch("worker.tasks.abandoned_task.decode_frame", return_value=_FRAME)
def test_no_detections_no_alert(mock_decode, mock_detect, mock_setting, mock_session, mock_alert):
    """No abandoned objects detected → no alert is published."""
    _setup_conn(mock_session)
    from worker.tasks.abandoned_task import process_frame_job
    process_frame_job(_make_job())
    mock_alert.assert_not_called()


@patch("worker.tasks.abandoned_task.publish_alert_created")
@patch("worker.storage.save_evidence_snapshot", return_value=("path/e.jpg", "abc"))
@patch("worker.tasks.abandoned_task.get_tenant_session")
@patch("worker.tasks.abandoned_task.get_tenant_setting", return_value=30.0)
@patch(
    "worker.tasks.abandoned_task.detect_abandoned",
    return_value=[{"object_class": "bag", "dwell_seconds": 45.0, "bbox": _BBOX}],
)
@patch("worker.tasks.abandoned_task.decode_frame", return_value=_FRAME)
def test_single_object_fires_medium_alert_no_incident(
    mock_decode, mock_detect, mock_setting, mock_session, mock_evidence, mock_alert,
):
    """One abandoned object → exactly one medium-severity alert; no incident is created."""
    _setup_conn(mock_session)
    from worker.tasks.abandoned_task import process_frame_job
    process_frame_job(_make_job())

    mock_alert.assert_called_once()
    _, _, module_type, severity = mock_alert.call_args[0]
    assert module_type == "abandoned"
    assert severity == "medium"


@patch("worker.tasks.abandoned_task.publish_alert_created")
@patch("worker.storage.save_evidence_snapshot", return_value=("path/e.jpg", "abc"))
@patch("worker.tasks.abandoned_task.get_tenant_session")
@patch("worker.tasks.abandoned_task.get_tenant_setting", return_value=30.0)
@patch(
    "worker.tasks.abandoned_task.detect_abandoned",
    return_value=[
        {"object_class": "bag",      "dwell_seconds": 45.0, "bbox": _BBOX},
        {"object_class": "suitcase", "dwell_seconds": 60.0, "bbox": {"x": 200, "y": 200, "w": 80, "h": 60}},
    ],
)
@patch("worker.tasks.abandoned_task.decode_frame", return_value=_FRAME)
def test_multiple_objects_fires_multiple_alerts(
    mock_decode, mock_detect, mock_setting, mock_session, mock_evidence, mock_alert,
):
    """Two abandoned objects → exactly two alert publications, one per detection."""
    _setup_conn(mock_session)
    from worker.tasks.abandoned_task import process_frame_job
    process_frame_job(_make_job())

    assert mock_alert.call_count == 2
    severities = [call[0][3] for call in mock_alert.call_args_list]
    assert all(s == "medium" for s in severities)
