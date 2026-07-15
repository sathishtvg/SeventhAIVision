"""PPE Detection model loader with two-tier strategy:

Tier 1 — Custom fine-tuned weight at PPE_WEIGHT_PATH (preferred).
          Expected class mapping: {0: hard_hat, 1: safety_vest, 2: gloves,
          3: goggles, 4: mask, 5: safety_boots}.

Tier 2 — Open Images v7 public fallback (yolov8s-oiv7.pt), reusing the file
          already cached by the LPR worker. OIv7 class coverage:
            248 = Helmet       → hard_hat
            218 = Glove        → gloves
            220 = Goggles      → goggles
             56 = Boot         → safety_boots
            381 = Person       (used to group PPE items per person)
          NOTE: safety_vest has no OIv7 analogue. In fallback mode only
          hard_hat is required (vest violations are not detectable).

ACTIVE_PPE_CLASSES, ACTIVE_REQUIRED_PPE, PPE_CLASS_IDS, and PERSON_CLASS_ID
are populated at load time so detect_ppe() in ppe_task.py adapts automatically
to whichever tier is active.
"""

import logging
import os
from pathlib import Path

from worker.device import resolve_device

logger = logging.getLogger(__name__)

PPE_MODEL_VERSION = "yolov8s-ppe-2026.06"
PPE_WEIGHT_PATH = os.environ.get("PPE_WEIGHT_PATH", "/app/ai-worker/models/yolov8s-ppe.pt")

# Shared OIv7 cache (downloaded by LPR worker on first use)
OIV7_CACHE_PATH = str(Path(PPE_WEIGHT_PATH).parent / "yolov8s-oiv7.pt")
OIV7_DOWNLOAD_URL = (
    "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s-oiv7.pt"
)

# Fine-tuned model class mapping
PPE_CLASSES: dict[int, str] = {
    0: "hard_hat",
    1: "safety_vest",
    2: "gloves",
    3: "goggles",
    4: "mask",
    5: "safety_boots",
}
REQUIRED_PPE: set[str] = {"hard_hat", "safety_vest"}

# OIv7 class → our PPE item name (person is handled separately)
OIV7_PPE_MAP: dict[int, str] = {
    248: "hard_hat",     # Helmet
    218: "gloves",       # Glove
    220: "goggles",      # Goggles
    56:  "safety_boots", # Boot
}
OIV7_PERSON_CLASS = 381
OIV7_REQUIRED_PPE: set[str] = {"hard_hat"}   # vest not detectable via OIv7

# ── Runtime state (populated on first model load) ──────────────────────────
_MODEL_UNAVAILABLE = object()
_ppe_detector = None
ACTIVE_PPE_CLASSES: dict[int, str] = {}    # class_id → ppe_item for current tier
ACTIVE_REQUIRED_PPE: set[str] = set()      # required items for current tier
PPE_CLASS_IDS: list[int] | None = None     # None = all classes; list = OIv7 filter
PERSON_CLASS_ID: int | None = None         # None = "any unknown class is a person" (custom)
                                            # int  = explicit OIv7 person class (381)


def _try_download_oiv7() -> str | None:
    import urllib.request
    dest = OIV7_CACHE_PATH
    if os.path.exists(dest):
        return dest
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        logger.info("Downloading yolov8s-oiv7.pt from Ultralytics...")
        urllib.request.urlretrieve(OIV7_DOWNLOAD_URL, dest)
        if os.path.getsize(dest) < 5_000_000:
            os.unlink(dest)
            return None
        logger.info("Downloaded OIv7 model (%.1f MB)", os.path.getsize(dest) / 1_048_576)
        return dest
    except Exception as exc:
        logger.warning("OIv7 download failed: %s", exc)
        if os.path.exists(dest):
            os.unlink(dest)
        return None


def get_ppe_detector():
    """Returns the loaded detector or None in no-op mode.
    Also sets ACTIVE_PPE_CLASSES, ACTIVE_REQUIRED_PPE, PPE_CLASS_IDS,
    and PERSON_CLASS_ID for the task layer."""
    global _ppe_detector, ACTIVE_PPE_CLASSES, ACTIVE_REQUIRED_PPE, PPE_CLASS_IDS, PERSON_CLASS_ID

    if _ppe_detector is None:
        if os.path.exists(PPE_WEIGHT_PATH):
            # Tier 1: custom fine-tuned model
            from ultralytics import YOLO
            model = YOLO(PPE_WEIGHT_PATH)
            model.to(resolve_device())
            _ppe_detector = model
            ACTIVE_PPE_CLASSES.clear()
            ACTIVE_PPE_CLASSES.update(PPE_CLASSES)
            ACTIVE_REQUIRED_PPE.clear()
            ACTIVE_REQUIRED_PPE.update(REQUIRED_PPE)
            PPE_CLASS_IDS = None
            PERSON_CLASS_ID = None   # any unknown class ID = person
            logger.info("Loaded custom PPE model from %s", PPE_WEIGHT_PATH)
        else:
            # Tier 2: OIv7 public fallback
            logger.info(
                "PPE weight not found at %s. Using yolov8s-oiv7.pt (OIv7) fallback — "
                "detects Helmet, Glove, Goggles, Boot, Person. "
                "NOTE: safety_vest is not in OIv7; only hard_hat is required in fallback mode.",
                PPE_WEIGHT_PATH,
            )
            oiv7_path = _try_download_oiv7()
            if oiv7_path:
                try:
                    from ultralytics import YOLO
                    model = YOLO(oiv7_path)
                    model.to(resolve_device())
                    _ppe_detector = model
                    ACTIVE_PPE_CLASSES.clear()
                    ACTIVE_PPE_CLASSES.update(OIV7_PPE_MAP)
                    ACTIVE_REQUIRED_PPE.clear()
                    ACTIVE_REQUIRED_PPE.update(OIV7_REQUIRED_PPE)
                    PPE_CLASS_IDS = list(OIV7_PPE_MAP.keys()) + [OIV7_PERSON_CLASS]
                    PERSON_CLASS_ID = OIV7_PERSON_CLASS
                    logger.info(
                        "OIv7 PPE fallback active — PPE classes: %s, person class: %d, required: %s",
                        {k: model.names[k] for k in OIV7_PPE_MAP},
                        OIV7_PERSON_CLASS,
                        OIV7_REQUIRED_PPE,
                    )
                except Exception as exc:
                    logger.warning("Failed to load OIv7 for PPE: %s — no-op mode", exc)
                    _ppe_detector = _MODEL_UNAVAILABLE
            else:
                logger.warning("OIv7 unavailable — PPE worker in no-op mode")
                _ppe_detector = _MODEL_UNAVAILABLE

    return None if _ppe_detector is _MODEL_UNAVAILABLE else _ppe_detector
