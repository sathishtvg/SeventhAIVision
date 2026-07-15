"""Weapon Detection pipeline tests (plan Phase 3)."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np

from shared.events import FrameJob

_FRAME = np.zeros((4, 4, 3), dtype=np.uint8)

JPEG_1X1_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
    "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgN"
    "DRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjL/wAARCAABAAEDASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAA"
    "AAAAAAAAAAAAAP/EABQBAQAAAAAAAAAAAAAAAAAAAAD/xAAUEQEAAAAAAAAAAAAAAAAAAAAA"
    "/9oADAMBAAIRAxEAPwCwABmX/9k="
)


def _make_job(modules=("weapon",)) -> FrameJob:
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


def _mock_conn():
    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = lambda s: cursor
    cursor.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    return conn


@patch("worker.tasks.weapon_task.get_tenant_setting", return_value=0.55)
@patch("worker.tasks.weapon_task.detect_weapons")
@patch("worker.tasks.weapon_task.get_tenant_session")
@patch("worker.tasks.weapon_task.save_evidence_snapshot", return_value=("p/e.jpg", "abc"))
@patch("worker.tasks.weapon_task.publish_alert_created")
@patch("worker.tasks.weapon_task.publish_incident_created")
@patch("worker.tasks.weapon_task.decode_frame", return_value=_FRAME)
def test_firearm_critical_alert_and_incident(
    mock_decode, mock_pub_incident, mock_pub_alert, mock_evidence,
    mock_session, mock_detect, mock_setting,
):
    """Firearm detected -> critical alert + auto-incident."""
    conn = _mock_conn()
    mock_session.return_value.__enter__ = lambda s: conn
    mock_session.return_value.__exit__ = MagicMock(return_value=False)
    mock_detect.return_value = [{"weapon_type": "firearm", "confidence": 0.91, "bbox": (50, 50, 150, 150)}]

    from worker.tasks.weapon_task import process_frame_job

    process_frame_job(_make_job())

    mock_pub_alert.assert_called_once()
    mock_pub_incident.assert_called_once()
    assert mock_pub_alert.call_args[0][3] == "critical"


@patch("worker.tasks.weapon_task.get_tenant_setting", return_value=0.55)
@patch("worker.tasks.weapon_task.detect_weapons")
@patch("worker.tasks.weapon_task.get_tenant_session")
@patch("worker.tasks.weapon_task.save_evidence_snapshot", return_value=("p/e.jpg", "abc"))
@patch("worker.tasks.weapon_task.publish_alert_created")
@patch("worker.tasks.weapon_task.publish_incident_created")
@patch("worker.tasks.weapon_task.decode_frame", return_value=_FRAME)
def test_blade_high_alert_and_incident(
    mock_decode, mock_pub_incident, mock_pub_alert, mock_evidence,
    mock_session, mock_detect, mock_setting,
):
    """Blade detected -> high alert + auto-incident."""
    conn = _mock_conn()
    mock_session.return_value.__enter__ = lambda s: conn
    mock_session.return_value.__exit__ = MagicMock(return_value=False)
    mock_detect.return_value = [{"weapon_type": "blade", "confidence": 0.76, "bbox": (30, 30, 80, 80)}]

    from worker.tasks.weapon_task import process_frame_job

    process_frame_job(_make_job())

    mock_pub_alert.assert_called_once()
    mock_pub_incident.assert_called_once()
    assert mock_pub_alert.call_args[0][3] == "high"


@patch("worker.tasks.weapon_task.get_tenant_setting", return_value=0.55)
@patch("worker.tasks.weapon_task.detect_weapons")
@patch("worker.tasks.weapon_task.get_tenant_session")
@patch("worker.tasks.weapon_task.save_evidence_snapshot", return_value=("p/e.jpg", "abc"))
@patch("worker.tasks.weapon_task.publish_alert_created")
@patch("worker.tasks.weapon_task.publish_incident_created")
@patch("worker.tasks.weapon_task.decode_frame", return_value=_FRAME)
def test_blunt_medium_alert_no_incident(
    mock_decode, mock_pub_incident, mock_pub_alert, mock_evidence,
    mock_session, mock_detect, mock_setting,
):
    """Blunt weapon detected -> medium alert, NO auto-incident."""
    conn = _mock_conn()
    mock_session.return_value.__enter__ = lambda s: conn
    mock_session.return_value.__exit__ = MagicMock(return_value=False)
    mock_detect.return_value = [{"weapon_type": "blunt", "confidence": 0.61, "bbox": (20, 20, 60, 60)}]

    from worker.tasks.weapon_task import process_frame_job

    process_frame_job(_make_job())

    mock_pub_alert.assert_called_once()
    mock_pub_incident.assert_not_called()
    assert mock_pub_alert.call_args[0][3] == "medium"


@patch("worker.tasks.weapon_task.get_tenant_setting", return_value=0.55)
@patch("worker.tasks.weapon_task.detect_weapons")
@patch("worker.tasks.weapon_task.get_tenant_session")
@patch("worker.tasks.weapon_task.publish_alert_created")
@patch("worker.tasks.weapon_task.decode_frame", return_value=_FRAME)
def test_no_detection_no_write(mock_decode, mock_pub_alert, mock_session, mock_detect, mock_setting):
    """No weapons detected -> no alert."""
    mock_detect.return_value = []
    conn = _mock_conn()
    mock_session.return_value.__enter__ = lambda s: conn
    mock_session.return_value.__exit__ = MagicMock(return_value=False)

    from worker.tasks.weapon_task import process_frame_job

    process_frame_job(_make_job())
    mock_pub_alert.assert_not_called()


@patch("worker.tasks.weapon_task.publish_alert_created")
def test_module_not_enabled_returns_early(mock_pub_alert):
    from worker.tasks.weapon_task import process_frame_job

    process_frame_job(_make_job(modules=["lpr"]))
    mock_pub_alert.assert_not_called()
