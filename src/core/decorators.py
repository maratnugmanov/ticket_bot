from __future__ import annotations
from functools import wraps
from typing import Any, Callable, Coroutine, TYPE_CHECKING
from sqlalchemy import select
from sqlalchemy.orm import selectinload, Load
from src.core.logger import logger
from src.core.enums import ValidationMode, String, Action
from src.core.models import StateJS
from src.db.models import TicketDB, WriteoffDeviceDB

if TYPE_CHECKING:
    from src.core.conversation import Conversation


def require_ticket_context(
    *loader_options: Load,
    id_must_exist: bool = True,
    validate_device_index: ValidationMode | None = None,
):
    """
    A decorator for Conversation methods that require a valid ticket context.

    This decorator ensures that:
    1. The conversation state exists and contains a `ticket_id`.
    2. The ticket with that ID exists in the database.

    If these conditions are met, it fetches the TicketDB object (with optional
    preloaded relationships) and passes it as a `ticket` keyword argument
    to the decorated handler.

    If conditions are not met, it logs an error and returns a message to
    drop the state and return to the main menu.

    Args:
        id_must_exist: If True, ensures state.ticket_id exists and passes the
                       TicketDB object to the handler. If False, ensures
                       state.ticket_id does NOT exist.
        *loader_options: A list of SQLAlchemy loader options
                         (e.g., selectinload(TicketDB.contract)).
        validate_device_index:
            If ValidationMode.OPTIONAL_NEW, validates
            `ticket_device_index` if it exists,
            allowing for a new device index.
            If ValidationMode.REQUIRED_EXISTING,
            ensures `ticket_device_index` exists
            and is a valid index for an existing device.
    """

    def decorator(
        handler_func: Callable[..., Coroutine[Any, Any, list[Any]]],
    ) -> Callable[..., Coroutine[Any, Any, list[Any]]]:
        @wraps(handler_func)
        async def wrapper(self: Conversation, *args, **kwargs):
            assert self.state is not None
            if id_must_exist:
                if not self.state.ticket_id:  # Both None and 0 are covered this way.
                    logger.error(
                        f"{self.log_prefix}ticket_id is missing "
                        " for a ticket-context action."
                    )
                    return [
                        self._drop_state_goto_main_menu(
                            f"{String.INCONSISTENT_STATE_DETECTED} "
                            "(missing ticket_id). "
                            f"{String.PICK_A_FUNCTION}."
                        )
                    ]
                query = select(TicketDB).where(TicketDB.id == self.state.ticket_id)
                if loader_options:
                    query = query.options(*loader_options)
                ticket = await self.session.scalar(query)
                if not ticket:
                    logger.warning(
                        f"{self.log_prefix}Ticket with id={self.state.ticket_id} not found."
                    )
                    return [
                        self._drop_state_goto_main_menu(
                            f"{String.TICKET_NOT_FOUND}. {String.PICK_A_FUNCTION}."
                        )
                    ]
                if validate_device_index:
                    # This check requires devices to be loaded. We assume they are.
                    device_index = self.state.ticket_device_index
                    total_devices = len(ticket.devices)
                    if validate_device_index == ValidationMode.OPTIONAL_NEW:
                        if device_index is not None and not (
                            0 <= device_index <= total_devices
                        ):
                            logger.error(
                                f"{self.log_prefix}Error: "
                                f"device_index={device_index} and "
                                f"total_devices={total_devices}. "
                                "Expected: "
                                f"0 <= device_index <= total_devices."
                            )
                            return [
                                self._drop_state_goto_main_menu(
                                    f"{String.INCONSISTENT_STATE_DETECTED} "
                                    "(incorrect ticket_device_index). "
                                    f"{String.PICK_A_FUNCTION}."
                                )
                            ]
                    elif validate_device_index == ValidationMode.REQUIRED_EXISTING:
                        if device_index is None:
                            logger.error(
                                f"{self.log_prefix}device_index is "
                                "missing for a device-context action."
                            )
                            return [
                                self._drop_state_goto_main_menu(
                                    f"{String.INCONSISTENT_STATE_DETECTED} "
                                    "(missing ticket_device_index). "
                                    f"{String.PICK_A_FUNCTION}."
                                )
                            ]
                        if not (0 <= device_index < total_devices):
                            logger.error(
                                f"{self.log_prefix}Error: "
                                f"device_index={device_index} and "
                                f"total_devices={total_devices}. "
                                f"Expected: 0 <= device_index < total_devices."
                            )
                            return [
                                self._drop_state_goto_main_menu(
                                    f"{String.INCONSISTENT_STATE_DETECTED} "
                                    "(incorrect ticket_device_index). "
                                    f"{String.PICK_A_FUNCTION}."
                                )
                            ]
                # Pass the fetched ticket to the handler
                return await handler_func(self, ticket=ticket, *args, **kwargs)
            else:  # id_must_exist is False
                if self.state.ticket_id:
                    logger.error(
                        f"{self.log_prefix}{self.user_db.full_name} "
                        "is already working on a ticket "
                        f"under id={self.state.ticket_id}. "
                        "Cannot create a new ticket."
                    )
                    return [
                        self._drop_state_goto_main_menu(
                            f"{String.INCONSISTENT_STATE_DETECTED} "
                            "(ticket_id should not exist). "
                            f"{String.PICK_A_FUNCTION}."
                        )
                    ]
                return await handler_func(self, *args, **kwargs)

        return wrapper

    return decorator


