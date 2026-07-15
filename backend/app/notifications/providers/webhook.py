"""Webhook notification provider — HTTP POST with alert payload as JSON.

config keys expected in notification_channels.config:
    url:     str             — target URL to POST to
    headers: dict[str,str]  — optional extra HTTP headers (e.g. auth tokens)
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


async def send_webhook(channel_config: dict[str, Any], alert: dict[str, Any]) -> None:
    url: str = channel_config.get("url", "")
    if not url:
        raise ValueError("webhook channel config missing url")

    extra_headers: dict[str, str] = channel_config.get("headers", {})

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=alert, headers=extra_headers)
        resp.raise_for_status()
        logger.info("webhook sent alert_id=%s url=%s status=%s", alert.get("id"), url, resp.status_code)
