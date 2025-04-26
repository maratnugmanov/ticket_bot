from __future__ import annotations
from datetime import datetime, timezone
import zoneinfo
from sqlalchemy import (
    event,
    inspect,
    Enum as SQLAlchemyEnum,
    Integer,
    String,
    Boolean,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    mapped_column,
    relationship,
    Mapper,
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.engine import Connection
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
# fmt:off

# from sqlalchemy import DateTime
# class TimestampMixinDB():
#     created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
#     updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), index=True)

# https://docs.python.org/3/library/datetime.html#datetime.datetime.fromisoformat
class TimestampMixinDB():
    created_at: Mapped[datetime] = mapped_column(DATETIME(storage_format=SQLITE_ISO8601_ISO_UTC_FORMAT), init=False, default=lambda: datetime.now(timezone.utc), index=True)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(storage_format=SQLITE_ISO8601_ISO_UTC_FORMAT), init=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), index=True)


class TimestampMixinCC():
    created_at: Mapped[datetime] = mapped_column(DATETIME(storage_format=SQLITE_ISO8601_ISO_UTC_FORMAT), index=True)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(storage_format=SQLITE_ISO8601_ISO_UTC_FORMAT), index=True)


class BaseDB(AsyncAttrs, DeclarativeBase, MappedAsDataclass):
    type_annotation_map = {
        RoleName: SQLAlchemyEnum(RoleName, native_enum=False, length=128, validate_strings=True),
        DeviceTypeName: SQLAlchemyEnum(DeviceTypeName, native_enum=False, length=128, validate_strings=True),
    }

class BaseCC(AsyncAttrs, DeclarativeBase, MappedAsDataclass):
    type_annotation_map = {
        RoleName: SQLAlchemyEnum(RoleName, native_enum=False, length=128, validate_strings=True),
        DeviceTypeName: SQLAlchemyEnum(DeviceTypeName, native_enum=False, length=128, validate_strings=True),
    }


class UserRoleLinkDB(BaseDB):
    __tablename__ = "users_roles_link"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True, index=True)


