"""Camera tampering detection using OpenCV.

No ML model weights required — uses:
- Histogram comparison between current frame and stored reference frame (detects
  camera blocking / drastic scene change / physical covering).
- Laplacian blur score (detects intentional defocusing/lens smearing).

Both are fast OpenCV operations, CPU-only, no GPU needed.
"""

import os

import cv2
import numpy as np

TAMPERING_MODULE_VERSION = "opencv-hist-blur-v1"
TAMPERING_MODEL_VERSION = TAMPERING_MODULE_VERSION  # alias used by tampering_task.py

# Correl score < this  = scene drastically changed (blocked / moved / covered)
HIST_CORREL_THRESHOLD = float(os.environ.get("TAMPERING_HIST_THRESHOLD", "0.35"))
# Variance below this = frame is suspiciously blurry (defocused / smeared)
BLUR_VAR_THRESHOLD = float(os.environ.get("TAMPERING_BLUR_THRESHOLD", "30.0"))


def compute_hist_similarity(frame1: np.ndarray, frame2: np.ndarray) -> float:
    """Normalised Bhattacharyya correlation between HSV histograms of two frames.
    Returns 0.0–1.0; close to 1.0 = very similar, close to 0.0 = very different.
    """
    h1 = cv2.cvtColor(frame1, cv2.COLOR_BGR2HSV)
    h2 = cv2.cvtColor(frame2, cv2.COLOR_BGR2HSV)
    hist1 = cv2.calcHist([h1], [0, 1], None, [50, 60], [0, 180, 0, 256])
    hist2 = cv2.calcHist([h2], [0, 1], None, [50, 60], [0, 180, 0, 256])
    cv2.normalize(hist1, hist1)
    cv2.normalize(hist2, hist2)
    return float(cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL))


def compute_blur_variance(frame: np.ndarray) -> float:
    """Laplacian variance — high = sharp, low = blurry."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def detect_tampering(
    current_frame: np.ndarray,
    reference_frame: np.ndarray | None,
) -> dict | None:
    """Return a tampering detection dict or None.

    Dict keys:
    - tampering_type: 'blocked' | 'moved' | 'covered' | 'defocused'
    - score: float 0–1 (higher = more confident it is tampering)
    - reason: human-readable explanation
    """
    blur_var = compute_blur_variance(current_frame)

    if blur_var < BLUR_VAR_THRESHOLD:
        score = min(1.0, (BLUR_VAR_THRESHOLD - blur_var) / BLUR_VAR_THRESHOLD)
        return {
            "tampering_type": "defocused",
            "score": round(score, 4),
            "reason": f"Blur variance {blur_var:.1f} < threshold {BLUR_VAR_THRESHOLD}",
        }

    if reference_frame is None:
        return None

    similarity = compute_hist_similarity(current_frame, reference_frame)

    if similarity < HIST_CORREL_THRESHOLD:
        score = min(1.0, 1.0 - similarity)
        tampering_type = "covered" if similarity < 0.05 else "moved"
        return {
            "tampering_type": tampering_type,
            "score": round(score, 4),
            "reason": f"Histogram correlation {similarity:.3f} < threshold {HIST_CORREL_THRESHOLD}",
        }

    return None
