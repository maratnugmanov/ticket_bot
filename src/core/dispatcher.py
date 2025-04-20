from __future__ import annotations
import asyncio
from typing import Any, Annotated
from fastapi import Depends
from sqlalchemy import select
from src.core.config import settings
from src.core.logger import logger
from src.db.engine import SessionDep
from src.db.models import UserDB
from src.core.models import Role, User
from src.tg.models import UpdateTG, UserTG


class Conversation:
    """Holds the state and lock for a single user's conversation.
    Prevents race conditions for concurrent updates for the same user.
    """

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.lock = asyncio.Lock()
        self.data: dict[str, Any] = {}
        self.current_step: str | None = None
        logger.debug(f"Conversation with '{user_id}' initialized.")

    async def update(self, step: str | None = None, **kwargs):
        """Safely updates the conversation state under lock."""
        async with self.lock:
            if step is not None:
                self.current_step = step
            self.data.update(kwargs)
            logger.debug(
                f"Conversation with '{self.user_id}' updated: Step={self.current_step}, Data added={list(kwargs.keys())}"
            )

    async def get_data(self) -> dict[str, Any]:
        """Safely retrieves a copy of the conversation data under lock."""
        async with self.lock:
            # Return a copy to prevent external modification without the lock
            return self.data.copy()

    async def get_step(self) -> str | None:
        """Safely retrieves the current conversation step under lock."""
        async with self.lock:
            return self.current_step

    async def get_full_state(self) -> dict[str, Any]:
        """Safely retrieves a copy of the full state (step + data) under lock."""
        async with self.lock:
            return {"current_step": self.current_step, "data": self.data.copy()}

    async def clear(self):
        async with self.lock:
            self.data.clear()
            self.current_step = None
            logger.debug(f"Conversation with '{self.user_id}' cleared.")


class Dispatcher:
    """Manages short-term conversation states for users in memory.
    Designed to be used as a singleton via FastAPI dependency injection.
    """

    def __init__(self):
        self.conversations: dict[int, Conversation] = {}
        self.is_hiring: bool = False
        self.users: dict[int, UserTG] = {}
        self.lock = asyncio.Lock()
        logger.info("Dispatcher initialized.")

    async def process(
        self, update: UpdateTG, session: SessionDep
    ) -> dict[str, Any] | None:
        user_info: tuple[int, str] | None = await self.extract_user_id_firstname(update)
        if not user_info:
            logger.debug(
                "Ignoring update: Could not extract telegram_uid and first_name from supported update types (private message/callback)."
            )
            return None
        telegram_uid, first_name = user_info
        user: UserDB | None = self.get_user(telegram_uid, first_name, session)
        if user is None:
            if self.is_hiring is True:
                logger.info(
                    f"User registration is enabled. Guest '{first_name}'[{telegram_uid}] is in queue."
                )
            else:
                logger.debug(
                    f"User registration is disabled. Guest '{first_name}'[{telegram_uid}] will be ignored."
                )
                return None
        conversation = await self.get_conversation(telegram_uid)
        reply_args = {
            # "url": settings.get_tg_endpoint("sendMessage"),
            # "json": answer.model_dump(exclude_none=True),
        }
        return reply_args

    def get_user(
        self, telegram_uid: int, first_name: str, session: SessionDep
    ) -> UserDB | None:
        logger.debug(f"Querying DB for user with telegram_uid: {telegram_uid}")
        user = session.scalar(select(UserDB).where(UserDB.telegram_uid == telegram_uid))
        if user:
            logger.debug(
                f"User found in DB: {user.last_name} {user.first_name} [{user.telegram_uid}]."
            )
        else:
            logger.debug(f"User '{first_name}'[{telegram_uid}] was not found in DB.")
        return user

    async def extract_user_id_firstname(
        self, update: UpdateTG
    ) -> tuple[int, str] | None:
        telegram_uid = None
        first_name = None
        if (
            update.message
            and update.message.from_
            and not update.message.from_.is_bot
            and update.message.chat.type == "private"
        ):
            telegram_uid = update.message.from_.id
            first_name = update.message.from_.first_name
            logger.debug(
                f"Processing private message update from '{first_name}'[{telegram_uid}]."
            )
        elif (
            update.callback_query
            and update.callback_query.from_
            and not update.callback_query.from_.is_bot
            and update.callback_query.message
            and update.callback_query.message.from_
            and update.callback_query.message.from_.is_bot
            and update.callback_query.message.from_.id == settings.bot_id
            and update.callback_query.message.chat.type == "private"
        ):
            telegram_uid = update.callback_query.from_.id
            first_name = update.callback_query.from_.first_name
            logger.debug(
                f"Processing callback query update from '{first_name}'[{telegram_uid}]."
            )
        if telegram_uid is not None and first_name is not None:
            return telegram_uid, first_name
        return None

    async def get_conversation(self, user_id: int) -> Conversation:
        """Safely gets or creates Conversation object for a user.
        Handles potential race conditions during creation."""
        conversation = self.conversations.get(user_id)
        if conversation:
            logger.info(f"Conversation with '{user_id}' found.")
            return conversation

        async with self.lock:
            # Double-check idiom: check again inside the lock
            conversation = self.conversations.get(user_id)
            if not conversation:
                logger.info(f"Conversation with '{user_id}' created.")
                conversation = Conversation(user_id)
                self.conversations[user_id] = conversation
            return conversation

    async def update_conversation(
        self, user_id: int, step: str | None = None, **kwargs
    ):
        """Updates conversation for a specific user."""
        conversation = await self.get_conversation(user_id)
        # Delegate the actual update to the user-specific state object (which has its own lock)
        await conversation.update(step=step, **kwargs)

    async def get_user_state(self, user_id: int) -> dict[str, Any] | None:
        """Gets the full current state (step + data) for a specific
        user. Returns None if no conversation exists for the user."""
        # Use the global lock only to safely check for the key's existence
        async with self.lock:
            user_state_obj = self.conversations.get(user_id)

        if user_state_obj:
            # Get the data using the user-specific state object's method (which uses its lock)
            state_data = await user_state_obj.get_full_state()
            logger.debug(f"Retrieved state for user {user_id}: {state_data}")
            return state_data
        else:
            logger.debug(f"No active conversation state found for user {user_id}")
            return None

    async def clear_conversation(self, user_id: int):
        """Removes Conversation with a specific user. Use it when the
        conversation is finished, cancelled, or timed out."""
        async with self.lock:
            if user_id in self.conversations:
                del self.conversations[user_id]
                logger.info(f"Conversation with '{user_id}' removed.")
            else:
                logger.debug(f"Conversation with '{user_id}' doesn't exist.")


dispatcher = Dispatcher()


def get_dispatcher() -> Dispatcher:
    """FastAPI dependency getter for the singleton Dispatcher instance."""
    return dispatcher


DispatcherDep = Annotated[Dispatcher, Depends(get_dispatcher)]
