import aiosqlite
from typing import Annotated, AsyncGenerator
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncConnection,
)
from sqlalchemy.dialects.sqlite.aiosqlite import AsyncAdapt_aiosqlite_connection
from sqlalchemy import text
from fastapi import Depends

from src.core.config import settings
from src.core.logger import logger


sqlite_file_name = f"src/db/{settings.database_name}"
sqlite_db_url = f"sqlite+aiosqlite:///{sqlite_file_name}"
backup_file_name = "src/db/backup.db"

async_engine_db = create_async_engine(
    sqlite_db_url,
    echo=settings.echo_sql,
)


AsyncSessionFactoryDB = async_sessionmaker(
    bind=async_engine_db,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=True,
)


async def get_async_session_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactoryDB() as async_session_db:
        await async_session_db.execute(text("PRAGMA foreign_keys=ON"))
        logger.info("Async session with 'PRAGMA foreign_keys=ON' initialized.")
        yield async_session_db
        await async_session_db.commit()
        logger.info("Async session commit successful.")


SessionDepDB = Annotated[AsyncSession, Depends(get_async_session_db)]


async def backup_db(target_file: str = backup_file_name):
    """Backs up the database to a physical file using the raw aiosqlite
    connection's backup method."""
    logger.info(
        f"Backing up the database to '{target_file}' using aiosqlite backup method..."
    )
    try:
        async with (
            aiosqlite.connect(target_file) as target_conn,
            async_engine_db.connect() as source_sqlalchemy_conn,
        ):
            # Get the SQLAlchemy async connection wrapper for the source
            sqlalchemy_connection_wrapper: AsyncConnection = source_sqlalchemy_conn
            # Access the underlying aiosqlite connection wrapper for the source
            raw_connection_wrapper: AsyncAdapt_aiosqlite_connection = (
                await sqlalchemy_connection_wrapper.get_raw_connection()  # type: ignore[assignment]
            )
            # Access the actual aiosqlite driver connection for the source
            source_aiosqlite_conn: aiosqlite.Connection = (
                raw_connection_wrapper.driver_connection
            )
            # Perform the backup operation from source connection to target connection
            await source_aiosqlite_conn.backup(
                target_conn
            )  # Pass the target connection object
            await target_conn.commit()
        logger.info(f"Successfully backed up the database to '{target_file}'.")
        # Both connections are closed automatically by the combined 'async with'
    except Exception as e:
        logger.error(
            f"Failed to back up the database to '{target_file}': {e}",
            exc_info=True,
        )
