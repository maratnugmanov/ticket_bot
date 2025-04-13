# Built-ins
from typing import Annotated
from contextlib import asynccontextmanager

# Core dependencies
from fastapi import Depends, FastAPI, status, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

# from sqlmodel import Field, Session, SQLModel, select, text

# Local code
from api.variables import TOKEN, BOT_NAME
from db.models import (
    RoleName,
    DeviceTypeName,
    UserRoleLinkDB,
    RoleDB,
    UserDB,
    TicketDB,
    ReportDB,
    WriteoffDB,
    DeviceDB,
    DeviceTypeDB,
)
from db.engine import engine, create_db_and_tables, get_session


SessionDep = Annotated[Session, Depends(get_session)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/roles", status_code=status.HTTP_201_CREATED)
async def ensure_default_roles(session: SessionDep):
    created_roles: list[RoleDB] = []
    existing_roles_enums: list[RoleName] = []  # Store enums

    stmt = select(RoleDB.name)
    result = session.execute(stmt)
    role_enums_in_db: set[RoleName] = set(result.scalars().all())  # Set of Enums

    for role_name_enum in RoleName:
        # Compare Enum member to Enum member
        if role_name_enum not in role_enums_in_db:
            print(f"Creating role: {role_name_enum.value}")
            new_role = RoleDB(name=role_name_enum)
            session.add(new_role)
            created_roles.append(new_role)
        else:
            print(f"Role already exists: {role_name_enum.value}")
            existing_roles_enums.append(role_name_enum)  # Store the existing enum

    if created_roles:
        session.commit()
        for role in created_roles:
            session.refresh(role)
        return {
            "message": "Default roles checked/created.",
            "created": [role.name.value for role in created_roles],  # Return values
            "already_existed": [
                role_enum.value for role_enum in existing_roles_enums
            ],  # Return values
        }
    else:
        return {
            "message": "All default roles already exist.",
            "already_existed": [
                role_enum.value for role_enum in existing_roles_enums
            ],  # Return values
        }


@app.post("/user")
async def create_user(session: SessionDep):
    user = UserDB(telegram_uid=1)
    session.add(user)
    session.commit()
    session.refresh(user)
    return {
        "message": "user added.",
        "user": user,
    }


# @app.post("/heroes/")
# def create_hero(hero: Hero, session: SessionDep) -> Hero:
#     session.add(hero)
#     session.commit()
#     session.refresh(hero)
#     return hero


# @app.get("/heroes/")
# def read_heroes(
#     session: SessionDep,
#     offset: int = 0,
#     limit: Annotated[int, Query(le=100)] = 100,
# ) -> list[Hero]:
#     heroes = session.exec(select(Hero).offset(offset).limit(limit)).all()
#     return heroes


# @app.get("/heroes/{hero_id}")
# def read_hero(hero_id: int, session: SessionDep) -> Hero:
#     hero = session.get(Hero, hero_id)
#     if not hero:
#         raise HTTPException(status_code=404, detail="Hero not found")
#     return hero


# @app.delete("/heroes/{hero_id}")
# def delete_hero(hero_id: int, session: SessionDep):
#     hero = session.get(Hero, hero_id)
#     if not hero:
#         raise HTTPException(status_code=404, detail="Hero not found")
#     session.delete(hero)
#     session.commit()
#     return {"ok": True}
