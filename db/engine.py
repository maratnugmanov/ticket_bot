from api.variables import DATABASE_NAME
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session
from db.models import Base


sqlite_file_name = "db/" + DATABASE_NAME
sqlite_url = f"sqlite:///{sqlite_file_name}"

connect_args = {"check_same_thread": False}
engine = create_engine(sqlite_url, connect_args=connect_args, echo=True)


def create_db_and_tables():
    Base.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        session.execute(text("PRAGMA foreign_keys=ON"))
        yield session
