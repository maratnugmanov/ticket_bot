from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, AwareDatetime

from src.core.enums import RoleName, DeviceTypeName, Action, Script
from src.db.models import UserDB
from src.tg.models import SendMessageTG


class DeviceJS(BaseModel):
    type: DeviceTypeName
    serial_number: str | None
    is_defective: bool | None


class StateJS(BaseModel):
    message_id: int
    action: Action | None = None
    script: Script | None = None
    ticket_number: int | None = None
    device_type: DeviceTypeName | None = None
    device_index: int | None = None
    writeoff_sn: int | None = None
    devices_list: list[DeviceJS] | None = None
