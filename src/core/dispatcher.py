from __future__ import annotations
import asyncio
from typing import Any, Annotated
from fastapi import Depends
from sqlalchemy import select, exists
from sqlalchemy.orm import selectinload
from src.core.config import settings
from src.core.logger import logger
from src.core.enums import RoleName
from src.db.engine import SessionDepDB
from src.db.models import RoleDB, UserDB
from src.core.models import Role, User
from src.tg.models import UpdateTG, UserTG, SendMessageTG


class Conversation:
    """Receives Telegram Update (UpdateTG), database session
    (SessionDepDB), and User from the database (UserDB). Processes
    User's Request and Returns a Response."""

    def __init__(self, update_tg: UpdateTG, session_db: SessionDepDB, user_db: UserDB):
        self.update_tg: UpdateTG = update_tg
        self.session_db: SessionDepDB = session_db
        self.user_db: UserDB = user_db
        logger.debug(
            f"Conversation with {self.user_db.full_name}, "
            f"Update #{self.update_tg.update_id} initialized."
        )

    async def process(self) -> dict:
        if self.update_tg.message:
            send_message_tg = SendMessageTG(
                chat_id=self.update_tg.message.chat.id, text=self.update_tg.message.text
            )
            return {
                "url": settings.get_tg_endpoint("sendMessage"),
                "json": send_message_tg.model_dump(exclude_none=True),
            }


class Dispatcher:
    """Extracts Telegram User (UserTG) from the Telegram Update
    (UpdateTG) and passes the corresponding User from the database
    (UserDB) to the Conversation (Conversation) along with the database
    session (SessionDepDB). Returns Conversation Result or None if the User
    is not an employee."""

    def __init__(self, update_tg: UpdateTG, session_db: SessionDepDB):
        self.update_tg: UpdateTG = update_tg
        self.session_db: SessionDepDB = session_db
        logger.debug(f"Dispatcher for Update #{self.update_tg.update_id} initialized.")

    async def process(self) -> dict | None:
        user_tg: UserTG | None = self.get_user_tg()
        if not user_tg:
            logger.debug(
                "Ignoring update: Could not extract User from "
                "supported update types (private message/callback)."
            )
            return None
        user_db: UserDB | None = await self.session_db.scalar(
            select(UserDB)
            .where(UserDB.telegram_uid == user_tg.id)
            .options(selectinload(UserDB.roles))
        )
        if user_db is None:
            logger.debug(f"Guest {user_tg.full_name} is not registered.")
            hiring = await self.session_db.scalar(
                select(exists().where(UserDB.is_hiring is True))
            )
            if not hiring:
                logger.debug(
                    "User registration is disabled. Telegram User "
                    f"{user_tg.full_name} will be ignored. "
                    "Returning None."
                )
                return None
            logger.debug(
                "User registration is enabled. Telegram User "
                f"{user_tg.full_name} will be added to the database "
                f"with the default '{RoleName.GUEST}' role."
            )
            guest_role = await self.session_db.scalar(
                select(RoleDB).where(RoleDB.name == RoleName.GUEST)
            )
            if guest_role is None:
                error_message = logger.error(
                    f"CRITICAL: Default role '{RoleName.GUEST}' not "
                    "found in the DB. Cannot create new User DB."
                )
                logger.error(error_message)
                raise ValueError(error_message)
            user_db = UserDB(
                telegram_uid=user_tg.id,
                first_name=user_tg.first_name,
                last_name=user_tg.last_name,
            )
            user_db.roles.append(guest_role)
            self.session_db.add(user_db)
            await self.session_db.flush()
            logger.debug(
                f"User DB {user_db.full_name} (ID: {user_db.id}) was "
                f"created with role '{RoleName.GUEST.name}' in the DB."
            )
            return None
        if len(user_db.roles) == 1 and user_db.roles[0].name == guest_role:
            logger.error(
                f"User DB {user_db.full_name} has only "
                f"'{RoleName.GUEST}' role and won't get any reply."
            )
            return None
        logger.debug(f"Validated User DB {user_db.full_name} as employee.")
        bot_response = await Conversation(
            self.update_tg, self.session_db, user_db
        ).process()
        return bot_response

    def get_user_tg(self) -> UserTG | None:
        """Returns Telegram User (UserTG) by extracting it from relevant
        Telegram Update object. Returns None otherwise."""
        user_tg = None
        if (
            self.update_tg.message
            and self.update_tg.message.from_
            and not self.update_tg.message.from_.is_bot
            and self.update_tg.message.chat.type == "private"
        ):
            user_tg = self.update_tg.message.from_
            logger.debug(f"Processing private message update from {user_tg.full_name}.")
        elif (
            self.update_tg.callback_query
            and self.update_tg.callback_query.from_
            and not self.update_tg.callback_query.from_.is_bot
            and self.update_tg.callback_query.message
            and self.update_tg.callback_query.message.from_
            and self.update_tg.callback_query.message.from_.is_bot
            and self.update_tg.callback_query.message.from_.id == settings.bot_id
            and self.update_tg.callback_query.message.chat.type == "private"
        ):
            user_tg = self.update_tg.callback_query.from_
            logger.debug(f"Processing callback query update from {user_tg.full_name}.")
        return user_tg
