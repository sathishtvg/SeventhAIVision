"""insightface's `buffalo_l` model pack: RetinaFace-10GF detection + ArcFace
(ResNet50@WebFace600K) recognition in one package, one model load, one
dependency for both stages (verified real via web search during planning).
Auto-downloaded by insightface's own official model zoo on first use — same
trust level as the package install itself, not a third-party derived artifact."""

import os

from worker.device import resolve_device

FACE_MODEL_PACK = os.environ.get("FACE_MODEL_PACK", "buffalo_l")
FACE_MODEL_VERSION = f"insightface-{FACE_MODEL_PACK}"

_face_app = None


def _providers_for(device: str) -> list[str]:
    return ["CUDAExecutionProvider", "CPUExecutionProvider"] if device == "cuda" else ["CPUExecutionProvider"]


def get_face_app():
    global _face_app
    if _face_app is None:
        from insightface.app import FaceAnalysis

        device = resolve_device()
        app = FaceAnalysis(name=FACE_MODEL_PACK, providers=_providers_for(device))
        app.prepare(ctx_id=0 if device == "cuda" else -1, det_size=(640, 640))
        _face_app = app
    return _face_app
