"""LPR model loader with two-tier strategy:

Tier 1 — Custom fine-tuned model (preferred):
  Place a Singapore-trained YOLOv8s weight at LPR_WEIGHT_PATH.
  The model is used as-is; no class filtering needed.

Tier 2 — Open Images v7 auto-fallback (no auth, no training required):
  yolov8s-oiv7.pt is trained by Ultralytics on 601 OI classes including
  "Vehicle registration plate" (works for Singapore plates).  The model
  is downloaded once from GitHub releases and cached at OIV7_CACHE_PATH
  (inside the bind-mounted models/ directory so it survives restarts).
  PLATE_CLASS_FILTER is set to the OIv7 plate class ID so that
  detect_plates() filters for plates only.

Both models are heavy imports; loading is deferred to first use.
"""

import logging
import os
import urllib.request
from pathlib import Path

from worker.device import resolve_device

logger = logging.getLogger(__name__)

# ── Paths ──────────────────────────────────────────────────────────────────
LPR_WEIGHT_PATH = os.environ.get("LPR_WEIGHT_PATH", "/app/ai-worker/models/yolov8s-lpr.pt")
LPR_MODEL_VERSION = os.environ.get("LPR_MODEL_VERSION", "yolov8s-lpr-unversioned")

# OIv7 fallback — publicly downloadable, no auth needed
OIV7_CACHE_PATH = str(Path(LPR_WEIGHT_PATH).parent / "yolov8s-oiv7.pt")
OIV7_DOWNLOAD_URL = (
    "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s-oiv7.pt"
)
OIV7_PLATE_KEYWORD = "registration plate"  # substring in OIv7 class name

# ── Module-level state ──────────────────────────────────────────────────────
_MODEL_UNAVAILABLE = object()   # sentinel: both tiers failed
_plate_detector = None          # None = not yet attempted
PLATE_CLASS_FILTER: int | None = None   # set to OIv7 class ID when using tier-2
_ocr_engine = None


def _try_download_oiv7() -> str | None:
    """Download yolov8s-oiv7.pt from Ultralytics GitHub to the models directory.
    Returns the local path on success, None on failure."""
    dest = OIV7_CACHE_PATH
    if os.path.exists(dest):
        logger.info("OIv7 model already cached at %s", dest)
        return dest
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        logger.info("Downloading yolov8s-oiv7.pt from Ultralytics (no auth required)...")
        urllib.request.urlretrieve(OIV7_DOWNLOAD_URL, dest)
        size_mb = os.path.getsize(dest) / 1_048_576
        if size_mb < 5:
            logger.warning(
                "Downloaded file is only %.1f MB — likely an error response. Removing.", size_mb
            )
            os.unlink(dest)
            return None
        logger.info("Downloaded OIv7 model: %.1f MB → %s", size_mb, dest)
        return dest
    except Exception as exc:
        logger.warning("Could not download OIv7 fallback model: %s", exc)
        if os.path.exists(dest):
            os.unlink(dest)
        return None


def get_plate_detector():
    """Returns the loaded YOLO model (fine-tuned or OIv7), or None in no-op mode.

    Also sets the module-level PLATE_CLASS_FILTER when using OIv7 so that
    detect_plates() can restrict predictions to the plate class only.
    """
    global _plate_detector, PLATE_CLASS_FILTER

    if _plate_detector is None:
        # ── Tier 1: custom Singapore-trained model ───────────────────────
        if os.path.exists(LPR_WEIGHT_PATH):
            from ultralytics import YOLO

            model = YOLO(LPR_WEIGHT_PATH)
            model.to(resolve_device())
            _plate_detector = model
            PLATE_CLASS_FILTER = None   # fine-tuned model outputs plates only
            logger.info("Loaded custom Singapore LPR model from %s", LPR_WEIGHT_PATH)

        else:
            # ── Tier 2: Open Images v7 public fallback ───────────────────
            logger.info(
                "Singapore LPR weight not found at %s. "
                "Falling back to yolov8s-oiv7.pt (Open Images v7, publicly available). "
                "This detects all standard plate formats including Singapore plates. "
                "For production use, train or source a Singapore-specific model and "
                "place it at %s.",
                LPR_WEIGHT_PATH,
                LPR_WEIGHT_PATH,
            )
            oiv7_path = _try_download_oiv7()

            if oiv7_path:
                try:
                    from ultralytics import YOLO

                    model = YOLO(oiv7_path)
                    model.to(resolve_device())
                    # Find the "Vehicle registration plate" class in OIv7
                    cls_id = next(
                        (k for k, v in model.names.items() if OIV7_PLATE_KEYWORD in v.lower()),
                        None,
                    )
                    if cls_id is not None:
                        _plate_detector = model
                        PLATE_CLASS_FILTER = cls_id
                        logger.info(
                            "OIv7 model active — plate class: %d ('%s'). "
                            "Singapore plate format validation (SXX NNNN X) is applied in the pipeline.",
                            cls_id,
                            model.names[cls_id],
                        )
                    else:
                        logger.warning(
                            "OIv7 model loaded but '%s' class not found — no-op mode", OIV7_PLATE_KEYWORD
                        )
                        _plate_detector = _MODEL_UNAVAILABLE
                except Exception as exc:
                    logger.warning("Failed to load OIv7 model: %s — no-op mode", exc)
                    _plate_detector = _MODEL_UNAVAILABLE
            else:
                logger.warning("OIv7 download failed — LPR worker running in no-op mode")
                _plate_detector = _MODEL_UNAVAILABLE

    return None if _plate_detector is _MODEL_UNAVAILABLE else _plate_detector


def get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from paddleocr import PaddleOCR

        _ocr_engine = PaddleOCR(
            use_angle_cls=False,
            lang="en",
            det=False,
            rec=True,
            use_gpu=(resolve_device() == "cuda"),
        )
    return _ocr_engine
