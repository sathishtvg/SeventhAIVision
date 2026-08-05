"""Camera tampering detection pipeline tests."""

import time
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

# Minimal 4×4 black BGR frame returned by the decode_frame mock
_FRAME = np.zeros((4, 4, 3), dtype=np.uint8)


def _make_job(camera_id=None, modules=("tampering",)) -> FrameJob:
    return FrameJob(
        job_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        camera_id=camera_id or uuid.uuid4(),
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

@patch("worker.tasks.tampering_task.publish_alert_created")
def test_module_not_enabled_returns_early(mock_alert):
    from worker.tasks.tampering_task import process_frame_job
    process_frame_job(_make_job(modules=["lpr"]))
    mock_alert.assert_not_called()


@patch("worker.tasks.tampering_task.publish_alert_created")
@patch("worker.tasks.tampering_task.decode_frame", return_value=_FRAME)
def test_first_frame_stores_reference_no_alert(mock_decode, mock_alert):
    """First frame for a new camera stores the reference and returns without alerting."""
    from worker.tasks.tampering_task import process_frame_job
    process_frame_job(_make_job())
    mock_alert.assert_not_called()


@patch("worker.tasks.tampering_task.publish_alert_created")
@patch("worker.tasks.tampering_task.detect_tampering", return_value=None)
@patch("worker.tasks.tampering_task.decode_frame", return_value=_FRAME)
def test_second_frame_no_tampering_no_alert(mock_decode, mock_detect, mock_alert):
    """When detect_tampering returns None on the second frame, no alert is fired."""
    from worker.tasks.tampering_task import process_frame_job
    camera_id = uuid.uuid4()
    job = _make_job(camera_id=camera_id)
    process_frame_job(job)  # first call — stores reference, returns early
    process_frame_job(job)  # second call — detect_tampering → None → return
    mock_alert.assert_not_called()


@patch("worker.tasks.tampering_task.publish_incident_created")
@patch("worker.tasks.tampering_task.publish_alert_created")
@patch("worker.storage.save_evidence_snapshot", return_value=("path/e.jpg", "abc123"))
@patch("worker.tasks.tampering_task.get_tenant_setting", return_value=0.3)
@patch("worker.tasks.tampering_task.get_tenant_session")
@patch(
    "worker.tasks.tampering_task.detect_tampering",
    return_value={"tampering_type": "blocked", "score": 0.9, "reason": "histogram low"},
)
@patch("worker.tasks.tampering_task.decode_frame", return_value=_FRAME)
def test_tampering_detected_fires_alert_and_incident(
    mock_decode, mock_detect, mock_session, mock_setting, mock_evidence, mock_alert, mock_incident,
):
    """Confirmed tampering above threshold → high-severity alert + auto-incident."""
    _setup_conn(mock_session)

    from worker.tasks.tampering_task import process_frame_job
    camera_id = uuid.uuid4()
    job = _make_job(camera_id=camera_id)
    process_frame_job(job)  # first call — stores reference
    process_frame_job(job)  # second call — detect_tampering fires

    mock_alert.assert_called_once()
    mock_incident.assert_called_once()
    # publish_alert_created is (tenant_id, alert_id, module_type, severity,
    # camera_id, title) — index rather than unpack so a trailing argument
    # can be added without breaking this assertion.
    _args = mock_alert.call_args[0]
    module_type, severity = _args[2], _args[3]
    assert module_type == "tampering"
    assert severity == "high"


@patch("worker.tasks.tampering_task.publish_alert_created")
@patch("worker.tasks.tampering_task.get_tenant_setting", return_value=0.95)
@patch("worker.tasks.tampering_task.get_tenant_session")
@patch(
    "worker.tasks.tampering_task.detect_tampering",
    return_value={"tampering_type": "blocked", "score": 0.4, "reason": "test"},
)
@patch("worker.tasks.tampering_task.decode_frame", return_value=_FRAME)
def test_score_below_threshold_no_alert(mock_decode, mock_detect, mock_session, mock_setting, mock_alert):
    """A detection score below the configured threshold suppresses the alert."""
    _setup_conn(mock_session)

    from worker.tasks.tampering_task import process_frame_job
    camera_id = uuid.uuid4()
    job = _make_job(camera_id=camera_id)
    process_frame_job(job)  # stores reference
    process_frame_job(job)  # score=0.4 < threshold=0.95 → suppressed

    mock_alert.assert_not_called()


@patch("worker.tasks.tampering_task.publish_alert_created")
@patch(
    "worker.tasks.tampering_task.detect_tampering",
    return_value={"tampering_type": "blocked", "score": 0.9, "reason": "test"},
)
@patch("worker.tasks.tampering_task.decode_frame", return_value=_FRAME)
def test_cooldown_suppresses_repeat(mock_decode, mock_detect, mock_alert):
    """A second alert within ALERT_COOLDOWN seconds is suppressed."""
    import worker.tasks.tampering_task as mod

    camera_id = uuid.uuid4()
    camera_key = str(camera_id)
    # Pre-populate reference frame and mark camera as just-alerted
    mod._reference_frames[camera_key] = (_FRAME.copy(), time.time())
    mod._last_alert_ts[camera_key] = time.time()

    from worker.tasks.tampering_task import process_frame_job
    process_frame_job(_make_job(camera_id=camera_id))

    mock_alert.assert_not_called()


# ── Pure function tests ───────────────────────────────────────────────────────

def test_compute_hist_similarity_identical_frames():
    """Bhattacharyya correlation of a frame with itself should be ≈ 1.0."""
    from worker.models.tampering_model import compute_hist_similarity
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    score = compute_hist_similarity(frame, frame.copy())
    assert score > 0.99


def test_compute_blur_variance_uniform_frame_is_near_zero():
    """A solid-colour (uniform) frame is maximally blurry — Laplacian variance ≈ 0."""
    from worker.models.tampering_model import compute_blur_variance
    frame = np.full((10, 10, 3), 128, dtype=np.uint8)
    assert compute_blur_variance(frame) < 1.0


def test_compute_blur_variance_checkerboard_is_high():
    """A checkerboard pattern has many sharp edges — high Laplacian variance."""
    from worker.models.tampering_model import compute_blur_variance
    pattern = np.tile(np.array([[0, 255], [255, 0]], dtype=np.uint8), (5, 5))
    frame = np.stack([pattern] * 3, axis=-1)
    assert compute_blur_variance(frame) > 100.0
