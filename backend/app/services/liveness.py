"""Selfie liveness (anti-spoofing) check for check-in/out photos
(ShiftSecure Phase 2D). Reuses services/face.py's insightface singleton to
detect+crop the face, then runs a lightweight binary real/spoof ONNX
classifier (MiniFASNetV2-SE-style) on the crop via onnxruntime — already a
backend dependency (see watchlist.py's face-enrollment endpoint for the
precedent of running a face model synchronously inside an API request).

Model file (NOT included — must be sourced and placed manually before this
feature works, same as the LPR plate-detector weight in Phase 1):
    backend/models/liveness_minifasnet.onnx
Expected: https://github.com/facenox/face-antispoof-onnx's best_model_quantized.onnx
(~600KB): input 128x128 RGB in [0, 1], CHW; output two RAW LOGITS, index 0 = real,
index 1 = spoof (its src/inference/inference.py: `logit_diff = real_logit -
spoof_logit`, `is_real = logit_diff >= logit(threshold)`). Its face crop is a
square of 1.5x the face box's longer side, centred, edges reflect-padded
(src/inference/preprocess.py). This follows both.

FIXED 23 October 2026: this read output[1] - the SPOOF logit - and returned it
as the "real" probability. A real face scored about -4.5 and was refused; a spoof
would have passed. It now returns the model's real probability,
sigmoid(real - spoof), in 0..1, so attendance.liveness_min_score (0.70) means
what it says. The input size is still read from the model itself.
"""
import os
import threading

import cv2
import numpy as np

from app.services.face import decode_image, detect_largest_face

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "models", "liveness_minifasnet.onnx")

_session_lock = threading.Lock()
_session: object = None
_input_name: str | None = None
_input_hw: tuple[int, int] | None = None


def _load_session():
    global _session, _input_name, _input_hw
    if _session is not None:
        return _session, _input_name, _input_hw
    with _session_lock:
        if _session is not None:
            return _session, _input_name, _input_hw
        if not os.path.isfile(_MODEL_PATH):
            raise RuntimeError(
                f"Liveness model not found at {os.path.abspath(_MODEL_PATH)} — "
                "source and place the anti-spoofing ONNX model before this feature works."
            )
        try:
            import onnxruntime as ort  # noqa: PLC0415
            sess = ort.InferenceSession(_MODEL_PATH, providers=["CPUExecutionProvider"])
            inp = sess.get_inputs()[0]
            h, w = inp.shape[-2], inp.shape[-1]
            if not isinstance(h, int) or not isinstance(w, int):
                h, w = 128, 128  # dynamic-shape model — fall back to the known variant's size
            _session, _input_name, _input_hw = sess, inp.name, (h, w)
        except Exception as exc:
            raise RuntimeError(f"Liveness model unavailable: {exc}") from exc
    return _session, _input_name, _input_hw


#: The model's own crop: a square this many times the face box's longer side, centred on the face.
CROP_EXPANSION = 1.5


def _crop_face(img_rgb: np.ndarray, bbox: np.ndarray, expansion: float = CROP_EXPANSION) -> np.ndarray:
    """A square crop around the face, as the model was trained on: edges that fall outside the photo are
    filled by reflection rather than cut off, so the face stays centred."""
    x1, y1, x2, y2 = (float(v) for v in bbox[:4])
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return np.zeros((0, 0, 3), dtype=img_rgb.dtype)
    size = int(max(w, h) * expansion)
    left = int(x1 + w / 2 - size / 2)
    top = int(y1 + h / 2 - size / 2)
    height, width = img_rgb.shape[:2]
    cx1, cy1 = max(0, left), max(0, top)
    cx2, cy2 = min(width, left + size), min(height, top + size)
    if cx2 <= cx1 or cy2 <= cy1:
        return np.zeros((0, 0, 3), dtype=img_rgb.dtype)
    crop = img_rgb[cy1:cy2, cx1:cx2]
    return cv2.copyMakeBorder(crop, max(0, -top), max(0, top + size - height), max(0, -left),
                              max(0, left + size - width), cv2.BORDER_REFLECT_101)


def real_probability(scores: np.ndarray) -> float:
    """The model's 'this is a live face' probability, 0..1. Two raw logits are [real, spoof]: the probability
    is sigmoid(real - spoof), which is softmax's value for 'real'. A single output is taken as a probability
    already (a sigmoid variant)."""
    scores = np.asarray(scores, dtype=np.float64).squeeze()
    if scores.ndim == 0:
        return float(scores)
    real, spoof = float(scores[0]), float(scores[1])
    return float(1.0 / (1.0 + np.exp(-(real - spoof))))


def check_liveness_sync(image_bytes: bytes) -> float:
    """Returns a 0.0-1.0 'this is a real, live face' confidence score.
    Raises ValueError if no face is found (same convention as
    face.py's extract_embedding_sync), RuntimeError if the model itself
    can't be loaded."""
    img_rgb = decode_image(image_bytes)
    face = detect_largest_face(img_rgb)
    crop = _crop_face(img_rgb, face.bbox)
    if crop.size == 0:
        raise ValueError("Detected face region is empty after cropping")

    session, input_name, (h, w) = _load_session()
    interpolation = cv2.INTER_LANCZOS4 if w > crop.shape[1] else cv2.INTER_AREA
    resized = cv2.resize(crop, (w, h), interpolation=interpolation).astype(np.float32) / 255.0
    tensor = np.transpose(resized, (2, 0, 1))[np.newaxis, ...]  # NCHW, RGB, 0..1

    outputs = session.run(None, {input_name: tensor})
    return real_probability(outputs[0])
