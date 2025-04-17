from __future__ import annotations
from pydantic import BaseModel, AwareDatetime

from src.core.enums import RoleName, DeviceTypeName


class TimestampMixin:
    created_at: AwareDatetime
    updated_at: AwareDatetime


class BaseModelConfig(BaseModel):
    model_config = {"from_attributes": True}


class Role(BaseModelConfig, TimestampMixin):
    id: int
    name: RoleName
    users: list[User]


class User(BaseModelConfig, TimestampMixin):
    id: int
    telegram_uid: int
    first_name: str | None
    last_name: str | None
    timezone: str
    is_disabled: bool
    roles: list[Role]
    tickets: list[Ticket]
    writeoffs: list[Writeoff]


class Ticket(BaseModelConfig, TimestampMixin):
    id: int
    ticket_number: int
    user: User
    reports: list[Report]


class Report(BaseModelConfig, TimestampMixin):
    id: int
    device: Device
    ticket: Ticket


class Writeoff(BaseModelConfig, TimestampMixin):
    id: int
    device: Device
    user: User


class Device(BaseModelConfig, TimestampMixin):
    id: int
    type: DeviceType
    serial_number: str
    is_defective: bool
    reports: list[Report]
    writeoffs: list[Writeoff]


class DeviceType(BaseModelConfig, TimestampMixin):
    id: int
    name: DeviceTypeName
    is_disabled: bool
    devices: list[Device]
