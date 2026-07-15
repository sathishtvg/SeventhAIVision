"""
Download or source AI model weights for Seventh AI Vision.

Usage:
    python scripts/download_weights.py [--model lpr|ppe|fire_smoke|weapon|all]
    python scripts/download_weights.py --info          # print sourcing instructions
    python scripts/download_weights.py --copy-to-containers  # after manual download

Weight status per module
------------------------
  crowd    / intrusion / behavior : yolov8s.pt — auto-downloads from Ultralytics on
                                    first use; already baked into the Docker image.
  face                            : buffalo_l (insightface) — same, auto-downloads.
  lpr      / ppe / fire_smoke / weapon : fine-tuned weights — require manual sourcing
                                    (see --info).  Workers run in graceful no-op mode
                                    until the file appears at the configured path.

After placing a weight file at ai-worker/models/<name>.pt on the HOST, the
bind-mount in docker-compose makes it visible inside containers immediately —
no rebuild required.  The worker picks it up on the NEXT processed frame.
"""

import argparse
import os
import sys
import urllib.request
from pathlib import Path

# Windows cmd/PowerShell terminals default to cp1252 which can't encode arrows etc.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

MODELS_DIR = Path(__file__).parent.parent / "ai-worker" / "models"

# ── Download sources ────────────────────────────────────────────────────────
# We try multiple URLs per model; the first successful one wins.
# (Some HuggingFace repos require login — fallback URLs skip those.)

WEIGHT_SOURCES: dict[str, dict] = {
    "lpr": {
        "filename": "yolov8s-lpr.pt",
        "description": "License plate detector — YOLOv8s fine-tuned on LP datasets",
        "try_urls": [
            # keremberke models now require HuggingFace login — workers use yolov8s-oiv7.pt fallback
            # "https://huggingface.co/keremberke/yolov8s-license-plate-detection/resolve/main/best.pt?download=true",
        ],
        "manual": (
            "1. Sign up free at https://roboflow.com\n"
            "2. Go to: https://universe.roboflow.com/roboflow-universe-projects/license-plate-recognition-rxg4e\n"
            "3. Click 'Download Dataset' → Format: YOLOv8 → 'Download zip to computer'\n"
            "   OR: pip install roboflow && python -c \"\n"
            "   from roboflow import Roboflow\n"
            "   rf = Roboflow(api_key='YOUR_KEY')\n"
            "   proj = rf.workspace('roboflow-universe-projects').project('license-plate-recognition-rxg4e')\n"
            "   model = proj.version(3).model\n"
            "   # Download trained weights from Roboflow deploy tab instead\"\n"
            "4. Train with: yolo train model=yolov8s.pt data=<dataset>/data.yaml epochs=50 imgsz=640\n"
            "5. Copy runs/detect/train/weights/best.pt → ai-worker/models/yolov8s-lpr.pt"
        ),
    },
    "ppe": {
        "filename": "yolov8s-ppe.pt",
        "description": "PPE detector — hard hat, safety vest, person",
        "try_urls": [
            # keremberke models now require HuggingFace login — workers use yolov8s-oiv7.pt fallback
            # "https://huggingface.co/keremberke/yolov8s-hard-hat-detection/resolve/main/best.pt?download=true",
        ],
        "manual": (
            "1. Sign up free at https://roboflow.com\n"
            "2. Go to: https://universe.roboflow.com/roboflow-universe-projects/construction-site-safety\n"
            "3. Download dataset → YOLOv8 format\n"
            "4. Train: yolo train model=yolov8s.pt data=<dataset>/data.yaml epochs=50 imgsz=640\n"
            "5. Copy runs/detect/train/weights/best.pt → ai-worker/models/yolov8s-ppe.pt\n"
            "\nNOTE: After training, check class IDs match ppe_model.py PPE_CLASSES mapping.\n"
            "      Run: python -c \"from ultralytics import YOLO; print(YOLO('ai-worker/models/yolov8s-ppe.pt').names)\""
        ),
    },
    "fire_smoke": {
        "filename": "yolov8s-fire-smoke.pt",
        "description": "Fire and smoke detector — YOLOv8s",
        "try_urls": [
            # keremberke models now require HuggingFace login — workers use yolov8s-worldv2.pt fallback
            # "https://huggingface.co/keremberke/yolov8s-fire-and-smoke-detection/resolve/main/best.pt?download=true",
        ],
        "manual": (
            "1. Sign up free at https://roboflow.com\n"
            "2. Go to: https://universe.roboflow.com/roboflow-universe-projects/fire-smoke-detection-3df9a\n"
            "3. Download dataset → YOLOv8 format\n"
            "4. Train: yolo train model=yolov8s.pt data=<dataset>/data.yaml epochs=50 imgsz=640\n"
            "5. Copy runs/detect/train/weights/best.pt → ai-worker/models/yolov8s-fire-smoke.pt\n"
            "\nNOTE: Dataset must have 'fire' as class 0 and 'smoke' as class 1 to match\n"
            "      fire_smoke_model.py FIRE_SMOKE_CLASSES. Verify after training."
        ),
    },
    "weapon": {
        "filename": "yolov8s-weapon.pt",
        "description": "Weapon detector — firearm, blade, blunt instrument",
        "try_urls": [
            # keremberke models now require HuggingFace login — workers use yolov8s-oiv7.pt fallback
            # "https://huggingface.co/keremberke/yolov8s-weapon-detection/resolve/main/best.pt?download=true",
        ],
        "manual": (
            "1. Sign up free at https://roboflow.com\n"
            "2. Search: https://universe.roboflow.com for 'weapon detection yolov8'\n"
            "   Recommended: 'Weapon Detection' by Roboflow Universe Projects\n"
            "3. Download dataset → YOLOv8 format\n"
            "4. Train: yolo train model=yolov8s.pt data=<dataset>/data.yaml epochs=50 imgsz=640\n"
            "5. Copy runs/detect/train/weights/best.pt → ai-worker/models/yolov8s-weapon.pt\n"
            "\nClass mapping required in weapon_model.py WEAPON_CLASSES:\n"
            "  {0: 'firearm', 1: 'blade', 2: 'blunt'}\n"
            "Adjust if your trained model uses a different mapping."
        ),
    },
}

