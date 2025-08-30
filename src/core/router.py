from __future__ import annotations
from typing import Any, Callable, Coroutine, TYPE_CHECKING
import inspect
from src.core.logger import logger
from src.core.enums import String

if TYPE_CHECKING:
    from src.core.conversation import Conversation


class Router:
    def __init__(self):
        self.routes: dict[str, Callable[..., Coroutine[Any, Any, list[Any]]]] = {}

    def route(self, path: str) -> Callable:
        def decorator(func: Callable[..., Coroutine[Any, Any, list[Any]]]) -> Callable:
            if path in self.routes:
                raise ValueError(f"Route '{path}' is already registered.")
            self.routes[path] = func
            return func

        return decorator

    async def process(self, command_string: str, conversation: Conversation) -> list:
        """
        Parses a command string, finds the best matching handler,
        and executes it.
        """
        command_parts = command_string.split(":")
        best_match_path = None
        best_match_len = 0
        for candidate_path in self.routes:
            candidate_path_parts = candidate_path.split(":")
            candidate_path_len = len(candidate_path_parts)
            if (
                len(command_parts) >= candidate_path_len
                and command_parts[:candidate_path_len] == candidate_path_parts
            ):
                if candidate_path_len > best_match_len:
                    best_match_path = candidate_path
                    best_match_len = candidate_path_len
        if best_match_path:
            handler = self.routes[best_match_path]
            raw_args = command_parts[best_match_len:]
            sig = inspect.signature(handler)
            num_expected_args = len(sig.parameters) - 1
            args = []
            if num_expected_args > 0 and len(raw_args) > num_expected_args:
                args.extend(raw_args[: num_expected_args - 1])
                last_arg = ":".join(raw_args[num_expected_args - 1 :])
                args.append(last_arg)
            else:
                args = raw_args
            logger.info(
                f"{conversation.log_prefix}Routing command '{command_string}' "
                f"to handler for '{best_match_path}' with args: {args}"
            )
            try:
                return await handler(conversation, *args)
            except Exception as e:
                log_message = f"Error executing handler for '{command_string}': {e}"
                # Provide a more specific log message for TypeErrors, which are common
                # when the number of arguments in the callback data doesn't match the handler.
                if isinstance(e, TypeError):
                    sig = inspect.signature(handler)
                    log_message = (
                        f"Handler for '{best_match_path}' called with incorrect arguments. "
                        f"Signature: {sig}. Got: {args}. Error: {e}"
                    )
                logger.error(
                    f"{conversation.log_prefix}{log_message}",
                    exc_info=True,
                )
                return [
                    conversation._drop_state_goto_mainmenu(
                        f"{String.INCONSISTENT_STATE_DETECTED}. "
                        f"{String.CONTACT_THE_ADMINISTRATOR}."
                    )
                ]
        logger.warning(
            f"{conversation.log_prefix}No route found for command '{command_string}'. "
            f"Available routes: {', '.join(f"'{key}'" for key in self.routes.keys())}"
        )
        return []


# Create a single instance to be used across the application
router = Router()
