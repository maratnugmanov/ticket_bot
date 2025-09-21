from typing import TypedDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


from src.core.config import settings
from src.core.logger import logger
from src.core.enums import RoleName, DeviceStatus, DeviceTypeName
from src.db.engine import async_engine
from src.db.models import BaseDB, RoleDB, UserDB, DeviceStatusDB, DeviceTypeDB


async def create_db_and_tables():
    logger.info("Initializing database tables...")
    async with async_engine.begin() as conn:
        await conn.run_sync(BaseDB.metadata.create_all)
    logger.info("Successfully initialized database tables.")


async def create_device_statuses(
    session: AsyncSession,
) -> dict[DeviceStatus, DeviceStatusDB]:
    logger.info("Initializing device statuses...")
    status_map: dict[DeviceStatus, DeviceStatusDB] = {}
    for status_enum in DeviceStatus:
        existing_status = await session.scalar(
            select(DeviceStatusDB).where(DeviceStatusDB.name == status_enum)
        )
        if not existing_status:
            new_status = DeviceStatusDB(name=status_enum)
            session.add(new_status)
            status_map[status_enum] = new_status
            logger.info(f"'{status_enum.name}' device status added to session.")
        else:
            status_map[status_enum] = existing_status
    await session.flush()
    logger.info("Successfully initialized device statuses.")
    return status_map


async def create_device_types(
    session: AsyncSession, status_map: dict[DeviceStatus, DeviceStatusDB]
):
    class DeviceTypeDefinition(TypedDict):
        name: DeviceTypeName
        has_serial_number: bool
        statuses: list[DeviceStatus]

    logger.info("Initializing device types...")
    device_type_definitions: list[DeviceTypeDefinition] = [
        {
            "name": DeviceTypeName.ROUTER,
            "has_serial_number": True,
            "statuses": [DeviceStatus.RENT, DeviceStatus.SALE, DeviceStatus.RETURN],
        },
        {
            "name": DeviceTypeName.IP_DEVICE,
            "has_serial_number": True,
            "statuses": [DeviceStatus.RENT, DeviceStatus.RETURN],
        },
        {
            "name": DeviceTypeName.TVE_DEVICE,
            "has_serial_number": True,
            "statuses": [DeviceStatus.RENT, DeviceStatus.RETURN],
        },
        {
            "name": DeviceTypeName.SBERBOX,
            "has_serial_number": True,
            "statuses": [DeviceStatus.SALE, DeviceStatus.RETURN],
        },
        {
            "name": DeviceTypeName.POWER_UNIT,
            "has_serial_number": False,
            "statuses": [DeviceStatus.RENT],
        },
        {
            "name": DeviceTypeName.NETWORK_HUB,
            "has_serial_number": True,
            "statuses": [DeviceStatus.RENT],
        },
    ]
    for definition in device_type_definitions:
        existing_device_type = await session.scalar(
            select(DeviceTypeDB).where(DeviceTypeDB.name == definition["name"])
        )
        if not existing_device_type:
            new_device_type = DeviceTypeDB(
                name=definition["name"],
                has_serial_number=definition["has_serial_number"],
            )
            for status_enum in definition["statuses"]:
                new_device_type.statuses.append(status_map[status_enum])
            session.add(new_device_type)
            logger.info(f"'{new_device_type.name}' device type added to session.")
    await session.flush()
    logger.info("Successfully initialized device types.")


async def create_user_roles(session: AsyncSession):
    logger.info("Initializing user roles...")
    for role_enum in RoleName:
        query = select(RoleDB).where(RoleDB.name == role_enum)
        existing_role = await session.scalar(query)
        if not existing_role:
            session.add(RoleDB(name=role_enum))
            logger.info(f"'{role_enum.name}' user role added to session.")
    await session.flush()
    logger.info("Successfully initialized user roles.")


async def create_main_users(session: AsyncSession):
    logger.info("Initializing main users...")
    admin_exists = await session.scalar(
        select(UserDB.id).where(UserDB.telegram_uid == settings.admin_telegram_uid)
    )
    if not admin_exists:
        admin_role_enums = [
            RoleName.ADMIN,
            RoleName.MANAGER,
            RoleName.ENGINEER,
            RoleName.GUEST,
        ]
        admin_roles_result = await session.scalars(
            select(RoleDB).where(RoleDB.name.in_(admin_role_enums))
        )
        admin_roles = list(admin_roles_result)
        if len(admin_roles) != len(admin_role_enums):
            logger.error(
                "Not all admin user roles found in the database. "
                f"Found: {[role.name for role in admin_roles]}."
            )
        admin_user = UserDB(
            telegram_uid=settings.admin_telegram_uid,
            first_name=settings.admin_first_name,
            last_name=settings.admin_last_name,
            timezone=settings.admin_timezone,
        )
        admin_user.roles.extend(admin_roles)
        session.add(admin_user)
        logger.info("Admin user added to session.")
        await session.flush()
    manager_exists = await session.scalar(
        select(UserDB.id).where(UserDB.telegram_uid == settings.manager_telegram_uid)
    )
    if not manager_exists:
        manager_role_enums = [
            RoleName.MANAGER,
            RoleName.ENGINEER,
            RoleName.GUEST,
        ]
        manager_roles_result = await session.scalars(
            select(RoleDB).where(RoleDB.name.in_(manager_role_enums))
        )
        manager_roles = list(manager_roles_result)
        if len(manager_roles) != len(manager_role_enums):
            logger.error(
                "Not all manager user roles found in the database. "
                f"Found: {[role.name for role in manager_roles]}."
            )
        manager_user = UserDB(
            telegram_uid=settings.manager_telegram_uid,
            first_name=settings.manager_first_name,
            last_name=settings.manager_last_name,
            timezone=settings.manager_timezone,
        )
        manager_user.roles.extend(manager_roles)
        session.add(manager_user)
        logger.info("Manager user added to session.")
        await session.flush()
    logger.info("Successfully initialized main users.")
