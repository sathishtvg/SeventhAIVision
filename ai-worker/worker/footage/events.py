"""Events: the stretches of a video in which people, or vehicles, were seen.

Each sampled second has a count of people and of vehicles. Seconds with at
least one are joined into one stretch when no more than GAP seconds without
any lie between them - a person walking behind a pillar is one event, not
three. A stretch says when it begins and ends, how many were seen at most, and
which second shows the most of them (its picture on the timeline).

What is counted is what the detector saw in one frame a second: somebody who
passes in less than a second may be missed, and a count is how many were in
one frame, not how many different people came by.
"""
from __future__ import annotations

from dataclasses import dataclass, field

GAP = 3
KINDS = ("PEOPLE", "VEHICLES")


@dataclass
class Event:
    kind: str
    start: int
    end: int
    most: int
    best: int
    score: float
    seconds: int = 0
    reasons: dict = field(default_factory=dict)


def stretches(kind: str, counts: dict[int, tuple[int, float]], gap: int = GAP) -> list[Event]:
    """(second -> (how many, best score)) as events of this kind."""
    out: list[Event] = []
    current: Event | None = None
    for second in sorted(s for s, (n, _) in counts.items() if n > 0):
        n, score = counts[second]
        if current is not None and second - current.end <= gap + 1:
            current.end = second
            current.seconds += 1
            if n > current.most or (n == current.most and score > current.score):
                current.most, current.best, current.score = n, second, score
            continue
        current = Event(kind, second, second, n, second, score, seconds=1)
        out.append(current)
    for e in out:
        e.reasons = {"seen_in_seconds": e.seconds, "most_at_once": e.most}
    return out
