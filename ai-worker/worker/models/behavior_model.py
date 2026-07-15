"""Behavior Analysis model (plan Phase 3).

Uses YOLOv8s-pose (COCO pose, 17-keypoint skeleton) for running and aggression
detection. Loitering and tailgating use the existing person bbox + BreachTracker
pattern — no separate weight needed for those behaviors.

Weight: standard `yolov8s-pose.pt` from Ultralytics (downloads automatically on
first run, no custom training required — COCO pose includes all the person
keypoints needed for motion analysis).
"""

import os
from functools import lru_cache

from ultralytics import YOLO

from worker.device import resolve_device

BEHAVIOR_MODEL_VERSION = "yolov8s-pose-2026.06"
BEHAVIOR_WEIGHT = os.environ.get("BEHAVIOR_WEIGHT_PATH", "yolov8s-pose.pt")

# Keypoint indices (COCO skeleton, 0-based)
KP_LEFT_ANKLE = 15
KP_RIGHT_ANKLE = 16
KP_LEFT_HIP = 11
KP_RIGHT_HIP = 12

BEHAVIOR_TYPES = {"loitering", "running", "aggression", "tailgating"}
AUTO_INCIDENT_BEHAVIORS: set[str] = {"aggression", "tailgating"}

# Behavior severities
BEHAVIOR_SEVERITIES: dict[str, str] = {
    "loitering": "medium",
    "running": "low",
    "aggression": "critical",
    "tailgating": "high",
}


@lru_cache(maxsize=1)
def get_pose_model() -> YOLO:
    model = YOLO(BEHAVIOR_WEIGHT)
    model.to(resolve_device())
    return model
