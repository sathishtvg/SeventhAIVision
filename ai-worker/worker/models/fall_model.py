"""Slip/fall detection using YOLOv8n-pose skeleton estimation.

Approach:
- Load YOLOv8n-pose (stock ultralytics weight — no custom training needed).
- Extract body keypoints per detected person.
- Apply heuristic: if the head keypoint Y-coordinate is LOWER (higher pixel row)
  than both hip keypoints, the person is likely horizontal (fallen).
- Alternatively, if the bounding-box aspect ratio is wide (width >> height),
  the person is prone.

The pose weight yolov8n-pose.pt is auto-downloaded by ultralytics on first use.
No separate pre-placed weight file needed for this module.
"""

import logging
import os

import numpy as np

FALL_MODEL_VERSION = "yolov8n-pose-v1"
FALL_CONFIDENCE_THRESHOLD = float(os.environ.get("FALL_CONFIDENCE_THRESHOLD", "0.45"))

_model = None
_log = logging.getLogger(__name__)


def get_fall_detector():
    global _model
    if _model is None:
        try:
            from ultralytics import YOLO
            _model = YOLO("yolov8n-pose.pt")
            _log.info("YOLOv8n-pose model loaded for fall detection")
        except Exception as exc:
            _log.warning("Could not load YOLOv8n-pose: %s — fall detection disabled", exc)
            _model = None
    return _model


def _is_fallen(keypoints: np.ndarray, bbox_xyxy: list) -> tuple[bool, float]:
    """Return (is_fallen, confidence_score).

    keypoints: shape (N, 3) — (x, y, visibility) for each of the 17 COCO keypoints.
    Keypoint indices (COCO order):
        0=nose, 1=left_eye, 2=right_eye, 3=left_ear, 4=right_ear,
        5=left_shoulder, 6=right_shoulder, 7=left_elbow, 8=right_elbow,
        9=left_wrist, 10=right_wrist, 11=left_hip, 12=right_hip,
        13=left_knee, 14=right_knee, 15=left_ankle, 16=right_ankle
    """
    # Bounding box aspect ratio check (prone body is wide)
    x1, y1, x2, y2 = bbox_xyxy
    w, h = x2 - x1, y2 - y1
    aspect_ratio = w / max(h, 1)
    if aspect_ratio > 2.0:
        return True, min(1.0, (aspect_ratio - 2.0) / 2.0 + 0.5)

    if keypoints is None or len(keypoints) < 13:
        return False, 0.0

    # Visibility check — only use keypoints with confidence > 0.3
    nose_vis = keypoints[0, 2]
    lhip_vis = keypoints[11, 2]
    rhip_vis = keypoints[12, 2]

    if nose_vis < 0.3 or (lhip_vis < 0.3 and rhip_vis < 0.3):
        return False, 0.0

    nose_y = keypoints[0, 1]  # lower row = larger value
    hip_y = max(keypoints[11, 1], keypoints[12, 1])

    # If nose Y ≥ hip Y (nose is at or below hip level in the image), person is fallen
    if nose_y >= hip_y * 0.95:
        confidence = min(1.0, (nose_y - hip_y * 0.95) / (hip_y * 0.15 + 1))
        return True, round(confidence, 4)

    return False, 0.0


def detect_falls(frame: np.ndarray) -> list[dict]:
    """Return list of fall detections.

    Each dict: fall_confidence, pose_keypoints (list of [x,y,vis]), person_bbox.
    """
    detector = get_fall_detector()
    if detector is None:
        return []

    results = detector.predict(frame, conf=FALL_CONFIDENCE_THRESHOLD, verbose=False)[0]
    detections = []

    for i, box in enumerate(results.boxes):
        x1, y1, x2, y2 = [int(v) for v in box.xyxy[0].tolist()]
        bbox_xyxy = [x1, y1, x2, y2]

        kp = None
        if results.keypoints is not None and i < len(results.keypoints):
            kp = results.keypoints[i].xy[0].cpu().numpy() if hasattr(results.keypoints[i], 'xy') else None
            if kp is not None:
                # Reconstruct (x, y, vis) — visibility from conf
                kp_conf = results.keypoints[i].conf[0].cpu().numpy() if hasattr(results.keypoints[i], 'conf') else np.ones(len(kp))
                kp = np.column_stack([kp, kp_conf])

        fallen, fall_conf = _is_fallen(kp, bbox_xyxy)
        if fallen:
            detections.append({
                "fall_confidence": fall_conf,
                "pose_keypoints": kp.tolist() if kp is not None else None,
                "person_bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
            })

    return detections
