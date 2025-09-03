from __future__ import annotations
import enum
from contextlib import asynccontextmanager
from fastapi import FastAPI
import src.core.handlers  # noqa: F401
from src.core.logger import logger
from src.core.enums import DeviceTypeName, RoleName, String
from src.db.engine import AsyncSessionFactory, backup_db
from src.db.seed import (
    create_db_and_tables,
    create_user_roles,
    create_main_users,
    create_device_types,
)


def _check_enum_consistency() -> None:
    """
    Checks for consistency between different enums at startup to fail
    fast. This ensures that every logical enum value has a corresponding
    human-readable string representation.
    """
    logger.info("Verifying Enum consistency...")
    enum_pairs_to_check: list[tuple[type[enum.Enum], type[enum.Enum], str]] = [
        (DeviceTypeName, String, f"{DeviceTypeName.__name__} -> {String.__name__}"),
        (RoleName, String, f"{RoleName.__name__} -> {String.__name__}"),
    ]
    for source_enum, target_enum, description in enum_pairs_to_check:
        missing_members = []
        for member in source_enum:
            if not hasattr(target_enum, member.name):
                missing_members.append(member.name)
        if missing_members:
            raise RuntimeError(
                f"Enum consistency check failed for {description}: The "
                "following members are missing a corresponding entry in"
                f" {target_enum.__name__}: {', '.join(missing_members)}"
            )
    logger.info("Enum consistency checks passed.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_enum_consistency()
    await create_db_and_tables()
    async with AsyncSessionFactory() as session_db:
        await create_device_types(session_db)
        await create_user_roles(session_db)
        await create_main_users(session_db)
        await session_db.commit()
        logger.info("Startup database commit successful.")
    yield
    await backup_db()
    logger.info("Lifespan operations complete.")
