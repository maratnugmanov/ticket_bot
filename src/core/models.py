from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, AwareDatetime

from src.core.enums import RoleName, DeviceTypeName, Action, Script
from src.db.models import UserDB
from src.tg.models import SendMessageTG


class TicketJS(BaseModel):
    ticket_number: int | None = None
    contract_number: int | None = None
    reports: list[ReportJS] | None = None
    id: int | None = None


class ReportJS(BaseModel):
    ticket_number: int | None = None
    contract_number: int | None = None
    reports: list[ReportJS]
    id: int | None = None


class DeviceJS(BaseModel):
    type: DeviceTypeJS
    is_defective: bool | None = None
    serial_number: str | None = None
    id: int | None = None


class DeviceTypeJS(BaseModel):
    id: int
    name: DeviceTypeName
    is_returnable: bool
    has_serial_number: bool
    is_disabled: bool = False

    model_config = {"from_attributes": True}


class StateJS(BaseModel):
    #  message_id: int
    action: Action
    script: Script
    # devices_list: list[DeviceJS] = []
    device_index: int = 0
    writeoff_devices_page: int = 0
    writeoff_devices_dict: dict[int, int] = {}
    # ticket_number: int | None = None
    # contract_number: int | None = None
    # device_type: DeviceTypeName | None = None
    # device_serial_number: str | None = None
    # writeoff_serial_number: int | None = None
