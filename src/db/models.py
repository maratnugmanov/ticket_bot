from __future__ import annotations
from datetime import datetime, timezone
import zoneinfo
from sqlalchemy import (
    Enum as SQLAlchemyEnum,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    mapped_column,
    relationship,
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.dialects.sqlite import DATETIME
from src.core.config import settings
from src.core.logger import logger
from src.core.enums import RoleName, DeviceTypeName


def format_datetime_for_user(
    dt_aware: datetime,
    user_iana_tz_str: str | None,
    default_tz_str: str = "UTC",
    format_str: str = "%Y-%m-%d %H:%M:%S %Z (%z)",
) -> str:
    target_tz_name = user_iana_tz_str if user_iana_tz_str else default_tz_str
    target_tz = zoneinfo.ZoneInfo(target_tz_name)
    target_dt = dt_aware.astimezone(target_tz)
    return target_dt.strftime(format_str)


SQLITE_ISO8601_ISO_UTC_FORMAT = (
    "%(year)04d-%(month)02d-%(day)02dT%(hour)02d:%(minute)02d:%(second)02dZ"
)


# from sqlalchemy import DateTime
# class TimestampMixinDB:
#     created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
#     updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=lambda: datetime.now(timezone.utc),
#         onupdate=lambda: datetime.now(timezone.utc),index=True)


# https://docs.python.org/3/library/datetime.html#datetime.datetime.fromisoformat
# https://github.com/sqlalchemy/sqlalchemy/discussions/11372
class TimestampMixinDB:
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(storage_format=SQLITE_ISO8601_ISO_UTC_FORMAT),
        init=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(storage_format=SQLITE_ISO8601_ISO_UTC_FORMAT),
        init=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        index=True,
    )


# fmt:off

class BaseDB(AsyncAttrs, DeclarativeBase, MappedAsDataclass):
    type_annotation_map = {
        RoleName: SQLAlchemyEnum(RoleName, native_enum=False, length=128, validate_strings=True),
        DeviceTypeName: SQLAlchemyEnum(DeviceTypeName, native_enum=False, length=128, validate_strings=True),
    }


class UserRoleLinkDB(BaseDB):
    __tablename__ = "users_roles_link"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True, index=True)


class RoleDB(BaseDB, TimestampMixinDB):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    name: Mapped[RoleName] = mapped_column(default=RoleName.GUEST, index=True, unique=True)
    users: Mapped[list[UserDB]] = relationship(default_factory=list, secondary="users_roles_link", back_populates="roles", init=False)


class ContractDB(BaseDB, TimestampMixinDB):
    __tablename__ = "contracts"
    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    number: Mapped[int] = mapped_column(unique=True, index=True)
    tickets: Mapped[list[TicketDB]] = relationship(default_factory=list, back_populates="contract", cascade="all, delete-orphan", passive_deletes=True, init=False)


class TicketDB(BaseDB, TimestampMixinDB):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    number: Mapped[int] = mapped_column(index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    user: Mapped[UserDB] = relationship(back_populates="tickets", foreign_keys=[user_id], init=False)
    contract_id: Mapped[int | None] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), default=None, index=True)
    contract: Mapped[ContractDB | None] = relationship(back_populates="tickets", init=False)
    locked_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None, index=True)
    locked_by_user: Mapped[UserDB | None] = relationship(back_populates="current_ticket", foreign_keys=[locked_by_user_id], init=False)
    devices: Mapped[list[DeviceDB]] = relationship(default_factory=list, back_populates="ticket", cascade="all, delete-orphan", passive_deletes=True, init=False)
    is_draft: Mapped[bool] = mapped_column(default=True, index=True)


class UserDB(BaseDB, TimestampMixinDB):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    telegram_uid: Mapped[int] = mapped_column(unique=True, index=True)
    first_name: Mapped[str] = mapped_column()
    last_name: Mapped[str | None] = mapped_column()
    current_ticket: Mapped[TicketDB | None] = relationship(back_populates="locked_by_user", foreign_keys=TicketDB.locked_by_user_id, uselist=False, init=False)
    state_json: Mapped[str | None] = mapped_column(default=None)
    timezone: Mapped[str] = mapped_column(default=settings.user_default_timezone)
    is_hiring: Mapped[bool] = mapped_column(default=False, index=True)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    roles: Mapped[list[RoleDB]] = relationship(default_factory=list, secondary="users_roles_link", back_populates="users", init=False)
    tickets: Mapped[list[TicketDB]] = relationship(default_factory=list, back_populates="user", foreign_keys=TicketDB.user_id, passive_deletes=True, init=False)
    writeoff_devices: Mapped[list[WriteoffDeviceDB]] = relationship(default_factory=list, back_populates="user", passive_deletes=True, init=False)

    @property
    def full_name(self) -> str:
        stripped_first = self.first_name.strip() if self.first_name is not None else ""
        stripped_last = self.last_name.strip() if self.last_name is not None else ""
        combined_names = " ".join((stripped_first, stripped_last)).strip()
        if combined_names:
            return f"'{combined_names}' [{self.telegram_uid}]"
        else:
            return f"[{self.telegram_uid}]"

    @property
    def is_admin(self) -> bool:
        return any(role.name == RoleName.ADMIN for role in self.roles)

    @property
    def is_manager(self) -> bool:
        return any(role.name == RoleName.MANAGER for role in self.roles)

    @property
    def is_engineer(self) -> bool:
        return any(role.name == RoleName.ENGINEER for role in self.roles)

    @property
    def is_guest(self) -> bool:
        return any(role.name == RoleName.GUEST for role in self.roles)


class WriteoffDeviceDB(BaseDB, TimestampMixinDB):
    __tablename__ = "writeoff_devices"
    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    user: Mapped[UserDB] = relationship(back_populates="writeoff_devices", init=False)
    type_id: Mapped[int] = mapped_column(ForeignKey("device_types.id", ondelete="RESTRICT"), index=True)
    type: Mapped[DeviceTypeDB] = relationship(back_populates="writeoff_devices", init=False)
    serial_number: Mapped[str | None] = mapped_column(default=None, index=True)
    is_draft: Mapped[bool] = mapped_column(default=True, index=True)

    # __table_args__ = (UniqueConstraint("device_id", "user_id", name="unique_device_user_pair"),)


class DeviceDB(BaseDB, TimestampMixinDB):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    ticket: Mapped[TicketDB] = relationship(back_populates="devices", init=False)
    type_id: Mapped[int] = mapped_column(ForeignKey("device_types.id", ondelete="RESTRICT"), index=True)
    type: Mapped[DeviceTypeDB] = relationship(back_populates="devices", init=False)
    serial_number: Mapped[str | None] = mapped_column(default=None, index=True)
    removal: Mapped[bool | None] = mapped_column(default=None, index=True)
    is_draft: Mapped[bool] = mapped_column(default=True, index=True)


class DeviceTypeDB(BaseDB, TimestampMixinDB):
    __tablename__ = "device_types"
    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    name: Mapped[DeviceTypeName] = mapped_column(index=True, unique=True)
    is_disposable: Mapped[bool] = mapped_column(index=True)
    has_serial_number: Mapped[bool] = mapped_column(index=True)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    devices: Mapped[list[DeviceDB]] = relationship(default_factory=list, back_populates="type", init=False)
    writeoff_devices: Mapped[list[WriteoffDeviceDB]] = relationship(default_factory=list, back_populates="type", init=False)
