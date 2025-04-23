from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.core.logger import logger
from src.db.engine import (
    AsyncSessionFactoryDB,
    AsyncSessionFactoryCC,
    backup_cache_db_to_file,
)
from src.db.seed import (
    create_db_and_tables,
    create_user_roles,
    create_main_users,
    sync_roles_to_cache,
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
        async with AsyncSessionFactoryCC() as session_cc:
            await sync_roles_to_cache(session_db, session_cc)
            await session_cc.commit()
        logger.debug("Application startup: Roles synchronized to cache DB.")
    yield
    await backup_cache_db_to_file("src/db/in_memory.db")
    logger.debug("Application shutdown: Complete.")
