"""A hold on evidence: the one question the retention jobs ask.

Three jobs delete what has outlived its retention period — frames and clips
(app/scheduler_main.py), recordings (app/services/continuous_recording.py) and
drone media (app/services/drone_retention.py). Each leaves alone anything with
a hold that has not been released, and each asks in the same words, which are
here so that they cannot drift apart.

A hold is a row of `evidence_holds` (migration 0144): placed when an evidence
package is sealed, or by hand by someone holding `evidence:hold:manage`;
released by a person, with a reason; never deleted.

The hold is read on the purge's own session, under the same row-level security
as the thing it protects: a session that can see a frame to delete it can see
that frame's hold.
"""
from __future__ import annotations

#: The kinds a hold can be on, by which job deletes them.
FRAMES_AND_CLIPS = ("SNAPSHOT", "CLIP")
RECORDINGS = ("RECORDING",)
DRONE_MEDIA = ("DRONE_MEDIA",)


def not_held(kinds: tuple[str, ...], id_column: str) -> str:
    """A SQL predicate that is true for a row with no hold in force.

    `kinds` is one of the tuples above and `id_column` the purge's own column
    — both written in code, never taken from a request."""
    assert kinds in (FRAMES_AND_CLIPS, RECORDINGS, DRONE_MEDIA) and id_column.replace(".", "").replace("_", "").isalnum()
    listed = ", ".join(f"'{k}'" for k in kinds)
    return (f"NOT EXISTS (SELECT 1 FROM evidence_holds held WHERE held.kind IN ({listed}) "
            f"AND held.ref_id = {id_column} AND held.released_at IS NULL)")
