import enum
from datetime import datetime, timezone
import zoneinfo
from sqlalchemy import (
    Enum as SQLAlchemyEnum,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class RoleName(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    ENGINEER = "engineer"
    GUEST = "guest"


class DeviceTypeName(str, enum.Enum):
    IP = "IP"
    TVE = "TVE"
    ROUTER = "Router"  # Russian?


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


# fmt:off


class TimestampMixin():
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default_factory=lambda: datetime.now(timezone.utc), index=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc), index=True)


class Base(DeclarativeBase):
    type_annotation_map = {
        RoleName: SQLAlchemyEnum(RoleName, native_enum=False, length=128, validate_strings=True),
        DeviceTypeName: SQLAlchemyEnum(DeviceTypeName, native_enum=False, length=128, validate_strings=True),
    }


class UserRoleLinkDB(Base):
    __tablename__ = "users_roles_link"
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)


class RoleDB(TimestampMixin, Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[RoleName] = mapped_column(default=RoleName.GUEST, index=True, unique=True)
    users: Mapped[list["UserDB"]] = relationship(default_factory=list, secondary="users_roles_link", back_populates="roles")


class UserDB(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_uid: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    first_name: Mapped[str | None] = mapped_column(String, index=True)
    last_name: Mapped[str | None] = mapped_column(String, index=True)
    timezone: Mapped[str] = mapped_column(String, default="Europe/Samara", index=True)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    roles: Mapped[list[RoleDB]] = relationship(default_factory=list, secondary="users_roles_link", back_populates="users")
    tickets: Mapped[list["TicketDB"]] = relationship(default_factory=list, back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    writeoffs: Mapped[list["WriteoffDB"]] = relationship(default_factory=list, back_populates="user", cascade="all, delete-orphan", passive_deletes=True)

    def __repr__(self) -> str:
        # created_repr = self.created_at.isoformat() if self.created_at else "None"
        return f"UserDB(id={self.id!r}, telegram_uid={self.telegram_uid!r}, first_name={self.first_name!r}, last_name={self.last_name!r}, is_disabled={self.is_disabled!r})"


class TicketDB(TimestampMixin, Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_number: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    user: Mapped[UserDB] = relationship(back_populates="tickets")
    reports: Mapped[list["ReportDB"]] = relationship(default_factory=list, back_populates="ticket", cascade="all, delete-orphan", passive_deletes=True)


class ReportDB(TimestampMixin, Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    device: Mapped["DeviceDB"] = relationship(back_populates="reports")
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    ticket: Mapped[TicketDB] = relationship(back_populates="reports")

    __table_args__ = (UniqueConstraint("device_id", "ticket_id", name="unique_device_ticket_pair"),)


class WriteoffDB(TimestampMixin, Base):
    __tablename__ = "writeoffs"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    device: Mapped["DeviceDB"] = relationship(back_populates="writeoffs")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    user: Mapped[UserDB] = relationship(back_populates="writeoffs")

    __table_args__ = (UniqueConstraint("device_id", "user_id", name="unique_device_user_pair"),)


class DeviceDB(TimestampMixin, Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    type_id: Mapped[int] = mapped_column(ForeignKey("device_types.id", ondelete="RESTRICT"), index=True)
    type: Mapped["DeviceTypeDB"] = relationship(back_populates="devices")
    serial_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    is_defective: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reports: Mapped[list[ReportDB]] = relationship(default_factory=list, back_populates="device", cascade="all, delete-orphan", passive_deletes=True)
    writeoffs: Mapped[list[WriteoffDB]] = relationship(default_factory=list, back_populates="device", cascade="all, delete-orphan", passive_deletes=True)


class DeviceTypeDB(TimestampMixin, Base):
    __tablename__ = "device_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[DeviceTypeName] = mapped_column(index=True, unique=True)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    devices: Mapped[list[DeviceDB]] = relationship(default_factory=list, back_populates="type")
