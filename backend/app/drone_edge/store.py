"""What a site edge gateway keeps on its own disk — enough to fly on through a
lost link, survive its own restart, and catch the centre up afterwards.

One SQLite file, written in WAL mode, every change in a transaction:

  sessions   the flights this gateway has claimed: frozen plan, provider state,
             the last update number. A restart resumes a flight here.
  outbox     everything still to be sent, in the order it happened — flight
             updates, drone health, command outcomes, events, file descriptions.
             A row leaves only when the centre has acknowledged it.
  commands   operator commands received, so one is never carried out twice.
  media      files recorded at the site: where they are, their checksum, whether
             the centre asked for them and whether they have been sent.
  rejected   items the centre refused for good, kept with the reason so a person
             at the site can see what was lost and why.
  meta       the last assignment (drones, policy) — so a gateway that restarts
             while offline still knows what it flies — and the open batch.

RECORDING AN UPDATE AND QUEUEING IT IS ONE TRANSACTION. The session's update
number, its provider state and the outbox row that carries them are written
together; a crash between them cannot happen, so the centre never sees a gap
and the gateway never re-flies a step it already reported.

THE OPEN BATCH IS REMEMBERED. A batch whose answer was lost is sent again with
the same id and the same items, so the centre can answer it from its receipt.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id                   TEXT PRIMARY KEY,
    drone_id             TEXT NOT NULL,
    mission_name         TEXT,
    snapshot             TEXT NOT NULL,
    provider_state       TEXT NOT NULL DEFAULT '{}',
    provider_mission_ref TEXT,
    phase                TEXT,
    seq                  INTEGER NOT NULL DEFAULT 0,
    started              INTEGER NOT NULL DEFAULT 0,
    outcome              TEXT,
    claimed_at           TEXT NOT NULL,
    finished_at          TEXT
);
CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    ref        TEXT NOT NULL,
    payload    TEXT NOT NULL,
    samples    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (kind, ref)
);
CREATE TABLE IF NOT EXISTS commands (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    command     TEXT NOT NULL,
    reason      TEXT,
    received_at TEXT NOT NULL,
    done_at     TEXT
);
CREATE TABLE IF NOT EXISTS media (
    client_ref  TEXT PRIMARY KEY,
    path        TEXT NOT NULL,
    media_kind  TEXT NOT NULL,
    checksum    TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    wanted      INTEGER,
    uploaded_at TEXT
);
CREATE TABLE IF NOT EXISTS rejected (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    ref         TEXT NOT NULL,
    payload     TEXT NOT NULL,
    reason      TEXT NOT NULL,
    rejected_at TEXT NOT NULL
);
"""

KINDS = ("health", "update", "command", "event", "media")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


@dataclass
class LocalSession:
    id: str
    drone_id: str
    mission_name: str | None
    snapshot: dict
    provider_state: dict
    provider_mission_ref: str | None
    phase: str | None
    seq: int
    started: bool
    outcome: str | None
    claimed_at: datetime
    finished_at: datetime | None


@dataclass
class OutboxItem:
    id: int
    kind: str
    ref: str
    payload: dict
    samples: int
    created_at: datetime


class EdgeStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    # ── meta ─────────────────────────────────────────────────────────────────

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_meta(self, key: str, value: Any, conn: sqlite3.Connection | None = None) -> None:
        (conn or self.conn).execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value, default=str)))

    # ── sessions ─────────────────────────────────────────────────────────────

    def _session(self, row: sqlite3.Row) -> LocalSession:
        return LocalSession(
            id=row["id"], drone_id=row["drone_id"], mission_name=row["mission_name"],
            snapshot=json.loads(row["snapshot"]), provider_state=json.loads(row["provider_state"]),
            provider_mission_ref=row["provider_mission_ref"], phase=row["phase"], seq=row["seq"],
            started=bool(row["started"]), outcome=row["outcome"], claimed_at=_dt(row["claimed_at"]),
            finished_at=_dt(row["finished_at"]))

    def add_session(self, s: dict, now: datetime) -> None:
        """A session this gateway has claimed (or, after a crash, finds it had)."""
        started = bool(s.get("provider_state") or s.get("provider_mission_ref"))
        self.conn.execute("""
            INSERT INTO sessions (id, drone_id, mission_name, snapshot, provider_state, provider_mission_ref,
                                  seq, started, claimed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO NOTHING
        """, (str(s["id"]), str(s["drone_id"]), s.get("mission_name"), json.dumps(s["config_snapshot"]),
              json.dumps(s.get("provider_state") or {}), s.get("provider_mission_ref"),
              int(s.get("edge_seq") or 0), int(started), _iso(now)))

    def session(self, session_id: str) -> LocalSession | None:
        row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return self._session(row) if row else None

    def live_sessions(self) -> list[LocalSession]:
        return [self._session(r) for r in self.conn.execute(
            "SELECT * FROM sessions WHERE outcome IS NULL ORDER BY claimed_at")]

    def known_session_ids(self) -> list[str]:
        return [r["id"] for r in self.conn.execute("SELECT id FROM sessions")]

    def record_update(self, session_id: str, update: dict, *, at: datetime, launched: bool = False) -> int:
        """Store a flight update and queue it — one transaction. Returns its number."""
        with self.tx() as c:
            row = c.execute("SELECT seq FROM sessions WHERE id = ?", (session_id,)).fetchone()
            seq = row["seq"] + 1
            outcome = update.get("outcome")
            c.execute("""
                UPDATE sessions SET seq = ?, provider_state = ?, phase = ?, started = 1,
                       provider_mission_ref = COALESCE(?, provider_mission_ref),
                       outcome = COALESCE(?, outcome), finished_at = CASE WHEN ? IS NOT NULL THEN ? ELSE finished_at END
                 WHERE id = ?
            """, (seq, json.dumps(update.get("provider_state") or {}), update.get("phase"),
                  update.get("provider_mission_ref"), outcome, outcome, _iso(at), session_id))
            self._queue(c, "update", f"{session_id}:{seq:09d}",
                        {"session_id": session_id, "seq": seq, "at": _iso(at), "launched": launched,
                         "update": update}, samples=len(update.get("samples") or []), now=at)
        return seq

    # ── commands ─────────────────────────────────────────────────────────────

    def add_commands(self, commands: list[dict], now: datetime) -> int:
        """New operator commands for sessions this gateway flies. Returns how
        many were new — one already received is not received again."""
        known = set(self.known_session_ids())
        new = 0
        for c in commands:
            if str(c["session_id"]) not in known:
                continue
            cur = self.conn.execute(
                "INSERT INTO commands (id, session_id, command, reason, received_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO NOTHING",
                (str(c["id"]), str(c["session_id"]), c["command"], c.get("reason"), _iso(now)))
            new += cur.rowcount
        return new

    def open_commands(self, session_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM commands WHERE session_id = ? AND done_at IS NULL ORDER BY received_at, id",
            (session_id,))]

    def finish_command(self, command_id: str, status: str, result: str, at: datetime) -> None:
        with self.tx() as c:
            c.execute("UPDATE commands SET done_at = ? WHERE id = ?", (_iso(at), command_id))
            self._queue(c, "command", command_id,
                        {"command_id": command_id, "status": status, "result": result[:2000], "at": _iso(at)},
                        now=at)

    # ── health, events, media ────────────────────────────────────────────────

    def put_health(self, drone_id: str, payload: dict, now: datetime) -> None:
        """Only the latest health per drone is worth sending."""
        self.conn.execute("""
            INSERT INTO outbox (kind, ref, payload, created_at) VALUES ('health', ?, ?, ?)
            ON CONFLICT (kind, ref) DO UPDATE SET payload = excluded.payload
        """, (drone_id, json.dumps(payload), _iso(now)))

    def put_event(self, payload: dict, now: datetime) -> None:
        with self.tx() as c:
            self._queue(c, "event", payload["client_ref"], payload, now=now)

    def put_media(self, payload: dict, path: str, now: datetime) -> None:
        with self.tx() as c:
            c.execute("""
                INSERT INTO media (client_ref, path, media_kind, checksum, size_bytes, captured_at)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (client_ref) DO NOTHING
            """, (payload["client_ref"], path, payload["media_kind"], payload["checksum_sha256"],
                  payload["size_bytes"], payload["captured_at"]))
            self._queue(c, "media", payload["client_ref"], payload, now=now)

    def media(self, client_ref: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM media WHERE client_ref = ?", (client_ref,)).fetchone()
        return dict(row) if row else None

    def mark_media(self, client_refs: list[str], *, wanted: bool | None = None,
                   uploaded_at: datetime | None = None) -> None:
        for ref in client_refs:
            if wanted is not None:
                self.conn.execute("UPDATE media SET wanted = ? WHERE client_ref = ?", (int(wanted), ref))
            if uploaded_at is not None:
                self.conn.execute("UPDATE media SET uploaded_at = ?, wanted = COALESCE(wanted, 1) "
                                  " WHERE client_ref = ?", (_iso(uploaded_at), ref))

    def prunable_media(self, before: datetime) -> list[dict]:
        """Files past local retention that the centre has, or never wanted."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM media WHERE captured_at < ? AND (uploaded_at IS NOT NULL OR wanted = 0)",
            (_iso(before),))]

    def forget_media(self, client_ref: str) -> None:
        self.conn.execute("DELETE FROM media WHERE client_ref = ?", (client_ref,))

    # ── the outbox ───────────────────────────────────────────────────────────

    def _queue(self, c: sqlite3.Connection, kind: str, ref: str, payload: dict, *, now: datetime,
               samples: int = 0) -> None:
        c.execute("INSERT INTO outbox (kind, ref, payload, samples, created_at) VALUES (?, ?, ?, ?, ?) "
                  "ON CONFLICT (kind, ref) DO NOTHING",
                  (kind, ref, json.dumps(payload, default=str), samples, _iso(now)))

    def depth(self) -> tuple[int, datetime | None]:
        row = self.conn.execute("SELECT count(*) AS n, min(created_at) AS oldest FROM outbox").fetchone()
        return row["n"], _dt(row["oldest"])

    def _items(self, ids: list[int]) -> list[OutboxItem]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(f"SELECT * FROM outbox WHERE id IN ({marks}) ORDER BY id", ids).fetchall()
        return [OutboxItem(r["id"], r["kind"], r["ref"], json.loads(r["payload"]), r["samples"],
                           _dt(r["created_at"])) for r in rows]

    def open_batch(self, *, max_items: int, max_samples: int) -> tuple[str, list[OutboxItem]]:
        """The batch to send: the one already open if all of it is still
        waiting, otherwise the oldest items, in order, within the limits."""
        open_ = self.get_meta("open_batch")
        if open_:
            items = self._items(open_["ids"])
            if len(items) == len(open_["ids"]):
                return open_["batch_id"], items
        ids, samples = [], 0
        for r in self.conn.execute("SELECT id, samples FROM outbox ORDER BY id LIMIT ?", (max_items,)):
            if ids and samples + r["samples"] > max_samples:
                break
            ids.append(r["id"])
            samples += r["samples"]
        batch_id = str(uuid.uuid4())
        if ids:
            self.set_meta("open_batch", {"batch_id": batch_id, "ids": ids})
        return batch_id, self._items(ids)

    def close_batch(self, items: list[OutboxItem], rejected: dict[tuple[str, str], str], now: datetime) -> None:
        """The centre answered: everything in the batch is done with — accepted,
        already there, or refused for good (kept in `rejected` with the reason)."""
        with self.tx() as c:
            for it in items:
                reason = rejected.get((it.kind, it.ref))
                if reason is not None:
                    c.execute("INSERT INTO rejected (kind, ref, payload, reason, rejected_at) VALUES (?, ?, ?, ?, ?)",
                              (it.kind, it.ref, json.dumps(it.payload), reason, _iso(now)))
                c.execute("DELETE FROM outbox WHERE id = ?", (it.id,))
            c.execute("DELETE FROM meta WHERE key = 'open_batch'")

    def drop_open_batch(self) -> None:
        self.conn.execute("DELETE FROM meta WHERE key = 'open_batch'")

    def rejected(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM rejected ORDER BY id")]

    def prune_sessions(self, before: datetime) -> None:
        """Finished flights whose every update has been delivered."""
        self.conn.execute("""
            DELETE FROM sessions
             WHERE finished_at IS NOT NULL AND finished_at < ?
               AND NOT EXISTS (SELECT 1 FROM outbox o WHERE o.kind = 'update' AND o.ref LIKE sessions.id || ':%')
        """, (_iso(before),))
        self.conn.execute("DELETE FROM commands WHERE done_at IS NOT NULL AND session_id NOT IN "
                          "(SELECT id FROM sessions)")
