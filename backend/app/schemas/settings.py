from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class TenantSettingUpsert(BaseModel):
    setting_value: Any


class TenantSettingOut(BaseModel):
    setting_key: str
    setting_value: Any
    updated_by_user_id: UUID
    updated_at: datetime
