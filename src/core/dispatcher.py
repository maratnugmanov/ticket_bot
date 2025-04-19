from __future__ import annotations
import asyncio
import logging
from typing import Any, Annotated
from fastapi import Depends
from src.core.models import Role, User


logger = logging.getLogger(__name__)


class ConversationState:
    """
    Holds the state and lock for a single user's conversation.
    Prevents race conditions for concurrent updates for the same user.
    """

    def __init__(self, user_id: Any):
        self.user_id = user_id
        self.lock = asyncio.Lock()
        self.data: dict[str, Any] = {}
        self.current_step: str | None = None
        logger.debug(f"Initialized conversation state for user: {user_id}")

    async def update(self, step: str | None = None, **kwargs):
        """Safely updates the conversation state under lock."""
        async with self.lock:
            if step is not None:
                self.current_step = step
            self.data.update(kwargs)
            logger.debug(
                f"State updated for user {self.user_id}: Step={self.current_step}, Data added={list(kwargs.keys())}"
            )  # Log keys only for brevity

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
            logger.debug(f"Cleared conversation state for user: {self.user_id}")


class Dispatcher:
    """
    Manages short-term conversation states for users in memory.
    Designed to be used as a singleton via FastAPI dependency injection.
    (Does NOT inherit from Pydantic BaseModel)
    """

    def __init__(self):
        # Key: user_id (e.g., telegram_uid), Value: ConversationState instance
        self._conversations: dict[Any, ConversationState] = {}
        # Lock for modifying the _conversations dictionary itself (adding/removing users)
        self._global_lock = asyncio.Lock()
        logger.info("Dispatcher service initialized.")

    async def _get_or_create_user_state(self, user_id: Any) -> ConversationState:
        """
        Safely gets or creates the state object for a user.
        Handles potential race conditions during creation.
        """
        # Quick check without lock first for performance
        user_state = self._conversations.get(user_id)
        if user_state:
            return user_state

        # If not found, acquire global lock to check again and potentially create
        async with self._global_lock:
            # Double-check idiom: check again inside the lock
            user_state = self._conversations.get(user_id)
            if not user_state:
                logger.info(f"Creating new conversation state for user_id: {user_id}")
                user_state = ConversationState(user_id)
                self._conversations[user_id] = user_state
            return user_state

    async def update_user_state(self, user_id: Any, step: str | None = None, **kwargs):
        """Updates the state for a specific user."""
        user_state = await self._get_or_create_user_state(user_id)
        # Delegate the actual update to the user-specific state object (which has its own lock)
        await user_state.update(step=step, **kwargs)

    async def get_user_state(self, user_id: Any) -> dict[str, Any] | None:
        """
        Gets the full current state (step + data) for a specific user.
        Returns None if no conversation exists for the user.
        """
        # Use the global lock only to safely check for the key's existence
        async with self._global_lock:
            user_state_obj = self._conversations.get(user_id)

        if user_state_obj:
            # Get the data using the user-specific state object's method (which uses its lock)
            state_data = await user_state_obj.get_full_state()
            logger.debug(f"Retrieved state for user {user_id}: {state_data}")
            return state_data
        else:
            logger.debug(f"No active conversation state found for user {user_id}")
            return None

    async def clear_user_state(self, user_id: Any):
        """
        Removes the conversation state for a specific user entirely.
        Call this when the conversation is finished, cancelled, or timed out.
        """
        async with self._global_lock:
            if user_id in self._conversations:
                # Potentially call clear on the state object if it needs cleanup
                # await self._conversations[user_id].clear() # Optional cleanup within state
                del self._conversations[user_id]
                logger.info(f"Removed conversation state for user_id: {user_id}")
            else:
                logger.debug(f"No state to clear for user_id: {user_id}")


dispatcher = Dispatcher()


def get_dispatcher() -> Dispatcher:
    """FastAPI dependency getter for the singleton Dispatcher instance."""
    return dispatcher


DispatcherDep = Annotated[Dispatcher, Depends(get_dispatcher)]
