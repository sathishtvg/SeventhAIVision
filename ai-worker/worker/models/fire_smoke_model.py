"""Fire/Smoke Detection model loader with two-tier strategy:

Tier 1 — Custom fine-tuned weight at FIRE_SMOKE_WEIGHT_PATH (preferred).
          Class mapping: {0: fire, 1: smoke}.

Tier 2 — YOLOWorld open-vocabulary fallback.
          Ultralytics YOLOWorld lets you pass arbitrary class names at runtime
          via model.set_classes(['fire', 'smoke']).  After set_classes, the
          model's prediction class IDs are 0=fire and 1=smoke — exactly the
          same mapping as FIRE_SMOKE_CLASSES — so fire_smoke_task.py requires
          no changes when the fallback is active.

          YOLOWorld models are auto-downloaded by the Ultralytics library when
          called by model name ('yolov8s-worldv2.pt' or 'yolov8s-world.pt').
          The URL pattern for these models changed between Ultralytics releases,
          so manual URL download is not used — we rely on the library's own
          download logic (same as how yolov8s.pt is downloaded for intrusion/crowd).

OIv7 was intentionally not used here because OIv7 has no fire or smoke class;
only fire-adjacent objects (190='Fire hydrant', 191='Fireplace') which are not
useful for fire/smoke detection.
"""

import logging
import os

from worker.device import resolve_device

logger = logging.getLogger(__name__)

FIRE_SMOKE_MODEL_VERSION = "yolov8s-fire-smoke-2026.06"
FIRE_SMOKE_WEIGHT_PATH = os.environ.get(
    "FIRE_SMOKE_WEIGHT_PATH", "/app/ai-worker/models/yolov8s-fire-smoke.pt"
)

# YOLOWorld model names to try in order (Ultralytics auto-downloads by model name)
YOLO_WORLD_MODELS = ["yolov8s-worldv2.pt", "yolov8s-world.pt"]
YOLO_WORLD_CLASSES = ["fire", "smoke"]  # → class 0 = fire, class 1 = smoke

FIRE_SMOKE_CLASSES: dict[int, str] = {
    0: "fire",
    1: "smoke",
}

_MODEL_UNAVAILABLE = object()
_fire_smoke_detector = None


def get_fire_smoke_detector():
    """Returns the fire/smoke detector or None in no-op mode.

    When the fallback (YOLOWorld) is active, set_classes() has already been
    called so predictions already use 0=fire / 1=smoke — the task layer
    requires no change.
    """
    global _fire_smoke_detector

    if _fire_smoke_detector is None:
        if os.path.exists(FIRE_SMOKE_WEIGHT_PATH):
            # Tier 1: custom fine-tuned model
            from ultralytics import YOLO
            model = YOLO(FIRE_SMOKE_WEIGHT_PATH)
            model.to(resolve_device())
            _fire_smoke_detector = model
            logger.info("Loaded custom fire/smoke model from %s", FIRE_SMOKE_WEIGHT_PATH)
        else:
            # Tier 2: YOLOWorld auto-download (Ultralytics manages the cache)
            logger.info(
                "Fire/smoke weight not found at %s. "
                "Trying YOLOWorld fallback models %s with classes %s.",
                FIRE_SMOKE_WEIGHT_PATH,
                YOLO_WORLD_MODELS,
                YOLO_WORLD_CLASSES,
            )
            _fire_smoke_detector = _MODEL_UNAVAILABLE
            for model_name in YOLO_WORLD_MODELS:
                try:
                    from ultralytics import YOLO
                    # Passing just the model name (not a path) triggers auto-download
                    model = YOLO(model_name)
                    model.set_classes(YOLO_WORLD_CLASSES)  # → 0=fire, 1=smoke
                    model.to(resolve_device())
                    _fire_smoke_detector = model
                    logger.info(
                        "YOLOWorld fallback active (%s) — class 0='fire', class 1='smoke'",
                        model_name,
                    )
                    break
                except Exception as exc:
                    logger.warning("YOLOWorld model '%s' failed: %s — trying next", model_name, exc)

            if _fire_smoke_detector is _MODEL_UNAVAILABLE:
                logger.warning(
                    "All YOLOWorld fallbacks failed — fire/smoke worker in no-op mode. "
                    "Place yolov8s-fire-smoke.pt at %s to enable fire/smoke detection.",
                    FIRE_SMOKE_WEIGHT_PATH,
                )

    return None if _fire_smoke_detector is _MODEL_UNAVAILABLE else _fire_smoke_detector