class UserRoleLinkCC(BaseCC):
    __tablename__ = "users_roles_link_cache"
    user_id: Mapped[int] = mapped_column(ForeignKey("users_cache.id", ondelete="CASCADE"), primary_key=True, index=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles_cache.id", ondelete="CASCADE"), primary_key=True, index=True)


class RoleDB(BaseDB, TimestampMixinDB):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    name: Mapped[RoleName] = mapped_column(default=RoleName.GUEST, index=True, unique=True)
    users: Mapped[list[UserDB]] = relationship(default_factory=list, secondary="users_roles_link", back_populates="roles")


@event.listens_for(RoleDB, 'before_delete')
def receive_role_before_delete(mapper: Mapper, connection: Connection, target: RoleDB):
    """Listener function executed before a RoleDB instance is
    deleted. Updates 'updated_at' for associated users. Now defined
    inside the RoleDB class."""
    state = inspect(target)
    if state.identity is not None:
            for user in target.users:
                user_state = inspect(user)
                if not user_state.deleted:
                    user.updated_at = datetime.now(timezone.utc)


class RoleCC(BaseCC, TimestampMixinCC):
    __tablename__ = "roles_cache"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    name: Mapped[RoleName] = mapped_column(index=True, unique=True)
    users: Mapped[list[UserCC]] = relationship(default_factory=list, secondary="users_roles_link_cache", back_populates="roles")


class UserDB(BaseDB, TimestampMixinDB):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    telegram_uid: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    first_name: Mapped[str | None] = mapped_column(String, index=True)
    last_name: Mapped[str | None] = mapped_column(String, index=True)
    timezone: Mapped[str] = mapped_column(String, default=settings.user_default_timezone, index=True)
    is_hiring: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    roles: Mapped[list[RoleDB]] = relationship(default_factory=list, secondary="users_roles_link", back_populates="users")
    tickets: Mapped[list[TicketDB]] = relationship(default_factory=list, back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    writeoffs: Mapped[list[WriteoffDB]] = relationship(default_factory=list, back_populates="user", cascade="all, delete-orphan", passive_deletes=True)

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

@event.listens_for(UserDB.roles, 'append')
def receive_role_append(target: UserDB, value: RoleDB, initiator):
    state = inspect(target)
    logger.debug(f"Append listener triggered for User ID: {target.id}, Role: {value.name}")
    if not state.deleted:
            now = datetime.now(timezone.utc)
            logger.debug(f"Updating updated_at for User ID: {target.id} to {now}")
            target.updated_at = now
            logger.debug(f"User ID: {target.id} updated_at history after set: {inspect(target).attrs.updated_at.history}")
    else:
        logger.debug(f"Append listener skipped for deleted User ID: {target.id}")

@event.listens_for(UserDB.roles, 'remove')
def receive_role_remove(target: UserDB, value: RoleDB, initiator):
    state = inspect(target)
    logger.debug(f"Remove listener triggered for User ID: {target.id}, Role: {value.name}")
    if not state.deleted:
            now = datetime.now(timezone.utc)
            logger.debug(f"Updating updated_at for User ID: {target.id} to {now}")
            target.updated_at = now
            logger.debug(f"User ID: {target.id} updated_at history after set: {inspect(target).attrs.updated_at.history}")
    else:
        logger.debug(f"Remove listener skipped for deleted User ID: {target.id}")


class UserCC(BaseCC, TimestampMixinCC):
    __tablename__ = "users_cache"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    telegram_uid: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    first_name: Mapped[str | None] = mapped_column(String, index=True)
    last_name: Mapped[str | None] = mapped_column(String, index=True)
    timezone: Mapped[str] = mapped_column(String, index=True)
    is_hiring: Mapped[bool] = mapped_column(Boolean, index=True)
    is_disabled: Mapped[bool] = mapped_column(Boolean, index=True)
    roles: Mapped[list[RoleCC]] = relationship(default_factory=list, secondary="users_roles_link_cache", back_populates="users")

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


class TicketDB(BaseDB, TimestampMixinDB):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    ticket_number: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    user: Mapped[UserDB] = relationship(back_populates="tickets")
    reports: Mapped[list[ReportDB]] = relationship(back_populates="ticket", cascade="all, delete-orphan", passive_deletes=True)


class ReportDB(BaseDB, TimestampMixinDB):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    device: Mapped[DeviceDB] = relationship(back_populates="reports")
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    ticket: Mapped[TicketDB] = relationship(back_populates="reports")

    __table_args__ = (UniqueConstraint("device_id", "ticket_id", name="unique_device_ticket_pair"),)


class WriteoffDB(BaseDB, TimestampMixinDB):
    __tablename__ = "writeoffs"
    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    device: Mapped[DeviceDB] = relationship(back_populates="writeoffs")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    user: Mapped[UserDB] = relationship(back_populates="writeoffs")

    __table_args__ = (UniqueConstraint("device_id", "user_id", name="unique_device_user_pair"),)


class DeviceDB(BaseDB, TimestampMixinDB):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    type_id: Mapped[int] = mapped_column(ForeignKey("device_types.id", ondelete="RESTRICT"), index=True)
    type: Mapped[DeviceTypeDB] = relationship(back_populates="devices")
    serial_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    is_defective: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reports: Mapped[list[ReportDB]] = relationship(default_factory=list, back_populates="device", cascade="all, delete-orphan", passive_deletes=True)
    writeoffs: Mapped[list[WriteoffDB]] = relationship(default_factory=list, back_populates="device", cascade="all, delete-orphan", passive_deletes=True)


class DeviceTypeDB(BaseDB, TimestampMixinDB):
    __tablename__ = "device_types"
    id: Mapped[int] = mapped_column(init=False, primary_key=True)
    name: Mapped[DeviceTypeName] = mapped_column(index=True, unique=True)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    devices: Mapped[list[DeviceDB]] = relationship(default_factory=list, back_populates="type")
