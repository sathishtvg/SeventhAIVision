"""Abandoned object detection using MOG2 background subtraction.

Approach:
1. Maintain a per-camera MOG2 background model.
2. Each frame: extract foreground mask (moving objects).
3. Track contours across frames with a simple centroid-distance tracker.
4. If a contour's centroid stops moving AND is still present after
   `abandoned.dwell_seconds` (tenant setting), fire an alert.

All OpenCV — no model weights required.
"""

import os
import time
from collections import defaultdict

import cv2
import numpy as np

ABANDONED_MODEL_VERSION = "opencv-mog2-v1"

# Minimum contour area in pixels to consider (filters noise)
MIN_CONTOUR_AREA = int(os.environ.get("ABANDONED_MIN_AREA", "1500"))
# Pixels threshold for "same object" matching between frames
CENTROID_MATCH_DIST = int(os.environ.get("ABANDONED_CENTROID_DIST", "40"))

# Per-camera state — stored in module-level dict (single process, single thread per module)
_bg_subtractors: dict[str, cv2.BackgroundSubtractorMOG2] = {}
_object_tracker: dict[str, dict[str, dict]] = defaultdict(dict)  # camera_id → {track_id → {cx, cy, first_seen_ts, last_seen_ts}}
_track_counter: dict[str, int] = defaultdict(int)


def _get_bg_subtractor(camera_id: str) -> cv2.BackgroundSubtractorMOG2:
    if camera_id not in _bg_subtractors:
        _bg_subtractors[camera_id] = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=40, detectShadows=False
        )
    return _bg_subtractors[camera_id]


def detect_abandoned(
    frame: np.ndarray,
    camera_id: str,
    dwell_threshold_seconds: float = 30.0,
) -> list[dict]:
    """Return list of abandoned-object detections for this frame.

    Each dict has: object_class, dwell_seconds, bbox (x1,y1,x2,y2).
    """
    bsub = _get_bg_subtractor(camera_id)
    fg_mask = bsub.apply(frame)
    # Morphological clean-up
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    now = time.time()
    active_track_ids: set[str] = set()

    # Classify contours as active objects
    current_centroids: list[tuple[int, int, int, int, int, int]] = []  # cx,cy,x1,y1,x2,y2
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < MIN_CONTOUR_AREA:
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        cx, cy = x + w // 2, y + h // 2
        current_centroids.append((cx, cy, x, y, x + w, y + h))

    tracker = _object_tracker[camera_id]
    # Match to existing tracks
    matched_track_ids: set[str] = set()
    for cx, cy, x1, y1, x2, y2 in current_centroids:
        best_id = None
        best_dist = float("inf")
        for tid, info in tracker.items():
            dist = ((cx - info["cx"]) ** 2 + (cy - info["cy"]) ** 2) ** 0.5
            if dist < CENTROID_MATCH_DIST and dist < best_dist:
                best_dist = dist
                best_id = tid
        if best_id:
            tracker[best_id]["cx"] = cx
            tracker[best_id]["cy"] = cy
            tracker[best_id]["last_seen_ts"] = now
            tracker[best_id]["bbox"] = (x1, y1, x2, y2)
            matched_track_ids.add(best_id)
            active_track_ids.add(best_id)
        else:
            _track_counter[camera_id] += 1
            new_id = f"{camera_id[:8]}_{_track_counter[camera_id]}"
            tracker[new_id] = {
                "cx": cx, "cy": cy,
                "first_seen_ts": now, "last_seen_ts": now,
                "bbox": (x1, y1, x2, y2),
            }
            active_track_ids.add(new_id)

    # Prune stale tracks not seen for >5s
    stale = [tid for tid, info in tracker.items() if now - info["last_seen_ts"] > 5.0]
    for tid in stale:
        del tracker[tid]

    # Fire detections for objects dwelling long enough
    detections: list[dict] = []
    for tid in active_track_ids:
        info = tracker[tid]
        dwell = now - info["first_seen_ts"]
        if dwell >= dwell_threshold_seconds:
            x1, y1, x2, y2 = info["bbox"]
            detections.append({
                "object_class": "unknown_object",
                "dwell_seconds": round(dwell, 1),
                "bbox": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
            })

    return detections
