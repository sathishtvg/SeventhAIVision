"""People and vehicles in a frame: the detector the live workers run, as ONNX.

YOLOv8s (Ultralytics; the weights in ai-worker/yolov8s.pt, which the live AI
workers load), converted to ONNX when the footage worker's image is built, so
the worker needs no PyTorch. AGPL-3.0: see FOOTAGE_REVIEW.md.

The model sees a 640 x 640 picture: the frame scaled to fit and the rest filled
with grey. It answers 8,400 boxes, each with a score for each of the 80 COCO
classes; a box is kept when its best class scores at least SCORE, and of boxes
of one class that overlap by more than OVERLAP only the best is kept.

Only two kinds of thing are counted: PEOPLE (COCO person) and VEHICLES (bicycle,
car, motorcycle, bus, truck). A count says how many were seen, never who.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DEFAULT_PATH = Path(os.environ.get("FOOTAGE_DETECTOR", "/opt/models/yolov8s/yolov8s.onnx"))
SIDE = 640
SCORE = float(os.environ.get("FOOTAGE_DETECT_SCORE", "0.35"))
OVERLAP = 0.5
PEOPLE = {0}
VEHICLES = {1, 2, 3, 5, 7}
VERSION = "yolov8s-coco-onnx"


@dataclass(frozen=True)
class Box:
    """One thing seen, in the frame's own pixels."""
    kind: str  # "PERSON" or "VEHICLE"
    score: float
    x1: float
    y1: float
    x2: float
    y2: float


def letterbox(frame: np.ndarray, side: int = SIDE) -> tuple[np.ndarray, float, float, float]:
    """(the model's input, scale, x padding, y padding)."""
    from PIL import Image

    h, w = frame.shape[:2]
    scale = min(side / w, side / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    canvas = np.full((side, side, 3), 114, dtype=np.uint8)
    px, py = (side - nw) // 2, (side - nh) // 2
    canvas[py:py + nh, px:px + nw] = np.asarray(Image.fromarray(frame).resize((nw, nh), Image.BILINEAR))
    pixels = canvas.astype(np.float32).transpose(2, 0, 1)[None] / 255.0
    return np.ascontiguousarray(pixels), scale, float(px), float(py)


def _iou(box: np.ndarray, others: np.ndarray) -> np.ndarray:
    x1 = np.maximum(box[0], others[:, 0])
    y1 = np.maximum(box[1], others[:, 1])
    x2 = np.minimum(box[2], others[:, 2])
    y2 = np.minimum(box[3], others[:, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area = (box[2] - box[0]) * (box[3] - box[1])
    areas = (others[:, 2] - others[:, 0]) * (others[:, 3] - others[:, 1])
    return inter / np.maximum(area + areas - inter, 1e-9)


def keep_best(boxes: np.ndarray, scores: np.ndarray, overlap: float = OVERLAP) -> list[int]:
    """Indices of the boxes kept: the best of each group that overlaps by more than `overlap`."""
    order = list(np.argsort(-scores))
    kept: list[int] = []
    while order:
        best = order.pop(0)
        kept.append(best)
        if not order:
            break
        rest = np.array(order)
        order = [int(i) for i in rest[_iou(boxes[best], boxes[rest]) <= overlap]]
    return kept


def decode(output: np.ndarray, scale: float, px: float, py: float, score: float = SCORE) -> list[Box]:
    """The model's (1, 84, 8400) answer as the people and vehicles in the frame's own pixels."""
    rows = output[0].T
    classes = rows[:, 4:].argmax(axis=1)
    scores = rows[np.arange(len(rows)), 4 + classes]
    wanted = (scores >= score) & np.isin(classes, list(PEOPLE | VEHICLES))
    rows, classes, scores = rows[wanted], classes[wanted], scores[wanted]
    if not len(rows):
        return []
    cx, cy, w, h = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3]
    xyxy = np.stack([(cx - w / 2 - px) / scale, (cy - h / 2 - py) / scale,
                     (cx + w / 2 - px) / scale, (cy + h / 2 - py) / scale], axis=1)
    found: list[Box] = []
    for cls in np.unique(classes):
        mine = np.where(classes == cls)[0]
        for i in keep_best(xyxy[mine], scores[mine]):
            j = mine[i]
            found.append(Box("PERSON" if int(cls) in PEOPLE else "VEHICLE", float(scores[j]), *map(float, xyxy[j])))
    return found


class Detector:
    def __init__(self, path: Path = DEFAULT_PATH, threads: int | None = None):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads or int(os.environ.get("FOOTAGE_THREADS", "2"))
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name

    def boxes(self, frame: np.ndarray) -> list[Box]:
        pixels, scale, px, py = letterbox(frame)
        return decode(self._session.run(None, {self._input: pixels})[0], scale, px, py)

    def count(self, frame: np.ndarray) -> tuple[int, int, float, float]:
        """(people, vehicles, the best person score, the best vehicle score)."""
        found = self.boxes(frame)
        people = [b.score for b in found if b.kind == "PERSON"]
        vehicles = [b.score for b in found if b.kind == "VEHICLE"]
        return len(people), len(vehicles), max(people, default=0.0), max(vehicles, default=0.0)
