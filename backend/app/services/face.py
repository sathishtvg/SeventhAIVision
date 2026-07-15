"""Shared insightface singleton — lazy-loaded, thread-safe, used synchronously
inside API requests (never blocks the event loop directly; callers must wrap
sync calls in asyncio.to_thread). Originally lived only in watchlist.py's
/enroll endpoint; extracted here once services/liveness.py became a second
caller needing the same loaded face-detection model.
"""
import threading

import numpy as np

_face_app_lock = threading.Lock()
_face_app: object = None


def load_face_app():
    global _face_app
    if _face_app is not None:
        return _face_app
    with _face_app_lock:
        if _face_app is not None:
            return _face_app
        try:
            import insightface  # noqa: PLC0415
            app = insightface.app.FaceAnalysis(
                name="buffalo_l",
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
            app.prepare(ctx_id=0, det_size=(640, 640))
            _face_app = app
        except Exception as exc:
            raise RuntimeError(f"Face recognition model unavailable: {exc}") from exc
    return _face_app


def decode_image(image_bytes: bytes):
    """Returns an RGB numpy array, or raises ValueError if not a valid image."""
    import cv2  # noqa: PLC0415
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not decode image — ensure it is a valid JPEG or PNG")
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


def detect_largest_face(img_rgb: np.ndarray):
    """Returns the insightface Face object with the largest bounding box, or
    raises ValueError if no face is detected."""
    app = load_face_app()
    faces = app.get(img_rgb)
    if not faces:
        raise ValueError("No face detected in the uploaded image")
    return max(faces, key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]))


def extract_embedding_sync(image_bytes: bytes) -> list[float]:
    img_rgb = decode_image(image_bytes)
    face = detect_largest_face(img_rgb)
    return face.normed_embedding.tolist()
