import enum


class RoleName(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    ENGINEER = "engineer"
    GUEST = "guest"


class DeviceTypeName(str, enum.Enum):
    IP = "IP"
    TVE = "TVE"
    ROUTER = "Router"  # Russian?
