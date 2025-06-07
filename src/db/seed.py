from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


from src.core.config import settings
from src.core.logger import logger
from src.core.enums import RoleName, DeviceTypeName
from src.db.engine import async_engine_db
from src.db.models import BaseDB, RoleDB, UserDB, DeviceTypeDB


async def create_db_and_tables():
    logger.info("Initializing database tables...")
    async with async_engine_db.begin() as conn:
        await conn.run_sync(BaseDB.metadata.create_all)
    logger.info("Successfully initialized database tables.")


async def create_device_types(session: AsyncSession):
    logger.info("Initializing device types...")
    router = DeviceTypeDB(
        name=DeviceTypeName.ROUTER, is_disposable=False, has_serial_number=True
    )
    ip_device = DeviceTypeDB(
        name=DeviceTypeName.IP_DEVICE, is_disposable=False, has_serial_number=True
    )
    tve_device = DeviceTypeDB(
        name=DeviceTypeName.TVE_DEVICE, is_disposable=False, has_serial_number=True
    )
    power_unit = DeviceTypeDB(
        name=DeviceTypeName.POWER_UNIT, is_disposable=True, has_serial_number=False
    )
    network_hub = DeviceTypeDB(
        name=DeviceTypeName.NETWORK_HUB, is_disposable=True, has_serial_number=True
    )
    device_types = [router, ip_device, tve_device, power_unit, network_hub]
    for device_type in device_types:
        existing_device_type = await session.scalar(
            select(DeviceTypeDB.id).where(DeviceTypeDB.name == device_type.name)
        )
        if not existing_device_type:
            session.add(device_type)
            logger.info(f"'{device_type.name}' device type added to session.")
    await session.flush()
    logger.info("Successfully initialized device types.")


async def create_user_roles(session: AsyncSession):
    logger.info("Initializing user roles...")
    for role in RoleName:
        query = select(RoleDB).where(RoleDB.name == role)
        existing_role = await session.scalar(query)
        if not existing_role:
            session.add(RoleDB(name=role))
            logger.info(f"'{role.name}' user role added to session.")
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
            roles=admin_roles,
        )
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
            roles=manager_roles,
        )
        session.add(manager_user)
        logger.info("Manager user added to session.")
        await session.flush()
    logger.info("Successfully initialized main users.")
