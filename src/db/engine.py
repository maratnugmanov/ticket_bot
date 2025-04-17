from typing import Annotated
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import Session
from fastapi import Depends

from src.api.constants import DATABASE_NAME
from src.db.models import Base, RoleDB
from src.core.enums import RoleName


sqlite_file_name = f"src/db/{DATABASE_NAME}"
sqlite_url = f"sqlite:///{sqlite_file_name}"

connect_args = {"check_same_thread": False}
engine = create_engine(sqlite_url, connect_args=connect_args, echo=True)


def create_db_and_tables():
    Base.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        session.execute(text("PRAGMA foreign_keys=ON"))
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def create_user_roles(session: Session):
    for role in RoleName:
        query = select(RoleDB).where(RoleDB.name == role)
        existing_role = session.scalar(query)
        if not existing_role:
            session.add(RoleDB(name=role))
