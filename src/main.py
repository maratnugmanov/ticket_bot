# Built-ins
from typing import Annotated
from contextlib import asynccontextmanager

# Core dependencies
from fastapi import FastAPI, Request, Depends, status, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

# Local code
from src.tg.models import Update
from src.api.constants import TOKEN, BOT_NAME
from src.db.engine import create_db_and_tables, create_user_roles, engine
from src.db.models import (
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    with Session(engine) as session:
        create_user_roles(session)
        session.commit()
    yield


app = FastAPI(lifespan=lifespan)


# ssh -R 80:localhost:8000 nokey@localhost.run
@app.post("/", status_code=status.HTTP_200_OK)
async def read_root(request: Request):
    result = await request.json()
    update = Update.model_validate(result)
    print(update.model_dump_json(exclude_none=True))
    return {"Hello": "World"}


# @app.get("/", status_code=status.HTTP_200_OK)
# async def read_root(request: Request):
#     print(request.json())
#     return {"Hello": "World"}


# @app.post("/roles", status_code=status.HTTP_201_CREATED)
# async def ensure_default_roles(session: SessionDep):
#     pass
