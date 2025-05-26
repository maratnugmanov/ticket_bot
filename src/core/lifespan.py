from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.core.logger import logger
from src.db.engine import AsyncSessionFactoryDB, backup_db
from src.db.seed import (
    create_db_and_tables,
    create_user_roles,
    create_main_users,
    create_device_types,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db_and_tables()
    async with AsyncSessionFactoryDB() as session_db:
        await create_device_types(session_db)
        await create_user_roles(session_db)
        await create_main_users(session_db)
        await session_db.commit()
        logger.info("Startup database commit successful.")
    yield
    await backup_db()
    logger.info("Lifespan operations complete.")