ALREADY_AVAILABLE = {
    "crowd":      "yolov8s.pt  (auto-downloads via Ultralytics — already in Docker image)",
    "intrusion":  "yolov8s.pt  (auto-downloads via Ultralytics — already in Docker image)",
    "behavior":   "yolov8s-pose.pt  (auto-downloads via Ultralytics on first use)",
    "face":       "buffalo_l  (insightface pack — auto-downloads on first use)",
    "lpr*":       "yolov8s-oiv7.pt fallback (Open Images v7, auto-downloads). "
                  "Detects all plate formats including Singapore. Use a Singapore-trained weight for best accuracy.",
    "weapon*":    "yolov8s-oiv7.pt fallback (OIv7, auto-downloads). "
                  "Detects Handgun/Rifle/Shotgun/Knife/Sword/Axe/Weapon — no fine-tuned weight needed.",
    "ppe*":       "yolov8s-oiv7.pt fallback (OIv7, auto-downloads). "
                  "Detects Helmet/Glove/Goggles/Boot. NOTE: safety_vest not in OIv7; only hard_hat required.",
    "fire_smoke*":"yolov8s-worldv2.pt fallback (YOLOWorld, auto-downloads). "
                  "Open-vocabulary model queried for 'fire' and 'smoke' directly.",
}


def _progress_hook(count: int, block_size: int, total_size: int) -> None:
    pct = min(int(count * block_size * 100 / total_size), 100) if total_size > 0 else 0
    mb_done = count * block_size / 1_048_576
    mb_total = total_size / 1_048_576
    print(f"\r  {pct:3d}%  {mb_done:.1f} / {mb_total:.1f} MB", end="", flush=True)


def download_model(model_key: str) -> bool:
    info = WEIGHT_SOURCES[model_key]
    dest = MODELS_DIR / info["filename"]

    print(f"\n{'='*60}")
    print(f"  {model_key.upper()} — {info['description']}")
    print(f"{'='*60}")

    if dest.exists():
        size_mb = dest.stat().st_size / 1_048_576
        print(f"  Already present: {dest} ({size_mb:.1f} MB)")
        return True

    for url in info["try_urls"]:
        print(f"  Trying: {url}")
        try:
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(url, dest, reporthook=_progress_hook)
            print()
            size_mb = dest.stat().st_size / 1_048_576
            if size_mb < 1:
                print(f"  File too small ({size_mb:.2f} MB) — likely an error page. Removing.")
                dest.unlink()
                continue
            print(f"  Downloaded: {size_mb:.1f} MB → {dest}")
            return True
        except Exception as exc:
            print(f"\n  Failed ({exc}), trying next source...")
            if dest.exists():
                dest.unlink()

    print(f"\n  Auto-download not available for {model_key}.")
    print(f"\n  Manual steps:\n")
    for line in info["manual"].splitlines():
        print(f"    {line}")
    return False


