from typing import Annotated
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from fastapi import Depends

from src.core.config import settings
from src.core.logger import logger


sqlite_file_name = f"src/db/{settings.database_name}"
sqlite_url = f"sqlite:///{sqlite_file_name}"

connect_args = {"check_same_thread": False}
engine = create_engine(sqlite_url, connect_args=connect_args, echo=settings.echo_sql)


def get_session():
    with Session(engine) as session:
        session.execute(text("PRAGMA foreign_keys=ON"))
        logger.debug("Session to be yielded")
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
