from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.core.logger import logger
from src.db.engine import AsyncSessionFactoryDB, backup_db
from src.db.seed import (
    create_db_and_tables,
    create_user_roles,
    create_main_users,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.debug("Application startup: Initializing database...")
    await create_db_and_tables()
    logger.debug("Application startup: Database, tables: Initialized.")
    async with AsyncSessionFactoryDB() as session_db:
        await create_user_roles(session_db)
        await create_main_users(session_db)
        await session_db.commit()
        logger.debug("Application startup: Default roles, users: Initialized.")
    yield
    await backup_db()
    logger.debug("Application shutdown: Complete.")
