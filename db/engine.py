from api.variables import DATABASE_NAME
from sqlmodel import create_engine, SQLModel, Session, text
from db.models import (
    UserRoleLink,
    RoleForCreation,
    User,
    Ticket,
    Report,
    Writeoff,
    Device,
    DeviceTypeForCreation,
)


sqlite_file_name = "db/" + DATABASE_NAME
sqlite_url = f"sqlite:///{sqlite_file_name}"

connect_args = {"check_same_thread": False}
engine = create_engine(sqlite_url, connect_args=connect_args, echo=True)


def create_db_and_tables():
    schemas_for_creation = [
        UserRoleLink.__table__,
        RoleForCreation.__table__,
        User.__table__,
        Ticket.__table__,
        Report.__table__,
        Writeoff.__table__,
        Device.__table__,
        DeviceTypeForCreation.__table__,
    ]
    SQLModel.metadata.create_all(bind=engine, tables=schemas_for_creation)


def get_session():
    with Session(engine) as session:
        session.exec(text("PRAGMA foreign_keys=ON"))
        yield session
