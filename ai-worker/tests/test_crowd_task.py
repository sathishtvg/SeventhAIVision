"""Crowd Density Detection pipeline tests (plan Phase 3)."""

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


def _make_job(modules=("crowd",)) -> FrameJob:
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


def _make_zone(max_capacity=5, severity="high"):
    from shapely.geometry import Polygon

    return {
        "id": uuid.uuid4(),
        "polygon": Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]),
        "max_capacity": max_capacity,
        "severity": severity,
    }


@patch("worker.tasks.crowd_task.get_tenant_setting", side_effect=lambda conn, tid, key: 0.8 if "ratio" in key else 60)
@patch("worker.tasks.crowd_task._fetch_active_zones")
@patch("worker.tasks.crowd_task._count_persons_in_zone")
@patch("worker.tasks.crowd_task.get_breach_tracker")
@patch("worker.tasks.crowd_task.get_tenant_session")
@patch("worker.tasks.crowd_task.save_evidence_snapshot", return_value=("p/e.jpg", "abc"))
@patch("worker.tasks.crowd_task.publish_alert_created")
@patch("worker.tasks.crowd_task.publish_incident_created")
@patch("worker.tasks.crowd_task.decode_frame", return_value=_FRAME)
def test_threshold_exceeded_high_zone_alert_and_incident(
    mock_decode, mock_pub_incident, mock_pub_alert, mock_evidence,
    mock_session, mock_tracker_factory, mock_count_in_zone,
    mock_fetch_zones, mock_setting,
):
    """person_count >= threshold in high zone -> alert + auto-incident."""
    zone = _make_zone(max_capacity=5, severity="high")
    mock_fetch_zones.return_value = [zone]

    tracker = MagicMock()
    tracker.register.return_value = (True, 0)
    mock_tracker_factory.return_value = tracker

    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = lambda s: cursor
    cursor.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    mock_session.return_value.__enter__ = lambda s: conn
    mock_session.return_value.__exit__ = MagicMock(return_value=False)

    # 5 persons in zone, max_capacity=5 -> density_ratio=1.0 >= 0.8 threshold
    persons_in_zone = [{"bbox": (10, 10, 50, 100), "confidence": 0.9}] * 5
    mock_count_in_zone.return_value = persons_in_zone

    # patch the predict path for the raw persons list
    with patch("worker.tasks.crowd_task.get_person_detector") as mock_detector:
        mock_results = MagicMock()
        mock_results.boxes = [MagicMock(xyxy=[MagicMock(tolist=lambda: [10, 10, 50, 100])], conf=[MagicMock(__getitem__=lambda s, i: 0.9)]) for _ in range(5)]
        mock_detector.return_value.predict.return_value = [mock_results]
        with patch("worker.tasks.crowd_task.PERSON_CLASS_ID", 0):
            from worker.tasks.crowd_task import process_frame_job
            process_frame_job(_make_job())

    mock_pub_alert.assert_called_once()
    mock_pub_incident.assert_called_once()
    assert mock_pub_alert.call_args[0][2] == "crowd"


@patch("worker.tasks.crowd_task.get_tenant_setting", side_effect=lambda conn, tid, key: 0.8 if "ratio" in key else 60)
@patch("worker.tasks.crowd_task._fetch_active_zones")
@patch("worker.tasks.crowd_task._count_persons_in_zone")
@patch("worker.tasks.crowd_task.get_breach_tracker")
@patch("worker.tasks.crowd_task.get_tenant_session")
@patch("worker.tasks.crowd_task.publish_alert_created")
@patch("worker.tasks.crowd_task.decode_frame", return_value=_FRAME)
def test_below_threshold_no_alert(
    mock_decode, mock_pub_alert, mock_session, mock_tracker_factory,
    mock_count_in_zone, mock_fetch_zones, mock_setting,
):
    """density_ratio < threshold -> no alert, no write."""
    zone = _make_zone(max_capacity=10, severity="medium")
    mock_fetch_zones.return_value = [zone]
    tracker = MagicMock()
    mock_tracker_factory.return_value = tracker

    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    mock_session.return_value.__enter__ = lambda s: conn
    mock_session.return_value.__exit__ = MagicMock(return_value=False)

    # 2 persons in zone with max_capacity=10 -> density 0.2 < 0.8 threshold
    mock_count_in_zone.return_value = [{"bbox": (1, 1, 2, 2), "confidence": 0.8}] * 2

    with patch("worker.tasks.crowd_task.get_person_detector") as mock_detector:
        mock_results = MagicMock()
        mock_results.boxes = [MagicMock(xyxy=[MagicMock(tolist=lambda: [1, 1, 2, 2])], conf=[MagicMock(__getitem__=lambda s, i: 0.8)]) for _ in range(2)]
        mock_detector.return_value.predict.return_value = [mock_results]
        with patch("worker.tasks.crowd_task.PERSON_CLASS_ID", 0):
            from worker.tasks.crowd_task import process_frame_job
            process_frame_job(_make_job())

    mock_pub_alert.assert_not_called()


@patch("worker.tasks.crowd_task.publish_alert_created")
def test_module_not_enabled_returns_early(mock_pub_alert):
    """crowd not in ai_modules_enabled -> immediate return."""
    from worker.tasks.crowd_task import process_frame_job

    process_frame_job(_make_job(modules=["lpr"]))
    mock_pub_alert.assert_not_called()
