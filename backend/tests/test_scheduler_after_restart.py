"""The daily cycle after a restart: wait for the database, keep going past a failed step, try again soon.

Found 31 October 2026: after Docker restarted, the scheduler ran its daily cycle while Postgres was still "starting up".
secure_new_partitions raised, which ended the cycle before the backup, and the loop then waited a whole day - there was
no backup on the 29th, 30th or 31st. Now the scheduler waits for the database at start, a failed partition step does
not stop the steps after it, and a cycle that made no backup is tried again in minutes.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.main import app  # noqa: F401 - the import every backend test makes first
from app import scheduler_main


@pytest.mark.asyncio
async def test_the_scheduler_waits_for_a_database_that_is_still_starting():
    tries = {"n": 0}

    async def starting_then_up():
        tries["n"] += 1
        if tries["n"] < 3:
            raise ConnectionError("the database system is starting up")

    assert await scheduler_main.wait_for_database(starting_then_up, wait_seconds=5, every=0.01) is True
    assert tries["n"] == 3

    async def never_up():
        raise ConnectionError("the database system is starting up")

    assert await scheduler_main.wait_for_database(never_up, wait_seconds=0.05, every=0.01) is False


def test_a_cycle_without_a_backup_is_tried_again_in_minutes_a_few_times_then_daily():
    day, retry = scheduler_main.RUN_INTERVAL_SECONDS, scheduler_main.DAILY_RETRY_SECONDS
    assert retry < day
    last, used = scheduler_main.next_daily_timer(1000.0, completed=True, retries_used=0)
    assert (last, used) == (1000.0, 0), "a good cycle is next due in a day"

    last, used = scheduler_main.next_daily_timer(1000.0, completed=False, retries_used=0)
    assert used == 1
    assert (1000.0 + retry) - last >= day, "due again DAILY_RETRY_SECONDS later"
    assert (1000.0 + retry - 1) - last < day, "and not before"

    for n in range(1, scheduler_main.DAILY_RETRIES):
        _, used = scheduler_main.next_daily_timer(1000.0, completed=False, retries_used=n)
        assert used == n + 1
    last, used = scheduler_main.next_daily_timer(1000.0, completed=False,
                                                 retries_used=scheduler_main.DAILY_RETRIES)
    assert (last, used) == (1000.0, 0), "after the last quick retry it waits the usual day"


@asynccontextmanager
async def _no_admin():
    yield None


class _Session:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


async def _cycle(monkeypatch, *, partitions_fail: bool, backup_ok: bool) -> tuple[bool, int]:
    """run_once with every database step replaced: only the order and the outcome are under test."""
    monkeypatch.setattr(scheduler_main, "admin_session", _no_admin)
    monkeypatch.setattr(scheduler_main, "AsyncSessionLocal", lambda: _Session())

    async def purge(_db):
        return 0

    async def secure():
        if partitions_fail:
            raise ConnectionError("the database system is starting up")
        return 0

    backups = {"n": 0}

    async def backup():
        backups["n"] += 1
        if not backup_ok:
            raise OSError("no space left on device")
        return {"filename": "backup_test.sql.gz", "size_bytes": 1}

    monkeypatch.setattr(scheduler_main, "purge_expired_evidence", purge)
    monkeypatch.setattr(scheduler_main, "secure_new_partitions", secure)
    monkeypatch.setattr(scheduler_main, "run_database_backup", backup)
    completed = await scheduler_main.run_once(None)
    return completed, backups["n"]


@pytest.mark.asyncio
async def test_a_failed_partition_step_no_longer_stops_the_backup(monkeypatch):
    assert await _cycle(monkeypatch, partitions_fail=True, backup_ok=True) == (True, 1)


@pytest.mark.asyncio
async def test_a_cycle_whose_backup_failed_says_so(monkeypatch):
    assert await _cycle(monkeypatch, partitions_fail=False, backup_ok=False) == (False, 1)
