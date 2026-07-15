"""Crowd density model — reuses the stock YOLOv8s person detector already
used by intrusion detection (same weight, same PERSON_CLASS_ID=0). No separate
weight file needed; crowd counting is done by post-processing the person bbox
list returned by the shared detector."""

from worker.models.intrusion_model import PERSON_CLASS_ID, get_person_detector  # noqa: F401

CROWD_MODEL_VERSION = "yolov8s-crowd-2026.06"
