from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


from src.core.config import settings
from src.core.logger import logger
from src.core.enums import RoleName
from src.db.engine import async_engine_db, async_engine_cc, SessionDepDB, SessionDepCC
from src.db.models import BaseDB, RoleDB, UserDB, BaseCC, RoleCC


async def create_db_and_tables():
    logger.debug("Creating tables for main DB...")
    async with async_engine_db.begin() as conn:
        await conn.run_sync(BaseDB.metadata.create_all)
    logger.debug("Main DB tables created.")
    logger.debug("Creating tables for cache DB...")
    async with async_engine_cc.begin() as conn:
        await conn.run_sync(BaseCC.metadata.create_all)
    logger.debug("Cache DB tables created.")


async def create_user_roles(session: AsyncSession):
    for role in RoleName:
        query = select(RoleDB).where(RoleDB.name == role)
        existing_role = await session.scalar(query)
        if not existing_role:
            session.add(RoleDB(name=role))
            logger.debug(f"Application startup: User role '{role.name}': in Session.")


async def create_main_users(session: AsyncSession):
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
            logger.warning(
                "Admin user creation: Not all expected roles found "
                f"in DB. Found: {[role.name for role in admin_roles]}"
            )
        admin_user = UserDB(
            telegram_uid=settings.admin_telegram_uid,
            first_name=settings.admin_first_name,
            last_name=settings.admin_last_name,
            timezone=settings.admin_timezone,
            roles=list(admin_roles),
        )
        session.add(admin_user)
        logger.debug("Application startup: User Admin: in Session.")
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
            logger.warning(
                "Manager user creation: Not all expected roles found "
                f"in DB. Found: {[role.name for role in manager_roles]}"
            )
        manager_user = UserDB(
            telegram_uid=settings.manager_telegram_uid,
            first_name=settings.manager_first_name,
            last_name=settings.manager_last_name,
            timezone=settings.manager_timezone,
            roles=list(manager_roles),
        )
        session.add(manager_user)
        logger.debug("Application startup: User Manager: in Session.")


async def sync_roles_to_cache(session_db: SessionDepDB, session_cc: SessionDepCC):
    logger.debug("Starting initial role population into cache DB...")
    roles_db = await session_db.scalars(select(RoleDB))
    added_count = 0
    for role_db in roles_db:
        role_cc = RoleCC(
            id=role_db.id,
            name=role_db.name,
            created_at=role_db.created_at,
            updated_at=role_db.updated_at,
        )
        session_cc.add(role_cc)
        added_count += 1
        logger.debug(
            f"Adding RoleCC ID {role_db.id} ('{role_db.name}') to cache session."
        )
    if added_count > 0:
        await session_cc.flush()
        logger.debug(
            f"Initial role population complete. Added {added_count} roles to cache."
        )
    else:
        logger.debug(
            "Initial role population complete. "
            "No roles found in main DB to add to cache."
        )
