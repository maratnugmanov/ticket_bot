from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, AwareDatetime

from src.core.enums import RoleName, DeviceTypeName, Scenario, Modifier
from src.db.models import UserDB
from src.tg.models import SendMessageTG


class DeviceJS(BaseModel):
    type: DeviceTypeName
    serial_number: str | None
    is_defective: bool | None


class StateJS(BaseModel):
    scenario: Scenario
    ticket_number: int | None = None
    modifier: Modifier | None = None
    device_index: int | None = None
    writeoff_sn: int | None = None
    devices_list: list[DeviceJS] | None = None


# class TimestampMixin:
#     created_at: AwareDatetime
#     updated_at: AwareDatetime


# class BaseModelConfig(BaseModel):
#     model_config = {"from_attributes": True}


# class Role(BaseModelConfig, TimestampMixin):
#     id: int
#     name: RoleName


# class User(BaseModelConfig, TimestampMixin):
#     id: int
#     telegram_uid: int
#     first_name: str | None = None
#     last_name: str | None = None
#     timezone: str | None = None
#     is_disabled: bool = False
#     roles: list[Role]


# class Ticket(BaseModelConfig, TimestampMixin):
#     id: int
#     ticket_number: int
#     user: User


# class Report(BaseModelConfig, TimestampMixin):
#     id: int
#     device: Device
#     ticket: Ticket


# class Writeoff(BaseModelConfig, TimestampMixin):
#     id: int
#     device: Device
#     user: User


# class Device(BaseModelConfig, TimestampMixin):
#     id: int
#     type: DeviceType
#     serial_number: str
#     is_defective: bool


# class DeviceType(BaseModelConfig, TimestampMixin):
#     id: int
#     name: DeviceTypeName
#     is_disabled: bool
