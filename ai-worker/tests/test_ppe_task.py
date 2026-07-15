"""PPE Detection pipeline tests (plan Phase 3). AI model calls are mocked at
the detect_ppe boundary so these tests exercise pipeline logic — DB writes,
alert creation, auto-incident, tenant settings — without requiring a real GPU
or model weight file."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from shared.events import FrameJob

_FRAME = np.zeros((4, 4, 3), dtype=np.uint8)

# minimal 1×1 black JPEG for a valid frame fixture
JPEG_1X1_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
    "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgN"
    "DRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjL/wAARCAABAAEDASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAA"
    "AAAAAAAAAAAAAP/EABQBAQAAAAAAAAAAAAAAAAAAAAD/xAAUEQEAAAAAAAAAAAAAAAAAAAAA"
    "/9oADAMBAAIRAxEAPwCwABmX/9k="
)


def _make_job(modules=("ppe",)) -> FrameJob:
    return FrameJob(
        job_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        camera_id=uuid.uuid4(),
        frame_jpeg_b64=JPEG_1X1_B64,
        frame_width=640,
        frame_height=480,
        captured_at=datetime.now(timezone.utc),
        ai_modules_enabled=list(modules),
    )


def _mock_conn(conf_value=0.5):
    conn = MagicMock()
    cursor_ctx = MagicMock()
    cursor_ctx.__enter__ = lambda s: cursor_ctx
    cursor_ctx.__exit__ = MagicMock(return_value=False)
    cursor_ctx.execute = MagicMock()
    cursor_ctx.fetchone = MagicMock(return_value=None)
    conn.cursor.return_value = cursor_ctx
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    return conn


@patch("worker.tasks.ppe_task.get_tenant_setting", return_value=0.5)
@patch("worker.tasks.ppe_task.detect_ppe")
@patch("worker.tasks.ppe_task.get_tenant_session")
@patch("worker.tasks.ppe_task.save_evidence_snapshot", return_value=("p/e.jpg", "abc"))
@patch("worker.tasks.ppe_task.publish_alert_created")
@patch("worker.tasks.ppe_task.publish_incident_created")
@patch("worker.tasks.ppe_task.decode_frame", return_value=_FRAME)
def test_ppe_violation_creates_alert_and_incident(
    mock_decode, mock_pub_incident, mock_pub_alert, mock_evidence,
    mock_session, mock_detect, mock_setting,
):
    """PPE violation (missing hard_hat) -> alert (high) + auto-incident."""
    conn = _mock_conn()
    mock_session.return_value.__enter__ = lambda s: conn
    mock_session.return_value.__exit__ = MagicMock(return_value=False)
    mock_detect.return_value = [
        {
            "bbox": (100, 100, 200, 400),
            "confidence": 0.82,
            "items_detected": ["safety_vest"],
            "items_missing": ["hard_hat"],
        }
    ]

    from worker.tasks.ppe_task import process_frame_job

    process_frame_job(_make_job())

    # detections + ppe_events + evidence + alert + incident rows written
    assert conn.cursor.return_value.execute.call_count >= 5
    mock_pub_alert.assert_called_once()
    mock_pub_incident.assert_called_once()
    call_args = mock_pub_alert.call_args[0]
    assert call_args[2] == "ppe"
    assert call_args[3] == "high"


@patch("worker.tasks.ppe_task.get_tenant_setting", return_value=0.5)
@patch("worker.tasks.ppe_task.detect_ppe")
@patch("worker.tasks.ppe_task.get_tenant_session")
@patch("worker.tasks.ppe_task.publish_alert_created")
@patch("worker.tasks.ppe_task.decode_frame", return_value=_FRAME)
def test_no_violation_no_write(mock_decode, mock_pub_alert, mock_session, mock_detect, mock_setting):
    """No PPE violations detected -> no DB writes, no alert."""
    mock_detect.return_value = []  # detect_ppe filters to violations only
    conn = _mock_conn()
    mock_session.return_value.__enter__ = lambda s: conn
    mock_session.return_value.__exit__ = MagicMock(return_value=False)

    from worker.tasks.ppe_task import process_frame_job

    process_frame_job(_make_job())

    mock_pub_alert.assert_not_called()
    conn.cursor.return_value.execute.assert_not_called()


@patch("worker.tasks.ppe_task.get_tenant_setting", return_value=0.5)
@patch("worker.tasks.ppe_task.detect_ppe")
@patch("worker.tasks.ppe_task.get_tenant_session")
@patch("worker.tasks.ppe_task.publish_alert_created")
def test_module_not_enabled_returns_early(mock_pub_alert, mock_session, mock_detect, mock_setting):  # no decode_frame mock needed — returns before decode
    """If 'ppe' not in ai_modules_enabled, return before any model call."""
    from worker.tasks.ppe_task import process_frame_job

    process_frame_job(_make_job(modules=["lpr", "face"]))

    mock_detect.assert_not_called()
    mock_pub_alert.assert_not_called()


@patch("worker.tasks.ppe_task.get_tenant_setting", return_value=0.99)
@patch("worker.tasks.ppe_task.detect_ppe")
@patch("worker.tasks.ppe_task.get_tenant_session")
@patch("worker.tasks.ppe_task.publish_alert_created")
@patch("worker.tasks.ppe_task.decode_frame", return_value=_FRAME)
def test_tenant_setting_passed_to_detect(mock_decode, mock_pub_alert, mock_session, mock_detect, mock_setting):
    """Tenant threshold from settings cache is forwarded to detect_ppe."""
    mock_detect.return_value = []
    conn = _mock_conn()
    mock_session.return_value.__enter__ = lambda s: conn
    mock_session.return_value.__exit__ = MagicMock(return_value=False)

    from worker.tasks.ppe_task import process_frame_job

    process_frame_job(_make_job())

    # detect_ppe should be called with the threshold from settings (0.99)
    mock_detect.assert_called_once()
    _, conf_arg = mock_detect.call_args[0]
    assert conf_arg == 0.99
