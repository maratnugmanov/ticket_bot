from __future__ import annotations
import asyncio
from typing import Any, Annotated
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from src.core.config import settings
from src.core.logger import logger
from src.core.enums import RoleName
from src.db.engine import SessionDepDB, SessionDepCC
from src.db.models import RoleDB, UserDB, RoleCC, UserCC, UserRoleLinkCC
from src.core.models import Role, User
from src.tg.models import UpdateTG, UserTG


class CacheInconsistencyError(RuntimeError):
    """Raised when cache timestamp is newer than DB timestamp."""

    pass


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
        self, update: UpdateTG, session_db: SessionDepDB, session_cc: SessionDepCC
    ) -> dict[str, Any] | None:
        user_tg: UserTG | None = await self.get_user_tg(update)
        if not user_tg:
            logger.debug(
                "Ignoring update: Could not extract User from "
                "supported update types (private message/callback)."
            )
            return None
        existing_user: UserCC | None = self.get_existing_user(
            user_tg, session_db, session_cc
        )
        if existing_user is None:
            logger.debug(f"Guest {user_tg.full_name} is not registered.")
            if not self.is_hiring:
                logger.debug(
                    "User registration is disabled. "
                    f"Guest {user_tg.full_name} will be ignored."
                )
                return None
            logger.debug(
                f"User registration is enabled. "
                f"Guest {user_tg.full_name} is being created."
            )
            user_db = await self.add_user_db(user_tg, session_db)
            if user_db is None:
                logger.warning(
                    f"Failed to insert new user {user_tg.full_name}. "
                    "Aborting processing."
                )
                return None
            # I'm here
            async with self.users_lock:
                self.users[user_db.telegram_uid] = user_db
            return None
        if existing_user.is_disabled:
            logger.debug(
                f"User {existing_user.full_name} is disabled and will be ignored."
            )
            return None
        return None
        # conversation = await self.get_conversation(telegram_uid)
        # reply_args = {
        #     # "url": settings.get_tg_endpoint("sendMessage"),
        #     # "json": answer.model_dump(exclude_none=True),
        # }
        # return reply_args

    async def get_existing_user(
        self, user_tg: UserTG, session_db: SessionDepDB, session_cc: SessionDepCC
    ) -> UserCC | None:
        """Returns up-to-date Cached User (UserCC) by either validating
        existing Cached User or by caching an existing User from DB
        (UserDB). Deletes Cached User and returns None if User doesn't
        exist in DB. Raises an error and removes Cached User if User in
        DB (UserDB) is older than its cached counterpart - which
        shouldn't be possible."""
        logger.debug(f"Querying cache for Telegram User {user_tg.full_name}.")
        user_cc = await self.get_user_cc(user_tg, session_cc)
        if not user_cc:
            logger.debug(f"Telegram User {user_tg.full_name} was not found in cache.")
            logger.debug(f"Querying DB for Telegram User {user_tg.full_name}.")
            user_db = await self.get_user_db(user_tg, session_db)
            if not user_db:
                logger.debug(f"Telegram User {user_tg.full_name} was not found in DB.")
                logger.debug("Returning None.")
                return None
            logger.debug(
                f"Telegram User {user_tg.full_name} was found in DB "
                f"as {user_db.full_name}."
            )
            logger.debug(f"Caching User {user_db.full_name}.")
            user_cc = await self.add_user_cc(user_db, session_db, session_cc)
            logger.debug(f"Returning Cached User {user_cc.full_name}.")
            return user_cc
        logger.debug(
            f"Telegram User {user_tg.full_name} was found in cache "
            f"as {user_cc.full_name}."
        )
        logger.debug(f"Validating Cached User {user_cc.full_name}")
        user_db_updated_at = await session_db.scalar(
            select(UserDB.updated_at).where(UserDB.telegram_uid == user_cc.telegram_uid)
        )
        if not user_db_updated_at:
            logger.debug(f"Cached User {user_cc.full_name} was not found in DB.")
            logger.debug(f"Deleting Cached User {user_cc.full_name} as orphan.")
            await self.del_user_cc(user_cc, session_cc)
            logger.debug("Returning None.")
            return None
        if user_cc.updated_at == user_db_updated_at:
            logger.debug(
                f"Cached User {user_cc.full_name} is valid. Returning Cached User."
            )
            return user_cc
        if user_cc.updated_at < user_db_updated_at:
            logger.debug(f"Cached User {user_cc.full_name} is outdated.")
            logger.debug(f"Deleting Cached User {user_cc.full_name} as outdated.")
            await self.del_user_cc(user_cc, session_cc)
            logger.debug(f"Querying DB for Telegram User {user_tg.full_name}.")
            user_db = await self.get_user_db(user_tg, session_db)
            if not user_db:
                logger.debug(f"Telegram User {user_tg.full_name} was not found in DB.")
                logger.debug("Returning None.")
                return None
            logger.debug(
                f"Telegram User {user_tg.full_name} was found in DB "
                f"as {user_db.full_name}."
            )
            logger.debug(f"Caching User {user_db.full_name}.")
            user_cc = await self.add_user_cc(user_db, session_db, session_cc)
            return user_cc
        if user_cc.updated_at > user_db_updated_at:
            error_message = (
                f"Inconsistent state detected for user {user_cc.full_name}: "
                f"Cached timestamp ({user_cc.updated_at}) is newer than "
                f"database timestamp ({user_db_updated_at}). Potential cache issue."
            )
            logger.debug(f"Deleting Inconsistent Cached User {user_cc.full_name}.")
            await session_cc.delete(user_cc)
            await session_cc.flush()
            logger.error(error_message)
            raise CacheInconsistencyError(error_message)

    async def del_user_cc(self, user_cc: UserCC, session_cc: SessionDepCC) -> None:
        await session_cc.delete(user_cc)
        await session_cc.flush()

    async def get_user_cc(
        self, user_tg: UserTG, session_cc: SessionDepCC
    ) -> UserCC | None:
        """Returns Cached User (UserCC) with its roles if the User
        exists. Returns None if not."""
        logger.debug(f"Querying cache for user {user_tg.full_name} by telegram_uid.")
        user_cc: UserCC | None = await session_cc.scalar(
            select(UserCC)
            .where(UserCC.telegram_uid == user_tg.id)
            .options(selectinload(UserCC.roles))
        )
        if user_cc:
            logger.debug(f"User found in cache: {user_cc.full_name}. Returning UserCC.")
        else:
            logger.debug(
                f"User {user_tg.full_name} was not found in cache by telegram_uid."
            )
        return user_cc

    async def get_user_db(
        self, user_tg: UserTG, session: SessionDepDB
    ) -> UserDB | None:
        """Returns User in DB (UserDB) with its roles if the User
        exists. Returns None if not."""
        logger.debug(f"Querying DB for user {user_tg.full_name} by telegram_uid.")
        user_db: UserDB | None = await session.scalar(
            select(UserDB)
            .where(UserDB.telegram_uid == user_tg.id)
            .options(selectinload(UserDB.roles))
        )
        if user_db:
            logger.debug(f"User found in DB: {user_db.full_name}. Returning UserDB.")
        else:
            logger.debug(
                f"User {user_tg.full_name} was not found in DB by telegram_uid."
            )
        return user_db

    async def add_user_cc(
        self, user_db: UserDB, session_db: SessionDepDB, session_cc: SessionDepCC
    ) -> UserCC:
        """Returns Cached User (UserCC) with its roles by creating it
        from existing User in DB (UserDB)."""
        user_cc = UserCC(
            id=user_db.id,
            telegram_uid=user_db.telegram_uid,
            first_name=user_db.first_name,
            last_name=user_db.last_name,
            timezone=user_db.timezone,
            is_hiring=user_db.is_hiring,
            is_disabled=user_db.is_disabled,
            created_at=user_db.created_at,
            updated_at=user_db.updated_at,
        )
        session_cc.add(user_cc)
        role_db_ids = {role.id for role in user_db.roles}
        for role_id in role_db_ids:
            link = UserRoleLinkCC(user_id=user_cc.id, role_id=role_id)
            session_cc.add(link)
        await session_cc.flush()
        await session_cc.refresh(user_cc, attribute_names=["roles"])
        logger.debug(f"User {user_cc.full_name} including roles was added to cache DB.")
        return user_cc

    async def add_user_db(
        self, user_tg: UserTG, session_db: SessionDepDB
    ) -> UserDB | None:
        """Returns User in DB (UserDB) with Guest role by creating it
        from Telegram User (UserTG). Returns None if Guest role was not
        found in Roles DB table."""
        guest_role = await session_db.scalar(
            select(RoleDB).where(RoleDB.name == RoleName.GUEST)
        )
        if guest_role is None:
            logger.error(
                f"CRITICAL: Default role '{RoleName.GUEST}' not "
                "found in the DB. Cannot create new User DB."
            )
            return None
        user_db = UserDB(
            telegram_uid=user_tg.id,
            first_name=user_tg.first_name,
            last_name=user_tg.last_name,
        )
        user_db.roles.append(guest_role)
        session_db.add(user_db)
        await session_db.flush()
        await session_db.refresh(user_db, attribute_names=["roles"])
        logger.debug(
            f"User DB {user_db.full_name} (ID: {user_db.id}) was "
            f"created with role '{RoleName.GUEST.name}' in the DB."
        )
        return user_db

    def get_user_tg(self, update: UpdateTG) -> UserTG | None:
        """Returns Telegram User (UserTG) by extracting it from relevant
        Telegram Update object. Returns None otherwise."""
        user_tg = None
        if (
            update.message
            and update.message.from_
            and not update.message.from_.is_bot
            and update.message.chat.type == "private"
        ):
            user_tg = update.message.from_
            logger.debug(f"Processing private message update from {user_tg.full_name}.")
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
            user_tg = update.callback_query.from_
            logger.debug(f"Processing callback query update from {user_tg.full_name}.")
        return user_tg

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

    async def clear_conversation(self, user_tg: UserTG):
        """Removes Conversation with a specific user. Use it when the
        conversation is finished, cancelled, or timed out."""
        async with self.conversations_lock:
            if user_tg.id in self.conversations:
                del self.conversations[user_tg.id]
                logger.info(f"Conversation with {user_tg.full_name} removed.")
            else:
                logger.debug(f"Conversation with {user_tg.full_name} doesn't exist.")


dispatcher = Dispatcher()


def get_dispatcher() -> Dispatcher:
    """FastAPI dependency getter for the singleton Dispatcher instance."""
    return dispatcher


DispatcherDep = Annotated[Dispatcher, Depends(get_dispatcher)]
