from sqlalchemy import select
from sqlalchemy.orm import Session


from src.core.config import settings
from src.core.logger import logger
from src.core.enums import RoleName
from src.db.engine import engine
from src.db.models import Base, RoleDB, UserDB


def create_db_and_tables():
    Base.metadata.create_all(engine)


def create_user_roles(session: Session):
    for role in RoleName:
        query = select(RoleDB).where(RoleDB.name == role)
        existing_role = session.scalar(query)
        if not existing_role:
            session.add(RoleDB(name=role))
            logger.debug(f"Application startup: User role '{role.name}': in Session.")


def create_main_users(session: Session):
    admin_uid = settings.admin_telegram_uid
    admin_first_name = settings.admin_first_name
    admin_last_name = settings.admin_last_name
    admin_timezone = settings.admin_timezone
    admin_exists = session.scalar(
        select(UserDB).where(UserDB.telegram_uid == admin_uid)
    )
    if not admin_exists:
        admin_role_enums = [
            RoleName.ADMIN,
            RoleName.MANAGER,
            RoleName.ENGINEER,
            RoleName.GUEST,
        ]
        admin_roles = session.scalars(
            select(RoleDB).where(RoleDB.name.in_(admin_role_enums))
        ).all()
        admin_user = UserDB(
            telegram_uid=admin_uid,
            first_name=admin_first_name,
            last_name=admin_last_name,
            timezone=admin_timezone,
            roles=list(admin_roles),
        )
        session.add(admin_user)
        logger.debug("Application startup: User Admin: in Session.")
    manager_uid = settings.manager_telegram_uid
    manager_first_name = settings.manager_first_name
    manager_last_name = settings.manager_last_name
    manager_timezone = settings.manager_timezone
    manager_exists = session.scalar(
        select(UserDB).where(UserDB.telegram_uid == manager_uid)
    )
    if not manager_exists:
        manager_role_enums = [
            RoleName.MANAGER,
            RoleName.ENGINEER,
            RoleName.GUEST,
        ]
        manager_roles = session.scalars(
            select(RoleDB).where(RoleDB.name.in_(manager_role_enums))
        ).all()
        manager_user = UserDB(
            telegram_uid=manager_uid,
            first_name=manager_first_name,
            last_name=manager_last_name,
            timezone=manager_timezone,
            roles=list(manager_roles),
        )
        session.add(manager_user)
        logger.debug("Application startup: User Manager: in Session.")
