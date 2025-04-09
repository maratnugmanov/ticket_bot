# Built-ins
from typing import Annotated
from contextlib import asynccontextmanager

# Core dependencies
from fastapi import Depends, FastAPI, HTTPException, Query
from sqlmodel import Field, Session, SQLModel, select, text

# Local code
from api.variables import TOKEN, BOT_NAME
from db.models import (
    RoleName,
    DeviceTypeName,
    UserRoleLink,
    Role,
    User,
    Ticket,
    Report,
    Writeoff,
    Device,
    DeviceType,
)
from db.engine import engine, create_db_and_tables, get_session


SessionDep = Annotated[Session, Depends(get_session)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/roles", status_code=201)
async def ensure_default_roles(session: SessionDep):
    created_roles: list[Role] = []
    existing_roles = []
    role_names_in_db = set(session.exec(select(Role.name)).all())
    for role_name_enum in RoleName:
        if role_name_enum.value not in role_names_in_db:
            print(f"Creating role: {role_name_enum.value}")
            new_role = Role(name=role_name_enum)
            session.add(new_role)
            created_roles.append(new_role)
        else:
            print(f"Role already exists: {role_name_enum}")
            existing_roles.append(role_name_enum)  # Just track the name

    if created_roles:
        session.commit()
        # Refresh created roles to get their assigned IDs if you need them
        for role in created_roles:
            session.refresh(role)
        return {
            "message": "Default roles checked/created.",
            "created": [role.name for role in created_roles],
            "already_existed": existing_roles,
        }
    else:
        # No changes made, no commit needed
        return {
            "message": "All default roles already exist.",
            "already_existed": existing_roles,
        }


@app.post("/user")
async def create_user(session: SessionDep):
    user = User(telegram_uid=1)
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
