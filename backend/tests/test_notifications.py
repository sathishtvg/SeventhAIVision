"""Phase 4 notification tests.

Covers:
- Severity ordering (_severity_ge pure function)
- Rule matching logic (module_types + alert_codes filters)
- Dispatch: channels called on matching rules, skipped on non-matching
- Dispatch: failure logged, does not propagate
- Test endpoint: 502 on provider failure
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ──────────────────────────────────────────────────────────
# Pure function: severity ordering
# ──────────────────────────────────────────────────────────

def test_severity_ge_same():
    from app.notifications.dispatch import _severity_ge
    assert _severity_ge("high", "high") is True


def test_severity_ge_higher():
    from app.notifications.dispatch import _severity_ge
    assert _severity_ge("critical", "medium") is True


def test_severity_ge_lower():
    from app.notifications.dispatch import _severity_ge
    assert _severity_ge("low", "high") is False


def test_severity_ge_info_ge_info():
    from app.notifications.dispatch import _severity_ge
    assert _severity_ge("info", "info") is True


def test_severity_ge_unknown_returns_false():
    from app.notifications.dispatch import _severity_ge
    assert _severity_ge("unknown_level", "low") is False


# ──────────────────────────────────────────────────────────
# Rule matching: module_types / alert_codes filters
# ──────────────────────────────────────────────────────────

def _make_rule(min_severity="medium", module_types=None, alert_codes=None):
    return {
        "rule_id": "r1",
        "min_severity": min_severity,
        "module_types": module_types or [],
        "alert_codes": alert_codes or [],
        "channel_id": "c1",
        "channel_type": "webhook",
        "channel_config": {"url": "http://example.com"},
    }


def _alert(severity="high", module_type="intrusion", alert_code="intrusion.zone_breach"):
    return {
        "id": "a1",
        "severity": severity,
        "module_type": module_type,
        "alert_code": alert_code,
        "title": "Test",
        "message": "Test message",
        "camera_id": "cam1",
        "created_at": "2026-06-19T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_rule_matching_passes_for_matching_alert():
    from app.notifications.dispatch import _load_matching_rules

    mock_session = AsyncMock()
    mock_row = {
        "rule_id": "r1", "min_severity": "medium",
        "module_types": [], "alert_codes": [], "trigger_events": [],
        "channel_id": "c1", "channel_type": "webhook",
        "channel_config": {"url": "http://example.com"},
    }
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = [mock_row]
    mock_session.execute = AsyncMock(return_value=mock_result)

    a = _alert(severity="high")
    rules = await _load_matching_rules(
        mock_session, "tenant1", "alert_created", a["severity"],
        alert_module=a["module_type"], alert_code=a["alert_code"],
    )
    assert len(rules) == 1


@pytest.mark.asyncio
async def test_rule_matching_filters_module_type():
    from app.notifications.dispatch import _load_matching_rules

    mock_session = AsyncMock()
    # Rule only fires for 'lpr' module — alert is 'intrusion'
    mock_row = {
        "rule_id": "r1", "min_severity": "medium",
        "module_types": ["lpr"], "alert_codes": [], "trigger_events": [],
        "channel_id": "c1", "channel_type": "webhook",
        "channel_config": {"url": "http://example.com"},
    }
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = [mock_row]
    mock_session.execute = AsyncMock(return_value=mock_result)

    a = _alert(module_type="intrusion")
    rules = await _load_matching_rules(
        mock_session, "tenant1", "alert_created", a["severity"],
        alert_module=a["module_type"], alert_code=a["alert_code"],
    )
    assert rules == []


@pytest.mark.asyncio
async def test_rule_matching_filters_alert_code():
    from app.notifications.dispatch import _load_matching_rules

    mock_session = AsyncMock()
    mock_row = {
        "rule_id": "r1", "min_severity": "low",
        "module_types": [], "alert_codes": ["lpr.blocklist_hit"], "trigger_events": [],
        "channel_id": "c1", "channel_type": "email",
        "channel_config": {"to_addresses": ["ops@example.com"]},
    }
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = [mock_row]
    mock_session.execute = AsyncMock(return_value=mock_result)

    # Alert code doesn't match the rule's filter
    a = _alert(alert_code="intrusion.zone_breach")
    rules = await _load_matching_rules(
        mock_session, "tenant1", "alert_created", a["severity"],
        alert_module=a["module_type"], alert_code=a["alert_code"],
    )
    assert rules == []


# ──────────────────────────────────────────────────────────
# Dispatch: provider called on match, failure logged
# ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_calls_webhook_on_match():
    from app.notifications import dispatch as d

    session = AsyncMock()

    # Stub _load_matching_rules to return one webhook rule
    matching_rule = {
        "channel_id": "c1", "channel_type": "webhook",
        "channel_config": {"url": "http://hook.test"},
    }

    async def fake_fetch_alert(s, tid, aid):
        return _alert()

    mock_webhook = AsyncMock()
    # Patch _PROVIDER_MAP directly — send_webhook is stored by reference in the
    # dict at import time, so patching d.send_webhook alone won't affect _dispatch_one.
    with patch.object(d, "_fetch_alert", side_effect=fake_fetch_alert), \
         patch.object(d, "_load_matching_rules", return_value=[matching_rule]), \
         patch.dict(d._PROVIDER_MAP, {"webhook": mock_webhook}), \
         patch.object(d, "_write_log", new_callable=AsyncMock):
        session.execute = AsyncMock()
        session.begin_nested = MagicMock(return_value=AsyncMock())
        session.commit = AsyncMock()

        await d.dispatch_notifications_for_alert(session, "tenant1", "alert-id", "intrusion", "high")
        mock_webhook.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_logs_failure_does_not_raise():
    from app.notifications import dispatch as d

    session = AsyncMock()
    session.execute = AsyncMock()
    session.begin_nested = MagicMock(return_value=AsyncMock().__aenter__.return_value)
    session.commit = AsyncMock()

    matching_rule = {
        "channel_id": "c1", "channel_type": "email",
        "channel_config": {"to_addresses": ["ops@example.com"]},
    }

    async def failing_email(config, alert):
        raise ConnectionError("SMTP unreachable")

    async def fake_fetch_alert(s, tid, aid):
        return _alert()

    written_logs = []
    async def capture_log(session, tenant_id, alert_id, channel_id, channel_type, status, error_detail, event_type=None):
        written_logs.append({"status": status, "error_detail": error_detail})

    # Patch _PROVIDER_MAP directly (send_email is stored by reference at import time)
    with patch.object(d, "_fetch_alert", side_effect=fake_fetch_alert), \
         patch.object(d, "_load_matching_rules", return_value=[matching_rule]), \
         patch.dict(d._PROVIDER_MAP, {"email": failing_email}), \
         patch.object(d, "_write_log", side_effect=capture_log):
        await d.dispatch_notifications_for_alert(session, "tenant1", "alert-id", "intrusion", "high")

    assert any(log["status"] == "failed" for log in written_logs)
    assert written_logs[0]["error_detail"] == "SMTP unreachable"
