from typing import Annotated, AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text
from fastapi import Depends

from src.core.config import settings
from src.core.logger import logger


sqlite_file_name = f"src/db/{settings.database_name}"
sqlite_url = f"sqlite+aiosqlite:///{sqlite_file_name}"

async_engine = create_async_engine(
    sqlite_url,
    echo=settings.echo_sql,
)

AsyncSessionFactory = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=True,
)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as async_session:
        await async_session.execute(text("PRAGMA foreign_keys=ON"))
        logger.debug("Async Session with 'PRAGMA foreign_keys=ON' initialized.")
        yield async_session


SessionDep = Annotated[AsyncSession, Depends(get_async_session)]
