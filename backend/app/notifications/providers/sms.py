"""SMS notification provider via Twilio REST API (httpx).

config keys expected in notification_channels.config:
    to_numbers: list[str]   — E.164 recipient numbers, e.g. ["+6591234567"]
"""

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

TWILIO_MESSAGES_URL = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"


async def send_sms(channel_config: dict[str, Any], alert: dict[str, Any]) -> None:
    to_numbers: list[str] = channel_config.get("to_numbers", [])
    if not to_numbers:
        raise ValueError("sms channel config missing to_numbers")

    severity = alert.get("severity", "medium").upper()
    body = f"[Seventh AI] {severity}: {alert.get('title', 'Alert')} — {alert.get('message') or ''}"
    body = body[:160]

    url = TWILIO_MESSAGES_URL.format(account_sid=settings.TWILIO_ACCOUNT_SID)

    async with httpx.AsyncClient(timeout=10.0) as client:
        for number in to_numbers:
            resp = await client.post(
                url,
                data={"From": settings.TWILIO_FROM_NUMBER, "To": number, "Body": body},
                auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
            )
            resp.raise_for_status()
            logger.info("sms sent alert_id=%s to=%s sid=%s", alert.get("id"), number, resp.json().get("sid"))
