import time
import enum
from sqlalchemy import (
    Enum as SQLAlchemyEnum,
    Integer,
    String,
    Boolean,
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


class Base(DeclarativeBase):
    pass


# fmt:off


class UserRoleLinkDB(Base):
    __tablename__ = "users_roles_link"
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)


class RoleDB(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[RoleName] = mapped_column(SQLAlchemyEnum(RoleName, native_enum=False, length=128), default=RoleName.GUEST, index=True, unique=True)
    users: Mapped[list["UserDB"]] = relationship(back_populates="roles", secondary="users_roles_link")


class UserDB(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_uid: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    first_name: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    last_name: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    roles: Mapped[list[RoleDB]] = relationship(secondary="users_roles_link", back_populates="users")
    tickets: Mapped[list["TicketDB"]] = relationship(back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    writeoffs: Mapped[list["WriteoffDB"]] = relationship(back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()), index=True, nullable=False)
    updated_at: Mapped[int | None] = mapped_column(Integer, onupdate=lambda: int(time.time()), index=True, nullable=True)

    def __repr__(self) -> str:
        return f"UserDB(id={self.id!r}, telegram_uid={self.telegram_uid!r}, is_disabled={self.is_disabled!r})"


class TicketDB(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_number: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    user: Mapped[UserDB] = relationship(back_populates="tickets")
    reports: Mapped[list["ReportDB"]] = relationship(back_populates="ticket", cascade="all, delete-orphan", passive_deletes=True)
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()), index=True, nullable=False)
    updated_at: Mapped[int | None] = mapped_column(Integer, onupdate=lambda: int(time.time()), index=True, nullable=True)


class ReportDB(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    device: Mapped["DeviceDB"] = relationship(back_populates="reports")
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    ticket: Mapped[TicketDB] = relationship(back_populates="reports")
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()), index=True, nullable=False)
    updated_at: Mapped[int | None] = mapped_column(Integer, onupdate=lambda: int(time.time()), index=True, nullable=True)

    __table_args__ = (UniqueConstraint("device_id", "ticket_id", name="unique_device_ticket_pair"),)


class WriteoffDB(Base):
    __tablename__ = "writeoffs"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    device: Mapped["DeviceDB"] = relationship(back_populates="writeoffs")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    user: Mapped[UserDB] = relationship(back_populates="writeoffs")
    created_at: Mapped[int] = mapped_column(Integer, default=lambda: int(time.time()), index=True, nullable=False)
    updated_at: Mapped[int | None] = mapped_column(Integer, onupdate=lambda: int(time.time()), index=True, nullable=True)

    __table_args__ = (UniqueConstraint("device_id", "user_id", name="unique_device_user_pair"),)


class DeviceDB(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    type_id: Mapped[int] = mapped_column(ForeignKey("device_types.id", ondelete="RESTRICT"), index=True)
    type: Mapped["DeviceTypeDB"] = relationship(back_populates="devices")
    serial_number: Mapped[str] = mapped_column(String, unique=True, index=True)
    is_defective: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    reports: Mapped[list[ReportDB]] = relationship(back_populates="device", cascade="all, delete-orphan", passive_deletes=True)
    writeoffs: Mapped[list[WriteoffDB]] = relationship(back_populates="device", cascade="all, delete-orphan", passive_deletes=True)


class DeviceTypeDB(Base):
    __tablename__ = "device_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[DeviceTypeName] = mapped_column(SQLAlchemyEnum(DeviceTypeName, native_enum=False, length=128), index=True, unique=True)
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    devices: Mapped[list[DeviceDB]] = relationship(back_populates="type")
