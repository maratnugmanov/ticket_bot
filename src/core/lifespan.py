from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy.orm import Session

from src.db.engine import engine
from src.db.seed import create_db_and_tables, create_user_roles, create_main_users
from src.core.logger import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.debug("Application startup: Initializing database...")
    create_db_and_tables()
    logger.debug("Application startup: Database, tables: Initialized.")
    with Session(engine) as session:
        create_user_roles(session)
        create_main_users(session)
        session.commit()
    logger.debug("Application startup: Default roles, users: Initialized.")
    yield
    logger.debug("Application shutdown: Complete.")
