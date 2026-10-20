"""Searching footage by description: the frames that look most like what was typed.

Design: docs/plans/2026-10-20-footage-review-design.md, section 4.

THE WORDS BECOME A VECTOR in the footage worker, by the same model that
indexed the frames (SigLIP 2, run in our own stack). Only the words go to it;
no footage leaves the platform, and the model writes no text: it ranks. A
search compares the words only with frames that model indexed.

THE NEAREST FRAMES are found through the HNSW index of `footage_frames`,
limited to the videos the person may see, and asked to keep looking past
frames it must leave out (pgvector's iterative scan).

HOW LIKELY A MATCH IS is the model's own measure, said in words. SigLIP was
trained to give each picture-and-sentence pair a probability,
sigmoid(logit_scale x likeness + logit_bias), with two numbers it learned; the
worker sends them with the words' vector. A frame is judged on its own, not
against the rest of the footage, so what fills half the video is found as
readily as what appears once:

    Strong match     at least STRONG
    Possible match   at least POSSIBLE
    Weak match       at least WEAK
    (below WEAK the frame is not offered)

A search with nothing above POSSIBLE says so ("no strong match") and offers
what weak matches there are only for the person to judge. The three thresholds
are first settings, to be checked against labelled clips and changed when they
are measured.

NEIGHBOURING SECONDS ARE ONE MOMENT. Matching frames of one video no more than
MOMENT_GAP seconds apart are one result, shown by its best frame, so a person
sees five places in the footage rather than the same scene five times.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

WORKER_URL = os.environ.get("FOOTAGE_WORKER_URL", "http://footage-worker:8090")
MAX_TEXT = 300
#: Matching frames of one video this many seconds apart or fewer are one moment.
MOMENT_GAP = 3
#: The nearest frames looked at; enough for the moments asked for, as neighbours merge.
CANDIDATES = 400
#: The model's probability that a frame shows what was described (first settings; see the module's description).
STRONG, POSSIBLE, WEAK = 0.5, 0.1, 0.01
LABELS = (("HIGH", STRONG), ("MEDIUM", POSSIBLE), ("LOW", WEAK))
STRENGTH_WORDS = {"HIGH": "Strong match", "MEDIUM": "Possible match", "LOW": "Weak match"}


class SearchUnavailable(RuntimeError):
    """The footage worker cannot be reached: nothing can turn the words into a vector."""


@dataclass(frozen=True)
class Words:
    """The words as the model reads them, and how the model turns a likeness into a probability."""
    vector: list[float]
    model_version: str
    logit_scale: float
    logit_bias: float

    def probability(self, likeness: float) -> float:
        z = self.logit_scale * likeness + self.logit_bias
        return 1 / (1 + math.exp(-z)) if z > -60 else 0.0

    def likeness_for(self, probability: float) -> float:
        """The least likeness that gives this probability: what the database is asked for."""
        return (math.log(probability / (1 - probability)) - self.logit_bias) / self.logit_scale


async def embed(words: str, *, timeout: float = 30.0) -> Words:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            answer = await client.post(f"{WORKER_URL}/embed-text", json={"text": words})
    except httpx.HTTPError as exc:
        raise SearchUnavailable("Search is not available: the footage worker is not running.") from exc
    if answer.status_code != 200:
        raise SearchUnavailable("Search is not available: the footage worker could not read the description.")
    body = answer.json()
    return Words(body["vector"], body["model_version"], float(body["logit_scale"]), float(body["logit_bias"]))


def vector_text(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


@dataclass
class Moment:
    video_id: str
    start: int
    end: int
    best: int
    likeness: float
    match: float = 0.0
    label: str = "LOW"


def moments(rows: list[tuple[str, int, float]], gap: int = MOMENT_GAP) -> list[Moment]:
    """(video, second, likeness) -> moments: neighbouring seconds of one video joined, each kept by its best frame."""
    out: list[Moment] = []
    by_video: dict[str, list[tuple[int, float]]] = {}
    for video_id, second, likeness in rows:
        by_video.setdefault(video_id, []).append((second, likeness))
    for video_id, frames in by_video.items():
        frames.sort()
        current: Moment | None = None
        for second, likeness in frames:
            if current is not None and second - current.end <= gap:
                current.end = second
                if likeness > current.likeness:
                    current.best, current.likeness = second, likeness
                continue
            current = Moment(video_id, second, second, second, likeness)
            out.append(current)
    return sorted(out, key=lambda m: (-m.likeness, m.video_id, m.start))


def label(moment: Moment, words: Words) -> Moment:
    moment.match = words.probability(moment.likeness)
    moment.label = next((name for name, least in LABELS if moment.match >= least), "LOW")
    return moment


async def search(db: AsyncSession, words: Words, video_ids: list[str], limit: int) -> dict:
    """The moments most like the words, among these videos, with how likely each is to show them."""
    if not video_ids:
        return {"moments": [], "frames_searched": 0}
    params = {"q": vector_text(words.vector), "model": words.model_version, "videos": video_ids, "n": CANDIDATES,
              "least": words.likeness_for(WEAK)}
    # Keep looking past frames of other videos (or organisations) the index finds first.
    await db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    await db.execute(text("SET LOCAL hnsw.ef_search = 200"))
    rows = (await db.execute(text("""
        SELECT video_id, second, likeness FROM (
            SELECT f.video_id::text AS video_id, f.second, 1 - (f.embedding <=> CAST(:q AS vector)) AS likeness
              FROM footage_frames f
             WHERE f.model_version = :model AND f.video_id = ANY(CAST(:videos AS uuid[]))
             ORDER BY f.embedding <=> CAST(:q AS vector)
             LIMIT :n) nearest
         WHERE likeness >= :least
    """), params)).all()
    total = (await db.execute(text("""
        SELECT count(*) FROM footage_frames f
         WHERE f.model_version = :model AND f.video_id = ANY(CAST(:videos AS uuid[]))
    """), params)).scalar_one()
    found = [label(m, words) for m in moments([(r.video_id, r.second, float(r.likeness)) for r in rows])][:limit]
    return {"moments": found, "frames_searched": total}
