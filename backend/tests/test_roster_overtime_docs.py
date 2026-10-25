"""The design note for the roster fix, auto-close and overtime says what the code does.

docs/plans/2026-10-25-roster-autoclose-overtime-design.md, agreed with the owner on 25 October 2026.
"""
from __future__ import annotations

import importlib.util
import re

from app.main import app  # noqa: F401 - the import every backend test makes first
from app.core.config_keys import SETTING_VALIDATORS
from app.services.attendance_status import ATTENDANCE_DEFAULTS
from tests._repo import REPO_ROOT, requires_repo_tree

pytestmark = requires_repo_tree

DOC = REPO_ROOT / "docs" / "plans" / "2026-10-25-roster-autoclose-overtime-design.md"
MIGRATION = REPO_ROOT / "backend" / "alembic" / "versions" / "0168_shift_autoclose_overtime.py"


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def test_the_settings_and_their_defaults_are_the_ones_agreed():
    doc = " ".join(_doc().split())
    assert "`attendance.auto_close_hours` (default **15**)" in doc
    assert "`attendance.auto_close_reminder_minutes` (default **15**)" in doc
    assert ATTENDANCE_DEFAULTS["attendance.auto_close_hours"] == 15
    assert ATTENDANCE_DEFAULTS["attendance.auto_close_reminder_minutes"] == 15
    for key in ("attendance.auto_close_hours", "attendance.auto_close_reminder_minutes"):
        SETTING_VALIDATORS[key](15)


def test_the_columns_the_note_names_are_the_ones_the_migration_adds():
    migration = MIGRATION.read_text(encoding="utf-8")
    for column in ("auto_closed_at", "auto_close_reminded_for", "checked_out_away_from_site", "overtime_needs_approval"):
        assert f"ADD COLUMN {column}" in migration, column
        assert f"`{column}`" in _doc(), column
    assert "CREATE TABLE overtime_requests" in migration and "`overtime_requests`" in _doc()


def test_the_existing_files_changed_are_named_and_there():
    part = _doc().split("## 7. Existing files this changes", 1)[1].split("## 8.", 1)[0]
    named = re.findall(r"`((?:backend|frontend|mobile)/[A-Za-z_/.]+)`", part)
    assert len(named) == len(set(named)) >= 12
    for path in named:
        assert (REPO_ROOT / path).exists(), path
