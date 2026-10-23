"""Bodies in a frame, and how they move: the second look at a possible altercation.

YOLOv8s-pose (Ultralytics, AGPL-3.0), converted to ONNX when the worker's image
is built. For each person it answers a box and 17 body points (COCO order:
nose, eyes, ears, shoulders, elbows, wrists, hips, knees, ankles), each with how
sure it is.

What is measured, over frames a fifth of a second apart:

    within reach   two people whose boxes are nearer, centre to centre, than
                   REACH times their mean height
    fast arms      a wrist of one of them moving faster than FAST body heights
                   a second (a punch or a shove; an arm swinging while walking
                   is well under it)

A person is followed from one frame to the next by the nearest box. Nothing
here says who anybody is.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from worker.footage.detect import keep_best, letterbox

DEFAULT_PATH = Path(os.environ.get("FOOTAGE_POSE", "/opt/models/yolov8s-pose/yolov8s-pose.onnx"))
SCORE = 0.4
POINT = 0.3
WRISTS = (9, 10)
REACH = 1.2
FAST = 1.5


@dataclass
class Body:
    score: float
    box: np.ndarray        # x1, y1, x2, y2
    points: np.ndarray     # 17 x 3: x, y, how sure

    @property
    def height(self) -> float:
        return float(max(self.box[3] - self.box[1], 1.0))

    @property
    def centre(self) -> np.ndarray:
        return np.array([(self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2])


def decode(output: np.ndarray, scale: float, px: float, py: float, score: float = SCORE) -> list[Body]:
    """The model's (1, 56, 8400) answer as the bodies in the frame's own pixels."""
    rows = output[0].T
    rows = rows[rows[:, 4] >= score]
    if not len(rows):
        return []
    cx, cy, w, h = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3]
    boxes = np.stack([(cx - w / 2 - px) / scale, (cy - h / 2 - py) / scale,
                      (cx + w / 2 - px) / scale, (cy + h / 2 - py) / scale], axis=1)
    bodies = []
    for i in keep_best(boxes, rows[:, 4]):
        points = rows[i, 5:].reshape(17, 3).copy()
        points[:, 0] = (points[:, 0] - px) / scale
        points[:, 1] = (points[:, 1] - py) / scale
        bodies.append(Body(float(rows[i, 4]), boxes[i], points))
    return bodies


def within_reach(bodies: list[Body], reach: float = REACH) -> list[tuple[int, int]]:
    """Pairs of people near enough to touch."""
    pairs = []
    for i in range(len(bodies)):
        for j in range(i + 1, len(bodies)):
            a, b = bodies[i], bodies[j]
            if np.linalg.norm(a.centre - b.centre) < reach * (a.height + b.height) / 2:
                pairs.append((i, j))
    return pairs


def follow(before: list[Body], after: list[Body]) -> dict[int, int]:
    """Who in `after` is who in `before`: each to the nearest centre within a body height, once."""
    taken: set[int] = set()
    matches: dict[int, int] = {}
    for j, b in enumerate(after):
        best, near = None, None
        for i, a in enumerate(before):
            if i in taken:
                continue
            distance = float(np.linalg.norm(a.centre - b.centre))
            if distance < a.height and (near is None or distance < near):
                best, near = i, distance
        if best is not None:
            taken.add(best)
            matches[j] = best
    return matches


def arm_speed(a: Body, b: Body, seconds: float) -> float:
    """The faster wrist's speed between two frames, in body heights a second; 0 when neither wrist is seen in both."""
    fastest = 0.0
    for k in WRISTS:
        if a.points[k, 2] >= POINT and b.points[k, 2] >= POINT:
            moved = float(np.linalg.norm(b.points[k, :2] - a.points[k, :2]))
            fastest = max(fastest, moved / ((a.height + b.height) / 2) / seconds)
    return fastest


def struggle(before: list[Body], after: list[Body], seconds: float, fast: float = FAST) -> bool:
    """Two people within reach in the later frame, one of whom moved an arm fast since the earlier one."""
    pairs = within_reach(after)
    if not pairs:
        return False
    who = follow(before, after)
    close = {i for pair in pairs for i in pair}
    return any(j in close and arm_speed(before[i], after[j], seconds) > fast for j, i in who.items())


class PoseDetector:
    def __init__(self, path: Path = DEFAULT_PATH, threads: int | None = None):
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads or int(os.environ.get("FOOTAGE_THREADS", "2"))
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name

    def bodies(self, frame: np.ndarray) -> list[Body]:
        pixels, scale, px, py = letterbox(frame)
        return decode(self._session.run(None, {self._input: pixels})[0], scale, px, py)
