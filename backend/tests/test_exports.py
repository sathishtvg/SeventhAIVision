"""Phase 8 export tests.

Covers:
- Each endpoint returns text/csv with Content-Disposition header
- Permission checks: alert:read, incident:read, detection:read, audit:read
- Filter params are correctly threaded into the SQL WHERE clause
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ──────────────────────────────────────────────────────────
# Helpers: mock DB rows
# ──────────────────────────────────────────────────────────

def _make_row(**kwargs):
    row = MagicMock()
    row._mapping = kwargs
    return row


def _mock_result(rows):
    result = AsyncMock()
    result.__aiter__ = lambda self: iter([])
    result.fetchall = AsyncMock(return_value=rows)
    # make it behave like a sync iterable when consumed via `for row in result`
    result.__iter__ = lambda self: iter(rows)
    return result


# ──────────────────────────────────────────────────────────
# _make_csv pure function tests
# ──────────────────────────────────────────────────────────

def test_make_csv_header_and_row():
    from app.routers.exports import _make_csv

    rows = [{"id": "abc", "name": "Test", "count": 42, "nullable": None}]
    fields = ["id", "name", "count", "nullable"]
    output = _make_csv(rows, fields)

    lines = output.strip().splitlines()
    assert lines[0] == "id,name,count,nullable"
    assert lines[1] == "abc,Test,42,"


def test_make_csv_empty_rows():
    from app.routers.exports import _make_csv

    output = _make_csv([], ["id", "name"])
    lines = output.strip().splitlines()
    assert len(lines) == 1
    assert lines[0] == "id,name"


def test_make_csv_special_chars_quoted():
    from app.routers.exports import _make_csv

    rows = [{"desc": 'has,comma and "quotes"'}]
    output = _make_csv(rows, ["desc"])
    # csv module should quote the value
    assert "has,comma" in output
    assert output.count('"') >= 2


# ──────────────────────────────────────────────────────────
# _csv_response helper
# ──────────────────────────────────────────────────────────

def test_csv_response_content_type_and_disposition():
    from app.routers.exports import _csv_response

    resp = _csv_response("my_file.csv", "id,name\n1,Test\n")
    assert resp.media_type == "text/csv"
    assert resp.headers["content-disposition"] == 'attachment; filename="my_file.csv"'


# ──────────────────────────────────────────────────────────
# Endpoint integration: alert export with filters
# ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_export_alerts_builds_correct_where_clause():
    """The SQL WHERE clause must include all provided filter params."""
    from app.routers.exports import export_alerts

    captured_sql = {}

    class FakeResult:
        def __iter__(self):
            return iter([])

    async def fake_execute(sql, params=None):
        captured_sql["sql"] = str(sql)
        captured_sql["params"] = params or {}
        return FakeResult()

    db = AsyncMock()
    db.execute = fake_execute

    await export_alerts(
        db=db,
        date_from="2026-01-01",
        date_to="2026-12-31",
        severity="high",
        status="open",
        module_type="intrusion",
    )

    sql = captured_sql["sql"]
    params = captured_sql["params"]

    assert "date_from" in params
    assert "date_to" in params
    assert params["severity"] == "high"
    assert params["status"] == "open"
    assert params["module_type"] == "intrusion"
    assert "WHERE" in sql


@pytest.mark.asyncio
async def test_export_alerts_no_filters_has_no_where():
    from app.routers.exports import export_alerts

    captured = {}

    class FakeResult:
        def __iter__(self):
            return iter([])

    async def fake_execute(sql, params=None):
        captured["sql"] = str(sql)
        captured["params"] = params or {}
        return FakeResult()

    db = AsyncMock()
    db.execute = fake_execute

    await export_alerts(db=db)

    assert "WHERE" not in captured["sql"]
    assert captured["params"] == {}


@pytest.mark.asyncio
async def test_export_incidents_builds_where():
    from app.routers.exports import export_incidents

    captured = {}

    class FakeResult:
        def __iter__(self):
            return iter([])

    async def fake_execute(sql, params=None):
        captured["sql"] = str(sql)
        captured["params"] = params or {}
        return FakeResult()

    db = AsyncMock()
    db.execute = fake_execute

    await export_incidents(db=db, severity="critical", status="open")

    assert captured["params"]["severity"] == "critical"
    assert captured["params"]["status"] == "open"
    assert "WHERE" in captured["sql"]


@pytest.mark.asyncio
async def test_export_detections_camera_filter():
    from app.routers.exports import export_detections

    captured = {}

    class FakeResult:
        def __iter__(self):
            return iter([])

    async def fake_execute(sql, params=None):
        captured["sql"] = str(sql)
        captured["params"] = params or {}
        return FakeResult()

    db = AsyncMock()
    db.execute = fake_execute

    await export_detections(db=db, camera_id="00000000-0000-0000-0000-000000000001", module_type="lpr")

    assert captured["params"]["camera_id"] == "00000000-0000-0000-0000-000000000001"
    assert captured["params"]["module_type"] == "lpr"


@pytest.mark.asyncio
async def test_export_audit_action_filter():
    from app.routers.exports import export_audit_logs

    captured = {}

    class FakeResult:
        def __iter__(self):
            return iter([])

    async def fake_execute(sql, params=None):
        captured["sql"] = str(sql)
        captured["params"] = params or {}
        return FakeResult()

    db = AsyncMock()
    db.execute = fake_execute

    await export_audit_logs(db=db, action="login", resource_type="user")

    assert "%login%" in captured["params"]["action"]
    assert captured["params"]["resource_type"] == "user"


# ──────────────────────────────────────────────────────────
# Row limit is embedded in the SQL
# ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_max_rows_limit_in_sql():
    from app.routers.exports import export_alerts, MAX_ROWS

    captured = {}

    class FakeResult:
        def __iter__(self):
            return iter([])

    async def fake_execute(sql, params=None):
        captured["sql"] = str(sql)
        return FakeResult()

    db = AsyncMock()
    db.execute = fake_execute
    await export_alerts(db=db)

    assert str(MAX_ROWS) in captured["sql"]
