"""Selfie liveness (anti-spoofing) check for check-in/out photos
(ShiftSecure Phase 2D). Reuses services/face.py's insightface singleton to
detect+crop the face, then runs a lightweight binary real/spoof ONNX
classifier (MiniFASNetV2-SE-style) on the crop via onnxruntime — already a
backend dependency (see watchlist.py's face-enrollment endpoint for the
precedent of running a face model synchronously inside an API request).

Model file (NOT included — must be sourced and placed manually before this
feature works, same as the LPR plate-detector weight in Phase 1):
    backend/models/liveness_minifasnet.onnx
Expected shape: 128x128 RGB, single sigmoid/softmax "real" probability
output — matches https://github.com/facenox/face-antispoof-onnx's
best_model_quantized.onnx (~600KB). The input size is read from the model
itself at load time rather than hardcoded, so a differently-shaped variant
still works without a code change.
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


def _crop_face(img_rgb: np.ndarray, bbox: np.ndarray, pad_ratio: float = 0.25) -> np.ndarray:
    x1, y1, x2, y2 = bbox
    w, h = x2 - x1, y2 - y1
    pad_x, pad_y = w * pad_ratio, h * pad_ratio
    x1 = max(0, int(x1 - pad_x))
    y1 = max(0, int(y1 - pad_y))
    x2 = min(img_rgb.shape[1], int(x2 + pad_x))
    y2 = min(img_rgb.shape[0], int(y2 + pad_y))
    return img_rgb[y1:y2, x1:x2]


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
    resized = cv2.resize(crop, (w, h)).astype(np.float32) / 255.0
    tensor = np.transpose(resized, (2, 0, 1))[np.newaxis, ...]  # NCHW

    outputs = session.run(None, {input_name: tensor})
    scores = np.asarray(outputs[0]).squeeze()
    if scores.ndim == 0:
        return float(scores)  # single sigmoid "real" probability
    # Two-class softmax output — convention: index 1 = "real", index 0 = "spoof"
    return float(scores[1]) if scores.shape[-1] >= 2 else float(scores[0])
