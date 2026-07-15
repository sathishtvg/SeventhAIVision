"""Weapon detection model loader with two-tier strategy:

Tier 1 — Custom fine-tuned weight at WEAPON_WEIGHT_PATH (preferred).
Tier 2 — Open Images v7 public fallback (yolov8s-oiv7.pt), reusing the file
          already cached for the LPR worker. OIv7 includes Handgun (238),
          Knife (296), Kitchen knife (292), Rifle (423), Shotgun (457),
          Sword (512), Axe (14), and Weapon (580).

ACTIVE_WEAPON_CLASSES and WEAPON_CLASS_IDS are populated at load time so
detect_weapons() in weapon_task.py can filter and map classes correctly
regardless of which tier is active.
"""

import logging
import os
from pathlib import Path

from worker.device import resolve_device

logger = logging.getLogger(__name__)

WEAPON_MODEL_VERSION = "yolov8s-weapon-2026.06"
WEAPON_WEIGHT_PATH = os.environ.get(
    "WEAPON_WEIGHT_PATH", "/app/ai-worker/models/yolov8s-weapon.pt"
)

# Shared OIv7 file (already downloaded by the LPR worker on first use)
OIV7_CACHE_PATH = str(Path(WEAPON_WEIGHT_PATH).parent / "yolov8s-oiv7.pt")
OIV7_DOWNLOAD_URL = (
    "https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8s-oiv7.pt"
)

# Fine-tuned model class mapping (classes 0-2 from custom weight)
WEAPON_CLASSES: dict[int, str] = {
    0: "firearm",
    1: "blade",
    2: "blunt",
}

# OIv7 class → our weapon type
OIV7_WEAPON_MAP: dict[int, str] = {
    14:  "blunt",    # Axe
    238: "firearm",  # Handgun
    292: "blade",    # Kitchen knife
    296: "blade",    # Knife
    423: "firearm",  # Rifle
    457: "firearm",  # Shotgun
    512: "blade",    # Sword
    580: "firearm",  # Weapon (general)
}

WEAPON_SEVERITIES: dict[str, str] = {
    "firearm": "critical",
    "blade": "high",
    "blunt": "medium",
}
AUTO_INCIDENT_WEAPON_TYPES: set[str] = {"firearm", "blade"}

# ── Runtime state (populated on first model load) ──────────────────────────
_MODEL_UNAVAILABLE = object()
_weapon_detector = None
ACTIVE_WEAPON_CLASSES: dict[int, str] = {}    # class_id → weapon_type for current tier
WEAPON_CLASS_IDS: list[int] | None = None      # None = all classes; list = OIv7 filter


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


def get_weapon_detector():
    """Returns the loaded detector or None in no-op mode.
    Also sets ACTIVE_WEAPON_CLASSES and WEAPON_CLASS_IDS for the task layer."""
    global _weapon_detector, ACTIVE_WEAPON_CLASSES, WEAPON_CLASS_IDS

    if _weapon_detector is None:
        if os.path.exists(WEAPON_WEIGHT_PATH):
            # Tier 1: custom fine-tuned model
            from ultralytics import YOLO
            model = YOLO(WEAPON_WEIGHT_PATH)
            model.to(resolve_device())
            _weapon_detector = model
            ACTIVE_WEAPON_CLASSES.clear()
            ACTIVE_WEAPON_CLASSES.update(WEAPON_CLASSES)
            WEAPON_CLASS_IDS = None
            logger.info("Loaded custom weapon model from %s", WEAPON_WEIGHT_PATH)
        else:
            # Tier 2: OIv7 public fallback
            logger.info(
                "Weapon weight not found at %s. Using yolov8s-oiv7.pt (OIv7) fallback — "
                "detects Handgun, Rifle, Shotgun, Knife, Sword, Axe, Weapon.",
                WEAPON_WEIGHT_PATH,
            )
            oiv7_path = _try_download_oiv7()
            if oiv7_path:
                try:
                    from ultralytics import YOLO
                    model = YOLO(oiv7_path)
                    model.to(resolve_device())
                    _weapon_detector = model
                    ACTIVE_WEAPON_CLASSES.clear()
                    ACTIVE_WEAPON_CLASSES.update(OIV7_WEAPON_MAP)
                    WEAPON_CLASS_IDS = list(OIV7_WEAPON_MAP.keys())
                    logger.info(
                        "OIv7 weapon fallback active — classes: %s",
                        {k: model.names[k] for k in WEAPON_CLASS_IDS},
                    )
                except Exception as exc:
                    logger.warning("Failed to load OIv7 for weapon: %s — no-op mode", exc)
                    _weapon_detector = _MODEL_UNAVAILABLE
            else:
                logger.warning("OIv7 unavailable — weapon worker in no-op mode")
                _weapon_detector = _MODEL_UNAVAILABLE

    return None if _weapon_detector is _MODEL_UNAVAILABLE else _weapon_detector
