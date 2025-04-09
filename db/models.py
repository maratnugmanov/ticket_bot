import enum
import time
from sqlmodel import SQLModel, Field, Relationship, MetaData
from sqlalchemy import UniqueConstraint, String
from pydantic import PositiveInt, StrictBool


class RoleName(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    ENGINEER = "engineer"
    GUEST = "guest"


class DeviceTypeName(str, enum.Enum):
    IP = "IP"
    TVE = "TVE"
    ROUTER = "Router"  # Russian?


class UserRoleLink(SQLModel, table=True):
    __tablename__ = "users_roles_link"
    role_id: int | None = Field(
        default=None, primary_key=True, foreign_key="roles.id", ondelete="CASCADE"
    )
    user_id: int | None = Field(
        default=None, primary_key=True, foreign_key="users.id", ondelete="CASCADE"
    )


class RoleForCreation(SQLModel, table=True):
    """This class is only used for creation to evade SQLite's VARCHAR(N)
    limitation for Enums. Even though SQLite itself doesn't enforce the
    limitation, it might introduce problems with other SQL database
    engines. After the initial creation of tables the Role class
    takes it's place, which is changing name type hint from str to
    RoleName enum class."""

    __tablename__ = "roles"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(default=RoleName.GUEST, unique=True, index=True)
    users: list["User"] = Relationship(back_populates="roles", link_model=UserRoleLink)


class Role(SQLModel, table=True):
    __tablename__ = "roles"
    id: int | None = Field(default=None, primary_key=True)
    name: RoleName = Field(default=RoleName.GUEST, unique=True)  # plus index=True
    users: list["User"] = Relationship(back_populates="roles", link_model=UserRoleLink)

    __table_args__ = {"extend_existing": True}


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: int | None = Field(default=None, primary_key=True)
    telegram_uid: PositiveInt = Field(unique=True, index=True)
    first_name: str | None = Field(default=None, index=True)
    last_name: str | None = Field(default=None, index=True)
    is_disabled: StrictBool = Field(default=False, index=True)
    roles: list[Role] = Relationship(back_populates="users", link_model=UserRoleLink)
    tickets: list["Ticket"] = Relationship(back_populates="user", cascade_delete=True)
    writeoffs: list["Writeoff"] = Relationship(back_populates="user")
    created_at: int = Field(default_factory=lambda: int(time.time()), index=True)
    updated_at: int | None = Field(default=None, index=True)


class Ticket(SQLModel, table=True):
    __tablename__ = "tickets"
    id: int | None = Field(default=None, primary_key=True)
    ticket_number: PositiveInt = Field(unique=True, index=True)
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    user: User = Relationship(back_populates="tickets")
    reports: list["Report"] = Relationship(back_populates="ticket", cascade_delete=True)
    created_at: int = Field(default_factory=lambda: int(time.time()), index=True)
    updated_at: int | None = Field(default=None, index=True)


class Report(SQLModel, table=True):
    __tablename__ = "reports"
    id: int | None = Field(default=None, primary_key=True)
    device_id: int = Field(foreign_key="devices.id", index=True, ondelete="CASCADE")
    device: "Device" = Relationship(back_populates="reports")
    ticket_id: int = Field(foreign_key="tickets.id", index=True, ondelete="CASCADE")
    ticket: Ticket = Relationship(back_populates="reports")
    created_at: int = Field(default_factory=lambda: int(time.time()), index=True)
    updated_at: int | None = Field(default=None, index=True)

    __table_args__ = (
        UniqueConstraint("device_id", "ticket_id", name="unique_device_ticket_pair"),
    )


class Writeoff(SQLModel, table=True):
    __tablename__ = "writeoffs"
    id: int | None = Field(default=None, primary_key=True)
    device_id: int = Field(foreign_key="devices.id", index=True, ondelete="CASCADE")
    device: "Device" = Relationship(back_populates="writeoffs")
    user_id: int = Field(foreign_key="users.id", index=True)
    user: User = Relationship(back_populates="writeoffs")
    created_at: int = Field(default_factory=lambda: int(time.time()), index=True)
    updated_at: int | None = Field(default=None, index=True)

    __table_args__ = (
        UniqueConstraint("device_id", "user_id", name="unique_device_user_pair"),
    )


class Device(SQLModel, table=True):
    __tablename__ = "devices"
    id: int | None = Field(default=None, primary_key=True)
    type_id: int = Field(foreign_key="device_types.id", index=True, ondelete="RESTRICT")
    type: "DeviceType" = Relationship(back_populates="devices")
    serial_number: str = Field(unique=True, index=True)
    is_defective: StrictBool = Field(default=False, index=True)
    reports: list[Report] = Relationship(back_populates="device", cascade_delete=True)
    writeoffs: list[Writeoff] = Relationship(
        back_populates="device",
        cascade_delete=True,
    )


class DeviceTypeForCreation(SQLModel, table=True):
    """This class is only used for creation to evade SQLite's VARCHAR(N)
    limitation for Enums. Even though SQLite itself doesn't enforce the
    limitation, it might introduce problems with other SQL database
    engines. After the initial creation of tables the DeviceType class
    takes it's place, which is changing name type hint from str to
    DeviceTypeName enum class."""

    __tablename__ = "device_types"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    is_disabled: StrictBool = Field(default=False, index=True)
    devices: list[Device] = Relationship(back_populates="type")


class DeviceType(SQLModel, table=True):
    __tablename__ = "device_types"
    id: int | None = Field(default=None, primary_key=True)
    name: DeviceTypeName = Field(unique=True)  # plus index=True
    is_disabled: StrictBool = Field(default=False)  # plus index=True
    devices: list[Device] = Relationship(back_populates="type")

    __table_args__ = {"extend_existing": True}
