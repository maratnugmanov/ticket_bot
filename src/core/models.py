from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, AwareDatetime

from src.core.enums import RoleName, DeviceTypeName, Action, Script
from src.db.models import UserDB
from src.tg.models import SendMessageTG


class DeviceJS(BaseModel):
    is_defective: bool
    id: int | None = None
    type: DeviceTypeName | None = None
    serial_number: str | None = None


class StateJS(BaseModel):
    #  message_id: int
    action: Action
    script: Script
    devices_list: list[DeviceJS] = []
    device_index: int = 0
    ticket_number: int | None = None
    contract_number: int | None = None
    # device_type: DeviceTypeName | None = None
    # device_serial_number: str | None = None
    writeoff_serial_number: int | None = None
