"""Email notification provider using aiosmtplib.

config keys expected in notification_channels.config:
    to_addresses: list[str]   — recipient list
    subject_prefix: str       — prepended to the alert title (default "[Seventh AI]")
"""

import logging
from typing import Any

import aiosmtplib

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_raw_email(to_addresses: list[str], subject: str, body: str) -> None:
    """Generic transactional-email sender — the SMTP mechanics shared by the
    alert-channel sender below and any other one-off email (password reset,
    etc.) that isn't shaped like an alert and shouldn't be forced into that
    shape just to reuse this connection/auth boilerplate."""
    from email.mime.text import MIMEText
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM
    msg["To"] = ", ".join(to_addresses)

    await aiosmtplib.send(
        msg,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER or None,
        password=settings.SMTP_PASSWORD or None,
        use_tls=(settings.SMTP_PORT == 465),
        start_tls=(settings.SMTP_PORT == 587),
    )


async def send_email(channel_config: dict[str, Any], alert: dict[str, Any]) -> None:
    to_addresses: list[str] = channel_config.get("to_addresses", [])
    if not to_addresses:
        raise ValueError("email channel config missing to_addresses")

    prefix = channel_config.get("subject_prefix", "[Seventh AI]")
    subject = f"{prefix} {alert.get('title', 'Alert')}"
    severity = alert.get("severity", "medium").upper()
    body_lines = [
        f"Severity: {severity}",
        f"Module:   {alert.get('module_type', '')}",
        f"Camera:   {alert.get('camera_id', '')}",
        f"Time:     {alert.get('created_at', '')}",
        "",
        alert.get("message") or "",
    ]
    body = "\n".join(body_lines)

    await send_raw_email(to_addresses, subject, body)
    logger.info("email sent alert_id=%s to=%s", alert.get("id"), to_addresses)
