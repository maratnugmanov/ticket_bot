# Built-ins


# Core dependencies
from fastapi import FastAPI, Request, Depends, status, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
import httpx
import asyncio

# Local code
from src.core.config import settings
from src.core.logger import logger
from src.core.lifespan import lifespan
from src.tg.models import MessageTG, UpdateTG, SendMessageTG, ResponseTG

from src.core.enums import RoleName, DeviceTypeName
from src.db.models import (
    UserRoleLinkDB,
    RoleDB,
    UserDB,
    TicketDB,
    ReportDB,
    WriteoffDB,
    DeviceDB,
    DeviceTypeDB,
)
from src.api import webhook

# uvicorn src.main:app --reload
app = FastAPI(lifespan=lifespan)
app.include_router(webhook.router, prefix="", tags=["Telegram Webhook"])


@app.get("/", status_code=status.HTTP_200_OK, tags=["Health Check"])
async def health_check():
    logger.debug("GET '/' triggered")
    return {"status": "ok"}


# @app.get("/", status_code=status.HTTP_200_OK)
# async def read_root(request: Request):
#     print(request.json())
#     return {"Hello": "World"}


# @app.post("/roles", status_code=status.HTTP_201_CREATED)
# async def ensure_default_roles(session: SessionDep):
#     pass
