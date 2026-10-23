"""The footage worker's steps in the database, each in a transaction of its own.

The worker connects as the application's role (svc_app), under the tenant
policy like the API: every step first names the video's organisation in the
transaction (`app.current_tenant`), and each statement names the video as
well. Which videos are waiting, across every organisation, is the one question
asked without an organisation: `footage_waiting()` answers it as the database's
owner, with ids and states and nothing else (migration 0164).

A video is CLAIMED by moving it from QUEUED to PROCESSING; a video somebody
else has claimed, or that is no longer waiting, is not claimed twice.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from datetime import timedelta
from typing import Iterator, Sequence

import numpy as np
import psycopg

#: A video PROCESSING with no progress for this long was dropped by a worker that stopped.
STALE_AFTER = timedelta(minutes=int(os.environ.get("FOOTAGE_STALE_MINUTES", "15")))
#: Tries before a video is FAILED for an error that is not the file's fault.
MOST_ATTEMPTS = 3


def connect() -> psycopg.Connection:
    """The application's own address, whichever driver it names (Compose gives psycopg, the chart asyncpg)."""
    dsn = re.sub(r"^postgresql\+\w+://", "postgresql://", os.environ.get("DATABASE_URL", ""))
    return psycopg.connect(dsn, autocommit=True)


@contextmanager
def as_tenant(conn: psycopg.Connection, tenant_id) -> Iterator[psycopg.Cursor]:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT set_config('app.current_tenant', %s, true)", (str(tenant_id),))
        yield cur


def waiting(conn: psycopg.Connection) -> list[tuple]:
    """(tenant_id, video_id, status, attempts) of every video QUEUED, or PROCESSING and stale."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT tenant_id, video_id, status, attempts FROM footage_waiting(%s)", (STALE_AFTER,))
        return cur.fetchall()


def release_stale(conn: psycopg.Connection, tenant_id, video_id) -> str | None:
    """A video a stopped worker dropped: back in the queue, or FAILED after its last try."""
    with as_tenant(conn, tenant_id) as cur:
        cur.execute("""
            UPDATE footage_videos
               SET status = CASE WHEN attempts >= %s THEN 'FAILED' ELSE 'QUEUED' END,
                   error = CASE WHEN attempts >= %s THEN %s ELSE NULL END, updated_at = now()
             WHERE id = %s AND tenant_id = %s AND status = 'PROCESSING' AND updated_at < now() - %s
         RETURNING status
        """, (MOST_ATTEMPTS, MOST_ATTEMPTS, "The analysis stopped three times before it finished. Start it again.",
              video_id, tenant_id, STALE_AFTER))
        row = cur.fetchone()
        return row[0] if row else None


def claim(conn: psycopg.Connection, tenant_id, video_id) -> dict | None:
    with as_tenant(conn, tenant_id) as cur:
        cur.execute("""
            UPDATE footage_videos
               SET status = 'PROCESSING', attempts = attempts + 1, progress = 0, error = NULL, updated_at = now()
             WHERE id = %s AND tenant_id = %s AND status = 'QUEUED'
         RETURNING id, tenant_id, storage_path, size_bytes, original_filename, attempts
        """, (video_id, tenant_id))
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip(("id", "tenant_id", "storage_path", "size_bytes", "original_filename", "attempts"), row))


ALLOWED = {"duration_seconds", "width", "height", "frame_rate", "codec", "sha256", "progress"}


def update(conn: psycopg.Connection, tenant_id, video_id, **values) -> None:
    assert values and set(values) <= ALLOWED, values
    sets = ", ".join(f"{k} = %s" for k in values)
    with as_tenant(conn, tenant_id) as cur:
        cur.execute(f"UPDATE footage_videos SET {sets}, updated_at = now() WHERE id = %s AND tenant_id = %s",
                    (*values.values(), video_id, tenant_id))


def clear_frames(conn: psycopg.Connection, tenant_id, video_id) -> None:
    with as_tenant(conn, tenant_id) as cur:
        cur.execute("DELETE FROM footage_frames WHERE video_id = %s AND tenant_id = %s", (video_id, tenant_id))


def _vector(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v.tolist()) + "]"


def add_frames(conn: psycopg.Connection, tenant_id, video_id,
               rows: Sequence[tuple[int, str, np.ndarray, int | None, int | None]], model_version: str) -> None:
    """(second, thumbnail path, vector, people, vehicles) for each sampled second. INSERT, not COPY: PostgreSQL
    refuses COPY into a table under a row-level policy."""
    with as_tenant(conn, tenant_id) as cur:
        cur.executemany(
            "INSERT INTO footage_frames (tenant_id, video_id, second, thumb_path, embedding, model_version, people, "
            "vehicles) VALUES (%s, %s, %s, %s, CAST(%s AS vector), %s, %s, %s)",
            [(str(tenant_id), str(video_id), second, path, _vector(vector), model_version, people, vehicles)
             for second, path, vector, people, vehicles in rows])


def clear_findings(conn: psycopg.Connection, tenant_id, video_id) -> None:
    with as_tenant(conn, tenant_id) as cur:
        cur.execute("DELETE FROM footage_findings WHERE video_id = %s AND tenant_id = %s", (video_id, tenant_id))


def add_findings(conn: psycopg.Connection, tenant_id, video_id, findings: Sequence, model_version: str) -> None:
    """Events (worker.footage.events.Event), as findings of the video."""
    import json

    with as_tenant(conn, tenant_id) as cur:
        cur.executemany(
            "INSERT INTO footage_findings (tenant_id, video_id, kind, start_second, end_second, best_second, score, "
            "most, reasons, model_version) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS jsonb), %s)",
            [(str(tenant_id), str(video_id), f.kind, f.start, f.end, f.best, round(f.score, 4), f.most,
              json.dumps(f.reasons), model_version) for f in findings])


def finish(conn: psycopg.Connection, tenant_id, video_id, *, frames: int, model_version: str, playable: str,
           proxy_path: str | None) -> None:
    with as_tenant(conn, tenant_id) as cur:
        cur.execute("""
            UPDATE footage_videos
               SET status = 'READY', progress = 100, processed_at = now(), updated_at = now(), error = NULL,
                   frames_indexed = %s, index_model = %s, playable = %s, proxy_path = %s
             WHERE id = %s AND tenant_id = %s AND status = 'PROCESSING'
        """, (frames, model_version, playable, proxy_path, video_id, tenant_id))


def fail(conn: psycopg.Connection, tenant_id, video_id, why: str, *, again: bool = False) -> None:
    """FAILED with the reason the person is shown; or, for a fault that is not the file's, back in the queue."""
    with as_tenant(conn, tenant_id) as cur:
        if again:
            cur.execute("""
                UPDATE footage_videos SET status = 'QUEUED', updated_at = now()
                 WHERE id = %s AND tenant_id = %s AND status = 'PROCESSING' AND attempts < %s
            """, (video_id, tenant_id, MOST_ATTEMPTS))
            if cur.rowcount:
                return
        cur.execute("""
            UPDATE footage_videos SET status = 'FAILED', error = %s, updated_at = now()
             WHERE id = %s AND tenant_id = %s AND status = 'PROCESSING'
        """, (why, video_id, tenant_id))