def print_info() -> None:
    print("\n=== Seventh AI Vision — Model Weight Reference ===\n")
    print("Modules that always work out of the box (auto-download):")
    for module, note in ALREADY_AVAILABLE.items():
        print(f"  {module:<14} {note}")

    print("\n* = OIv7/YOLOWorld fallback active — workers function without fine-tuned weights.")
    print("    Fine-tuned weights improve accuracy; fallbacks are adequate for development.\n")

    print("Optional fine-tuned weights (improve accuracy beyond the fallback):")
    for key, info in WEIGHT_SOURCES.items():
        dest = MODELS_DIR / info["filename"]
        status = f"PRESENT ({dest.stat().st_size/1_048_576:.1f} MB)" if dest.exists() else "MISSING (fallback active)"
        print(f"\n  {key.upper()} [{status}]")
        print(f"  File: ai-worker/models/{info['filename']}")
        print(f"  Container path: /app/ai-worker/models/{info['filename']}")

    print("\n" + "="*60)
    print("Quick activation (after placing file on host):")
    print("  Bind-mount in docker-compose.yml makes host ai-worker/models/")
    print("  visible at /app/ai-worker/models/ inside ALL ai-worker containers.")
    print("  The worker picks up the weight on the next processed frame —")
    print("  no container restart needed.\n")
    print("Or, to copy into a running container manually:")
    print("  docker cp ai-worker/models/<name>.pt docker-ai-worker-lpr-1:/app/ai-worker/models/<name>.pt")


def copy_to_containers() -> None:
    container_map = {
        "lpr": "docker-ai-worker-lpr-1",
        "ppe": "docker-ai-worker-ppe-1",
        "fire_smoke": "docker-ai-worker-fire-smoke-1",
        "weapon": "docker-ai-worker-weapon-1",
    }
    print("\n=== Copying weights to running containers ===")
    for key, info in WEIGHT_SOURCES.items():
        src = MODELS_DIR / info["filename"]
        if not src.exists():
            print(f"  {key}: {info['filename']} not found — skipping")
            continue
        container = container_map[key]
        dest_path = f"/app/ai-worker/models/{info['filename']}"
        cmd = f'docker exec {container} mkdir -p /app/ai-worker/models 2>/dev/null; docker cp "{src}" {container}:{dest_path}'
        print(f"  {key}: {cmd}")
        ret = os.system(cmd)
        if ret == 0:
            print(f"    OK")
        else:
            print(f"    Failed (container may not be running)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage AI model weights for Seventh AI Vision")
    parser.add_argument("--model", choices=list(WEIGHT_SOURCES.keys()) + ["all"], default="all")
    parser.add_argument("--info", action="store_true", help="Show weight status and sourcing instructions")
    parser.add_argument("--copy-to-containers", action="store_true",
                        help="Copy host-side weights into running Docker containers")
    args = parser.parse_args()

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    if args.info:
        print_info()
        return

    if args.copy_to_containers:
        copy_to_containers()
        return

    keys = list(WEIGHT_SOURCES.keys()) if args.model == "all" else [args.model]

    print(f"Models directory: {MODELS_DIR.resolve()}")
    results: dict[str, bool] = {}
    for key in keys:
        results[key] = download_model(key)

    print(f"\n{'='*60}")
    print("Summary:")
    for key, ok in results.items():
        info = WEIGHT_SOURCES[key]
        dest = MODELS_DIR / info["filename"]
        if dest.exists():
            print(f"  {key:<12} READY ({dest.stat().st_size/1_048_576:.1f} MB)")
        else:
            print(f"  {key:<12} MISSING — see manual steps above; worker runs in no-op mode")

    print(f"\nRun with --info for full sourcing instructions.")


if __name__ == "__main__":
    main()
