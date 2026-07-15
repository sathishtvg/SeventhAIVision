"""Behavior Analysis pipeline tests (plan Phase 3)."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

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


def _make_job(modules=("behavior",)) -> FrameJob:
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


@patch("worker.tasks.behavior_task.publish_alert_created")
def test_module_not_enabled_returns_early(mock_pub_alert):
    from worker.tasks.behavior_task import process_frame_job

    process_frame_job(_make_job(modules=["lpr"]))
    mock_pub_alert.assert_not_called()


@patch("worker.tasks.behavior_task.get_tenant_setting", side_effect=lambda conn, tid, key: 30 if "dwell" in key else 120)
@patch("worker.tasks.behavior_task._detect_persons_with_pose")
@patch("worker.tasks.behavior_task._fetch_active_zones")
@patch("worker.tasks.behavior_task.get_breach_tracker")
@patch("worker.tasks.behavior_task.get_tenant_session")
@patch("worker.tasks.behavior_task.publish_alert_created")
@patch("worker.tasks.behavior_task.decode_frame", return_value=_FRAME)
def test_no_persons_no_write(
    mock_decode, mock_pub_alert, mock_session, mock_tracker, mock_zones, mock_detect, mock_setting
):
    """No persons in frame -> no writes."""
    mock_detect.return_value = []
    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    mock_session.return_value.__enter__ = lambda s: conn
    mock_session.return_value.__exit__ = MagicMock(return_value=False)
    mock_zones.return_value = []

    from worker.tasks.behavior_task import process_frame_job

    process_frame_job(_make_job())
    mock_pub_alert.assert_not_called()


@patch("worker.tasks.behavior_task.get_tenant_setting", side_effect=lambda conn, tid, key: 30 if "dwell" in key else 120)
@patch("worker.tasks.behavior_task._detect_persons_with_pose")
@patch("worker.tasks.behavior_task._fetch_active_zones")
@patch("worker.tasks.behavior_task.get_breach_tracker")
@patch("worker.tasks.behavior_task.get_tenant_session")
@patch("worker.tasks.behavior_task.save_evidence_snapshot", return_value=("p/e.jpg", "abc"))
@patch("worker.tasks.behavior_task.publish_alert_created")
@patch("worker.tasks.behavior_task.publish_incident_created")
@patch("worker.tasks.behavior_task.decode_frame", return_value=_FRAME)
def test_loitering_beyond_dwell_alert_and_no_incident(
    mock_decode, mock_pub_incident, mock_pub_alert, mock_evidence,
    mock_session, mock_tracker_factory, mock_zones, mock_detect, mock_setting,
):
    """Person in zone for > loitering_dwell_seconds -> loitering alert (medium, no incident)."""
    from shapely.geometry import Polygon

    zone = {
        "id": uuid.uuid4(),
        "polygon": Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]),
        "severity": "medium",
    }
    mock_zones.return_value = [zone]

    tracker = MagicMock()
    # register returns is_new=True, dwell=45 (exceeds loitering_dwell_seconds=30)
    tracker.register.return_value = (True, 45.0)
    mock_tracker_factory.return_value = tracker

    # Person at center of frame — foot point (cx, fy) = (0.5, ~0.958) inside polygon.
    # Frame: 640x480 -> foot_cx = (160+480)/(2*640) = 0.5, foot_fy = 460/480 ≈ 0.958
    # y2=460 (not 480) because shapely .within() is strict interior — a point exactly
    # on the boundary (y=1.0) returns False.
    mock_detect.return_value = [{"bbox": (160, 100, 480, 460), "confidence": 0.85, "keypoints": None}]

    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = lambda s: cursor
    cursor.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    mock_session.return_value.__enter__ = lambda s: conn
    mock_session.return_value.__exit__ = MagicMock(return_value=False)

    with patch("worker.tasks.behavior_task._classify_behaviors", return_value=[]):
        from worker.tasks.behavior_task import process_frame_job
        process_frame_job(_make_job())

    mock_pub_alert.assert_called()
    # loitering is medium severity — not in AUTO_INCIDENT_BEHAVIORS
    mock_pub_incident.assert_not_called()
    alert_module = mock_pub_alert.call_args[0][2]
    assert alert_module == "behavior"


@patch("worker.tasks.behavior_task.get_tenant_setting", side_effect=lambda conn, tid, key: 30 if "dwell" in key else 120)
@patch("worker.tasks.behavior_task._detect_persons_with_pose")
@patch("worker.tasks.behavior_task._fetch_active_zones")
@patch("worker.tasks.behavior_task.get_breach_tracker")
@patch("worker.tasks.behavior_task.get_tenant_session")
@patch("worker.tasks.behavior_task.save_evidence_snapshot", return_value=("p/e.jpg", "abc"))
@patch("worker.tasks.behavior_task.publish_alert_created")
@patch("worker.tasks.behavior_task.publish_incident_created")
@patch("worker.tasks.behavior_task.decode_frame", return_value=_FRAME)
def test_aggression_critical_alert_and_incident(
    mock_decode, mock_pub_incident, mock_pub_alert, mock_evidence,
    mock_session, mock_tracker_factory, mock_zones, mock_detect, mock_setting,
):
    """Aggression detected -> critical alert + auto-incident."""
    mock_zones.return_value = []  # no zones so loitering can't fire
    tracker = MagicMock()
    mock_tracker_factory.return_value = tracker

    mock_detect.return_value = [
        {"bbox": (100, 100, 200, 400), "confidence": 0.9, "keypoints": None},
        {"bbox": (110, 100, 210, 400), "confidence": 0.88, "keypoints": None},
    ]

    conn = MagicMock()
    cursor = MagicMock()
    cursor.__enter__ = lambda s: cursor
    cursor.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    mock_session.return_value.__enter__ = lambda s: conn
    mock_session.return_value.__exit__ = MagicMock(return_value=False)

    # force _classify_behaviors to return aggression
    with patch("worker.tasks.behavior_task._classify_behaviors") as mock_classify:
        mock_classify.return_value = [("aggression", mock_detect.return_value[0])]
        from worker.tasks.behavior_task import process_frame_job
        process_frame_job(_make_job())

    mock_pub_alert.assert_called()
    mock_pub_incident.assert_called()  # aggression IS in AUTO_INCIDENT_BEHAVIORS


def test_classify_behaviors_running_heuristic():
    """Running heuristic fires when ankles are visible + person takes large bbox height."""
    from worker.tasks.behavior_task import _classify_behaviors
    from worker.models.behavior_model import KP_LEFT_ANKLE, KP_RIGHT_ANKLE

    # Build minimal 17-keypoint list with high-confidence ankles at index 15+16
    keypoints = [[0.0, 0.0, 0.0]] * 17
    keypoints[KP_LEFT_ANKLE] = [300.0, 400.0, 0.8]   # left ankle conf = 0.8
    keypoints[KP_RIGHT_ANKLE] = [320.0, 400.0, 0.75]  # right ankle conf = 0.75

    # person bbox height = 480-50 = 430 in a 480px frame -> 430/480 = 0.896 > 0.3
    persons = [{"bbox": (100, 50, 300, 480), "confidence": 0.9, "keypoints": keypoints}]
    behaviors = _classify_behaviors(persons, frame_w=640, frame_h=480)
    assert any(bt == "running" for bt, _ in behaviors)


def test_classify_behaviors_aggression_overlap():
    """Aggression fires when two persons have overlapping bboxes."""
    from worker.tasks.behavior_task import _classify_behaviors

    persons = [
        {"bbox": (100, 100, 200, 400), "confidence": 0.9, "keypoints": None},
        {"bbox": (150, 120, 250, 420), "confidence": 0.88, "keypoints": None},  # overlaps first
    ]
    behaviors = _classify_behaviors(persons, frame_w=640, frame_h=480)
    assert any(bt == "aggression" for bt, _ in behaviors)


def test_classify_behaviors_no_overlap_no_aggression():
    """Non-overlapping bboxes don't trigger aggression."""
    from worker.tasks.behavior_task import _classify_behaviors

    persons = [
        {"bbox": (10, 10, 100, 200), "confidence": 0.9, "keypoints": None},
        {"bbox": (400, 10, 500, 200), "confidence": 0.88, "keypoints": None},
    ]
    behaviors = _classify_behaviors(persons, frame_w=640, frame_h=480)
    assert not any(bt == "aggression" for bt, _ in behaviors)
