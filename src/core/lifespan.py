from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.core.logger import logger
from src.db.engine import AsyncSessionFactory
from src.db.seed import create_db_and_tables, create_user_roles, create_main_users


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.debug("Application startup: Initializing database...")
    await create_db_and_tables()
    logger.debug("Application startup: Database, tables: Initialized.")
    async with AsyncSessionFactory() as async_session:
        await create_user_roles(async_session)
        await create_main_users(async_session)
        await async_session.commit()
    logger.debug("Application startup: Default roles, users: Initialized.")
    yield
    logger.debug("Application shutdown: Complete.")
