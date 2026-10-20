"""SigLIP 2: a picture and a sentence as vectors that can be compared.

The model turns a frame into 768 numbers and a typed description into 768
numbers of the same kind; the nearer the two point, the more the frame looks
like what was described. It writes no text and decides nothing: it ranks.

Preparing the inputs is what the model was trained on, read from the model's
own files and checked, so a changed revision stops rather than quietly ranking
worse:

    a frame     resized to 224 x 224 (bilinear), scaled to 0..1, then to -1..1
    a sentence  lower case; its tokens, the end-of-text token, then padding to
                exactly 64 - the text model reads the last position, so the
                padding is part of what it was trained on

Vectors are scaled to length 1, so comparing two is one dot product, and the
database's cosine distance is 1 minus it.

HOW LIKELY A FRAME AND A SENTENCE ARE TO MATCH is the model's own measure:
SigLIP was trained to give each picture-and-sentence pair a probability,
sigmoid(LOGIT_SCALE x likeness + LOGIT_BIAS), with the two numbers it learned.
They are read from Google's weights (google/siglip2-base-patch16-224, revision
75de2d5, the tensors `logit_scale` - stored as its logarithm - and
`logit_bias`) and are the same for every frame and every sentence: a match is
judged on its own, not against the rest of the footage.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np

from worker.footage import fetch_model

DIM = 768
VERSION = f"siglip2-base-patch16-224-int8@{fetch_model.REVISION[:7]}"
LOGIT_SCALE = math.exp(4.724453449249268)
LOGIT_BIAS = -16.771724700927734
DEFAULT_ROOT = Path(os.environ.get("FOOTAGE_MODEL_DIR", "/opt/models/siglip2"))


def _session(path: Path, threads: int):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def _unit(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return (vectors / np.maximum(norms, 1e-12)).astype(np.float32)


class Embedder:
    """Both halves of the model. The text half is loaded when first asked."""

    def __init__(self, root: Path = DEFAULT_ROOT, threads: int | None = None):
        self.root = root
        self.threads = threads or int(os.environ.get("FOOTAGE_THREADS", "2"))
        pre = json.loads((root / "preprocessor_config.json").read_text(encoding="utf-8"))
        assert (pre["size"], pre["image_mean"], pre["image_std"], pre["resample"]) == (
            {"height": 224, "width": 224}, [0.5, 0.5, 0.5], [0.5, 0.5, 0.5], 2), pre
        self.side = pre["size"]["height"]
        self.scale = float(pre["rescale_factor"])
        tok = json.loads((root / "tokenizer_config.json").read_text(encoding="utf-8"))
        assert (tok["max_length"], tok["padding_side"], tok["add_eos_token"], tok["add_bos_token"],
                tok["do_lower_case"]) == (64, "right", True, False, True), tok
        self.length = tok["max_length"]
        self._vision = _session(root / "onnx" / "vision_model_quantized.onnx", self.threads)
        self._text = None
        self._tokenizer = None

    # ── pictures ──────────────────────────────────────────────────────────────

    def pixels(self, frames: list[np.ndarray]) -> np.ndarray:
        """RGB frames (height x width x 3, uint8) as the model's input."""
        from PIL import Image

        batch = np.stack([
            np.asarray(Image.fromarray(f).resize((self.side, self.side), Image.BILINEAR), dtype=np.float32)
            for f in frames])
        batch = (batch * self.scale - 0.5) / 0.5
        return np.ascontiguousarray(batch.transpose(0, 3, 1, 2))

    def embed_images(self, frames: list[np.ndarray]) -> np.ndarray:
        if not frames:
            return np.zeros((0, DIM), dtype=np.float32)
        out = self._vision.run(["pooler_output"], {self._vision.get_inputs()[0].name: self.pixels(frames)})[0]
        return _unit(out)

    # ── sentences ─────────────────────────────────────────────────────────────

    def token_ids(self, text: str) -> np.ndarray:
        if self._tokenizer is None:
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(str(self.root / "tokenizer.json"))
            tokenizer.no_padding()
            tokenizer.no_truncation()
            self._tokenizer = tokenizer
            self._eos = tokenizer.token_to_id("<eos>")
            self._pad = tokenizer.token_to_id("<pad>")
            assert self._eos is not None and self._pad is not None
        ids = self._tokenizer.encode(text.lower(), add_special_tokens=False).ids[: self.length - 1] + [self._eos]
        return np.array([ids + [self._pad] * (self.length - len(ids))], dtype=np.int64)

    def embed_text(self, text: str) -> np.ndarray:
        if self._text is None:
            self._text = _session(self.root / "onnx" / "text_model_quantized.onnx", self.threads)
        out = self._text.run(["pooler_output"], {self._text.get_inputs()[0].name: self.token_ids(text)})[0]
        return _unit(out)[0]
