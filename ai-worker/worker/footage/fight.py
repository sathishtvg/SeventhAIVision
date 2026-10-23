"""Possible altercations: a picture-and-sentence screen, then a look at the bodies.

STAGE 1, every sampled second. The frame's SigLIP 2 vector is compared with
fixed descriptions of fighting and with descriptions of what looks like it and
is not (hugging, dancing, a crowd walking, sport). Each comparison is the
model's own logit; the share of the fighting descriptions among all of them is
how much more the frame looks like a fight than like its look-alikes. A second
is a CANDIDATE when that share is at least SHARE and at least two people were
counted in it.

Where at least 2 of 3 consecutive seconds are candidates, that stretch (and a
second either side) is a WINDOW.

STAGE 2, each window. The frames are read again, FPS a second, and the pose
model looks at the bodies (worker.footage.pose): two people within reach of
each other, and a fast-moving arm. The window is a POSSIBLE ALTERCATION when
that is seen between at least MOMENTS pairs of frames.

What is written is never "assault" and never a name: a possible altercation,
with the reasons, for a person to mark confirmed or not an altercation. The
thresholds are first settings, to be measured against labelled footage.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

FIGHTING = (
    "people fighting",
    "a person punching another person",
    "people pushing each other",
    "a violent fight between people",
    "people wrestling on the ground",
)
LOOK_ALIKES = (
    "people hugging",
    "people dancing",
    "a crowd of people walking",
    "people talking to each other",
    "people shaking hands",
    "people playing sports",
    "an empty place with nobody in it",
)
SHARE = 0.5
LEAST_PEOPLE = 2
FPS = 5
MOMENTS = 3
VERSION = "siglip2+yolov8s-pose"


class Screen:
    """The fixed descriptions, as the model reads them."""

    def __init__(self, embed_text, logit_scale: float, logit_bias: float):
        self.fighting = np.stack([embed_text(t) for t in FIGHTING])
        self.look_alikes = np.stack([embed_text(t) for t in LOOK_ALIKES])
        self.scale, self.bias = logit_scale, logit_bias

    def shares(self, vectors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(how much each frame looks like a fight rather than a look-alike, 0..1; its best fighting description)."""
        fight = self.scale * vectors @ self.fighting.T + self.bias
        alike = self.scale * vectors @ self.look_alikes.T + self.bias
        everything = np.concatenate([fight, alike], axis=1)
        top = everything.max(axis=1, keepdims=True)
        weights = np.exp(everything - top)
        share = weights[:, : len(FIGHTING)].sum(axis=1) / weights.sum(axis=1)
        return share, fight.argmax(axis=1)


@dataclass
class Window:
    start: int
    end: int
    best: int
    share: float
    described: str
    seconds: list[int] = field(default_factory=list)


def windows(per_second: dict[int, tuple[float, int, int]], share: float = SHARE,
            people: int = LEAST_PEOPLE) -> list[Window]:
    """(second -> (share, people counted, best description's index)) as the stretches worth a second look."""
    candidate = {s for s, (sh, n, _) in per_second.items() if sh >= share and n >= people}
    out: list[Window] = []
    for s in sorted(candidate):
        if sum(1 for t in (s - 1, s, s + 1) if t in candidate) < 2:
            continue
        if out and s <= out[-1].end + 2:
            w = out[-1]
            w.end = s
        else:
            w = Window(s, s, s, 0.0, "")
            out.append(w)
        w.seconds.append(s)
        sh, _, which = per_second[s]
        if sh > w.share:
            w.best, w.share, w.described = s, sh, FIGHTING[which]
    for w in out:
        w.start = max(0, min(w.seconds) - 1)
        w.end = max(w.seconds) + 1
    return out


def confirmed(frames: list[list], fps: int = FPS, moments: int = MOMENTS) -> tuple[bool, dict]:
    """Bodies in each frame of a window, in order -> (a possible altercation, why)."""
    from worker.footage import pose

    struggles = close = 0
    for before, after in zip(frames, frames[1:]):
        if pose.within_reach(after):
            close += 1
        if pose.struggle(before, after, 1 / fps):
            struggles += 1
    return struggles >= moments, {"frames_looked_at": len(frames), "frames_with_people_within_reach": close,
                                  "moments_with_fast_arms_within_reach": struggles}