def require_writeoff_context(*loader_options: Load, id_must_exist: bool = True):
    """
    A decorator for Conversation methods that require a valid writeoff_device context.
    """

    def decorator(
        handler_func: Callable[..., Coroutine[Any, Any, list[Any]]],
    ) -> Callable[..., Coroutine[Any, Any, list[Any]]]:
        @wraps(handler_func)
        async def wrapper(self: Conversation, *args, **kwargs):
            assert self.state is not None
            if id_must_exist:
                if not self.state.writeoff_device_id:  # Both None and 0 are covered
                    logger.error(
                        f"{self.log_prefix}writeoff_device_id "
                        "is missing for a writeoff-context action."
                    )
                    self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
                    return [
                        await self._build_writeoff_devices_list(
                            f"{String.INCONSISTENT_STATE_DETECTED} "
                            "(missing writeoff_device_id). "
                            f"{String.AVAILABLE_WRITEOFF_DEVICES_ACTIONS}."
                        )
                    ]
                query = select(WriteoffDeviceDB).where(
                    WriteoffDeviceDB.id == self.state.writeoff_device_id
                )
                if loader_options:
                    query = query.options(*loader_options)
                writeoff_device = await self.session.scalar(query)
                if not writeoff_device:
                    logger.warning(
                        f"{self.log_prefix}Writeoff device with "
                        f"id={self.state.writeoff_device_id} not found."
                    )
                    logger.info(
                        f"{self.log_prefix}Going back to writeoff devices menu."
                    )
                    self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
                    return [
                        await self._build_writeoff_devices_list(
                            f"{String.WRITEOFF_DEVICE_NOT_FOUND}. "
                            f"{String.AVAILABLE_WRITEOFF_DEVICES_ACTIONS}."
                        )
                    ]
                return await handler_func(
                    self, writeoff_device=writeoff_device, *args, **kwargs
                )
            else:  # id_must_exist is False
                if self.state.writeoff_device_id:
                    logger.error(
                        f"{self.log_prefix}{self.user_db.full_name} "
                        "is already working on a write-off device "
                        f"under id={self.state.writeoff_device_id}. "
                        "Cannot create a new one."
                    )
                    self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
                    return [
                        await self._build_writeoff_devices_list(
                            f"{String.INCONSISTENT_STATE_DETECTED} "
                            "(writeoff_device_id should not exist). "
                            f"{String.AVAILABLE_WRITEOFF_DEVICES_ACTIONS}."
                        )
                    ]
                return await handler_func(self, *args, **kwargs)

        return wrapper

    return decorator
