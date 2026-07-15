"""Stock YOLOv8s, COCO `person` class (id 0) — no custom training needed
(confirmed: COCO already includes `person`, unlike LPR which needed a
fine-tune). Auto-downloaded by ultralytics' own official release mechanism on
first use, same trust level as `pip install ultralytics` itself — not a
third-party derived artifact like the LPR weight."""

import os

from worker.device import resolve_device

INTRUSION_WEIGHT_NAME = os.environ.get("INTRUSION_WEIGHT_NAME", "yolov8s.pt")
INTRUSION_MODEL_VERSION = os.environ.get("INTRUSION_MODEL_VERSION", "yolov8s-stock-coco")
PERSON_CLASS_ID = 0

_person_detector = None


def get_person_detector():
    global _person_detector
    if _person_detector is None:
        from ultralytics import YOLO

        detector = YOLO(INTRUSION_WEIGHT_NAME)
        detector.to(resolve_device())
        _person_detector = detector
    return _person_detector
