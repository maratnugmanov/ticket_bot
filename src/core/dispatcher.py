from __future__ import annotations
import functools
import asyncio
from typing import Any, Annotated, TypedDict, Callable, Coroutine
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from src.core.config import settings
from src.core.logger import logger
from src.db.engine import SessionDep
from src.db.models import UserDB
from src.core.models import Role, User
from src.tg.models import UpdateTG, UserTG


# Decided not to use it for now.
#
# def with_lock(lock_attr_name: str) -> Callable:
#     """Decorator factory to acquire an asyncio.Lock on the instance
#     before calling an async method. Assumes the lock is an attribute
#     of the instance (self) named `lock_attr_name`."""

#     def decorator(
#         func: Callable[..., Coroutine[Any, Any, Any]],
#     ) -> Callable[..., Coroutine[Any, Any, Any]]:
#         @functools.wraps(func)
#         async def wrapper(self, *args, **kwargs) -> Any:
#             lock: asyncio.Lock = getattr(self, lock_attr_name)
#             async with lock:
#                 return await func(self, *args, **kwargs)

#         return wrapper

#     return decorator


class UserUpd(TypedDict):
    telegram_uid: int
    first_name: str
    last_name: str | None


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
    """Manages short-term conversation states for users in cache.
    Designed to be used as a singleton via FastAPI dependency injection.
    """

    def __init__(self):
        self.conversations: dict[int, Conversation] = {}
        self.is_hiring: bool = False
        self.users: dict[int, UserDB] = {}
        self.users_lock = asyncio.Lock()
        self.conversations_lock = asyncio.Lock()

        logger.info("Dispatcher initialized.")

    async def process(
        self, update: UpdateTG, session: SessionDep
    ) -> dict[str, Any] | None:
        user_upd: UserUpd | None = await self.extract_user_data(update)
        if not user_upd:
            logger.debug(
                "Ignoring update: Could not extract telegram_uid and first_name from supported update types (private message/callback)."
            )
            return None
        full_name = f"{user_upd.first_name}{' ' + user_upd.last_name if user_upd.last_name else ''}"
        relevant_user: UserDB | None = self.get_relevant_user(user_upd, session)
        if relevant_user is None:
            logger.debug("User is not registered.")
            if self.is_hiring is True:
                logger.debug(
                    f"User registration is enabled. Guest '{full_name}' [{user_upd.telegram_uid}] is in queue."
                )
                relevant_user = UserDB(
                    telegram_uid=user_upd.telegram_uid,
                    first_name=user_upd.first_name,
                    last_name=user_upd.last_name,
                )
            else:
                logger.debug(
                    f"User registration is disabled. Guest '{full_name}' [{user_upd.telegram_uid}] will be ignored."
                )
                return None

        elif relevant_user:
            if relevant_user.is_disabled is True:
                logger.debug(
                    f"User '{full_name}' [{relevant_user.telegram_uid}] is disabled and will be ignored."
                )
        return None
        conversation = await self.get_conversation(telegram_uid)
        reply_args = {
            # "url": settings.get_tg_endpoint("sendMessage"),
            # "json": answer.model_dump(exclude_none=True),
        }
        return reply_args

    async def get_relevant_user(
        self,
        user_upd: UserUpd,
        session: SessionDep,
    ) -> UserDB | None:
        full_name = f"{user_upd.first_name}{' ' + user_upd.last_name if user_upd.last_name else ''}"
        async with self.users_lock:
            user_cache = self.users.get(user_upd.telegram_uid)
        if not user_cache:
            logger.debug(
                f"User '{full_name}' [{user_upd.telegram_uid}] was not found in cache."
            )
            return await self.get_user_db(user_upd, session)
        logger.debug(
            f"User '{user_cache.first_name} {user_cache.last_name}' [{user_upd.telegram_uid}] was found in cache."
        )
        user_db_updated_at = await session.scalar(
            select(UserDB.updated_at).where(
                UserDB.telegram_uid == user_upd.telegram_uid
            )
        )
        if user_db_updated_at is None:
            logger.debug(
                f"Cached User '{user_cache.first_name} {user_cache.last_name}' [{user_upd.telegram_uid}] not found in DB. Removing from cache."
            )
            async with self.users_lock:
                if user_upd.telegram_uid in self.users:
                    del self.users[user_upd.telegram_uid]
            return None
        elif user_cache.updated_at == user_db_updated_at:
            logger.debug(
                f"Cached User '{user_cache.first_name} {user_cache.last_name}' [{user_upd.telegram_uid}] is relevant."
            )
            return user_cache
        elif user_cache.updated_at < user_db_updated_at:
            logger.debug(
                f"Cached User '{user_cache.first_name} {user_cache.last_name}' [{user_upd.telegram_uid}] is obsolete."
            )
            return await self.get_user_db(user_upd, session)
        elif user_cache.updated_at > user_db_updated_at:
            error_message = (
                f"Inconsistent state detected for user {user_upd.telegram_uid}: "
                f"Cached timestamp ({user_cache.updated_at}) is newer than "
                f"database timestamp ({user_db_updated_at}). Potential cache issue."
            )
            logger.error(error_message)
            raise RuntimeError(error_message)

    async def get_user_db(
        self, user_upd: UserUpd, session: SessionDep
    ) -> UserDB | None:
        full_name = f"{user_upd.first_name}{' ' + user_upd.last_name if user_upd.last_name else ''}"
        logger.debug(
            f"Querying DB for user '{full_name}' [{user_upd.telegram_uid}] by telegram_uid."
        )
        user_db = await session.scalar(
            select(UserDB)
            .where(UserDB.telegram_uid == user_upd.telegram_uid)
            .options(selectinload(UserDB.roles))
        )
        async with self.users_lock:
            if user_db:
                logger.debug(
                    f"User found in DB: '{user_db.first_name} {user_db.last_name}' [{user_db.telegram_uid}]. Updating User in Cache."
                )
                self.users[user_upd.telegram_uid] = user_db
            else:
                logger.debug(
                    f"User '{full_name}' [{user_upd.telegram_uid}] was not found in DB by telegram_uid."
                )
                if user_upd.telegram_uid in self.users:
                    logger.debug(
                        f"User '{full_name}' [{user_upd.telegram_uid}] was not found in DB. Removing User from cache."
                    )
                    del self.users[user_upd.telegram_uid]
                    self.clear_conversation(user_upd)
            return user_db

    async def extract_user_data(self, update: UpdateTG) -> UserUpd | None:
        user_data = UserUpd(telegram_uid=None, first_name=None, last_name=None)
        full_name = None
        if (
            update.message
            and update.message.from_
            and not update.message.from_.is_bot
            and update.message.chat.type == "private"
        ):
            user_data.telegram_uid = update.message.from_.id
            user_data.first_name = update.message.from_.first_name
            if update.message.from_.last_name:
                user_data.last_name = update.message.from_.last_name
            full_name = f"{user_data.first_name}{' ' + user_data.last_name if user_data.last_name else ''}"
            logger.debug(
                f"Processing private message update from '{full_name}' [{user_data.telegram_uid}]."
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
            user_data.telegram_uid = update.callback_query.from_.id
            user_data.first_name = update.callback_query.from_.first_name
            if update.callback_query.from_.last_name:
                user_data.last_name = update.callback_query.from_.last_name
            full_name = f"{user_data.first_name}{' ' + user_data.last_name if user_data.last_name else ''}"
            logger.debug(
                f"Processing callback query update from '{full_name}' [{user_data.telegram_uid}]."
            )
        if user_data.telegram_uid is not None and user_data.first_name is not None:
            return user_data
        return None

    async def get_conversation(self, user_id: int) -> Conversation:
        """Safely gets or creates Conversation object for a user.
        Handles potential race conditions during creation."""
        conversation = self.conversations.get(user_id)
        if conversation:
            logger.info(f"Conversation with '{user_id}' found.")
            return conversation

        async with self.conversations_lock:
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
        async with self.conversations_lock:
            user_state_obj = self.conversations.get(user_id)

        if user_state_obj:
            # Get the data using the user-specific state object's method (which uses its lock)
            state_data = await user_state_obj.get_full_state()
            logger.debug(f"Retrieved state for user {user_id}: {state_data}")
            return state_data
        else:
            logger.debug(f"No active conversation state found for user {user_id}")
            return None

    async def clear_conversation(self, user_upd: UserUpd):
        """Removes Conversation with a specific user. Use it when the
        conversation is finished, cancelled, or timed out."""
        full_name = f"{user_upd.first_name}{' ' + user_upd.last_name if user_upd.last_name else ''}"
        async with self.conversations_lock:
            if user_upd.telegram_uid in self.conversations:
                del self.conversations[user_upd.telegram_uid]
                logger.info(
                    f"Conversation with '{full_name}' [{user_data.telegram_uid}] removed."
                )
            else:
                logger.debug(
                    f"Conversation with '{full_name}' [{user_data.telegram_uid}] doesn't exist."
                )


dispatcher = Dispatcher()


def get_dispatcher() -> Dispatcher:
    """FastAPI dependency getter for the singleton Dispatcher instance."""
    return dispatcher


DispatcherDep = Annotated[Dispatcher, Depends(get_dispatcher)]
