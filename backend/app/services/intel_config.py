"""The intelligence layer's per-tenant configuration.

OFF UNTIL A TENANT TURNS IT ON. `intel.enabled` is an ordinary tenant setting,
validated in core/config_keys.py and changed through the settings API. A tenant
that has never set it has no row, and no row means off: nothing about a tenant
is read, assessed or shown by this layer until its own administrator asks.
"""
from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ENABLED_KEY = "intel.enabled"


async def is_enabled(db: AsyncSession) -> bool:
    """Whether the calling tenant has switched the layer on."""
    value = (await db.execute(
        text("SELECT setting_value FROM tenant_settings WHERE setting_key = :k"),
        {"k": ENABLED_KEY})).scalar_one_or_none()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return False
    return value is True
