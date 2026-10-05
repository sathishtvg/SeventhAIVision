"""What the scheduler needs from the machine it runs on, and from itself.

  A — It can read the files it is asked to verify
  B — Restarting it does not cost a day's backup

Both were found by restarting the scheduler and reading what it logged. The
recording integrity sweep reported every recording it checked as missing: the
files were all there, on a volume the scheduler had never been given. And once
the daily cycle ran at every start, a rotation that kept "the newest seven files"
would have kept seven copies of today.
"""
from __future__ import annotations

import inspect

import pytest
import yaml

from app.core.backup import to_rotate
from tests._repo import REPO_ROOT, requires_repo_tree

# The first half reads the compose files from the working tree, so the whole
# module runs with the repository-inspection suites.
pytestmark = requires_repo_tree

COMPOSE_FILES = ("docker/docker-compose.yml", "docker/docker-compose.core.yml")


def _mounts(service: dict) -> dict[str, bool]:
    """{path inside the container: read-only?} for a compose service."""
    found = {}
    for v in service.get("volumes") or []:
        if isinstance(v, str):
            parts = v.split(":")
            if len(parts) >= 2:
                found[parts[1]] = len(parts) > 2 and "ro" in parts[2].split(",")
        elif isinstance(v, dict) and v.get("target"):
            found[v["target"]] = bool(v.get("read_only"))
    return found


# ─── A. Its files ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("compose", COMPOSE_FILES)
def test_the_scheduler_can_read_the_recordings_it_verifies(compose):
    from app import scheduler_main

    assert "recording_integrity" in inspect.getsource(scheduler_main.main), \
        "the scheduler no longer runs the recording sweep — this test can go"
    services = yaml.safe_load((REPO_ROOT / compose).read_text(encoding="utf-8"))["services"]
    writers = [name for name, s in services.items() if "/data/recordings" in _mounts(s) and name != "scheduler"]
    assert writers, f"{compose} gives no service a recordings volume"
    mounts = _mounts(services["scheduler"])
    assert "/data/recordings" in mounts, \
        f"{compose}: the scheduler verifies recordings ({', '.join(writers)} write them) but cannot see them"
    assert mounts["/data/recordings"] is True, "the scheduler only reads recordings; mount them read-only"


@pytest.mark.parametrize("compose", COMPOSE_FILES)
def test_the_scheduler_can_reach_the_evidence_it_purges_and_verifies(compose):
    services = yaml.safe_load((REPO_ROOT / compose).read_text(encoding="utf-8"))["services"]
    assert "/data/evidence" in _mounts(services["scheduler"])


# ─── B. Its backups ──────────────────────────────────────────────────────────

def _name(day: str, time: str = "020000") -> str:
    return f"backup_{day}_{time}.sql.gz"


def test_a_day_restarted_many_times_keeps_one_backup_and_every_other_day():
    """Seven restarts on the 5th used to leave seven backups of the 5th and none
    of the week before."""
    earlier = [_name(f"202610{d:02d}") for d in range(1, 5)]
    today = [_name("20261005", f"{h:02d}0000") for h in range(3, 10)]
    doomed = to_rotate(earlier + today, keep_days=7)
    assert doomed == sorted(today[:-1]), "only today's earlier copies go"
    assert not set(doomed) & set(earlier)


def test_days_beyond_the_ones_kept_go_whole():
    names = [_name(f"202609{d:02d}") for d in range(1, 11)]
    doomed = to_rotate(names, keep_days=7)
    assert doomed == sorted(names[:3])
    assert to_rotate(names[-7:], keep_days=7) == []


def test_a_file_that_is_not_a_dated_backup_is_left_alone():
    assert to_rotate(["backup_manual.sql.gz", "backup_latest.sql.gz", _name("20261005")], keep_days=1) == []
    assert to_rotate([], keep_days=7) == []
