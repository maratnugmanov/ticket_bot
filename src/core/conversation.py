from __future__ import annotations
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any, Callable, Coroutine
import inspect
import re
import httpx
from pydantic import ValidationError
from sqlalchemy import select, exists, func
from sqlalchemy.orm import selectinload
from src.core.config import settings
from src.core.logger import logger
from src.core.enums import (
    RoleName,
    DeviceTypeName,
    CallbackData,
    String,
    Action,
    Script,
)
from src.core.models import DeviceJS, DeviceTypeJS, StateJS
from src.tg.models import (
    UpdateTG,
    MessageUpdateTG,
    CallbackQueryUpdateTG,
    MessageTG,
    CallbackQueryTG,
    UserTG,
    SendMessageTG,
    InlineKeyboardMarkupTG,
    InlineKeyboardButtonTG,
    SuccessTG,
    ErrorTG,
    MethodTG,
    DeleteMessagesTG,
    EditMessageTextTG,
)
from src.db.engine import SessionDep
from src.db.models import (
    RoleDB,
    UserDB,
    ContractDB,
    TicketDB,
    WriteoffDeviceDB,
    DeviceDB,
    DeviceTypeDB,
)


class Conversation:
    """Receives Telegram Update (UpdateTG), database session
    (SessionDepDB), and User from the database (UserDB). Processes
    User's Request and Returns a Response."""

    def __init__(
        self,
        update_tg: MessageUpdateTG | CallbackQueryUpdateTG,
        session: SessionDep,
        user_db: UserDB,
    ):
        self.update_tg: MessageUpdateTG | CallbackQueryUpdateTG = update_tg
        self.log_prefix: str = self.update_tg._log
        self.session: SessionDep = session
        self.user_db: UserDB = user_db
        self.state: StateJS | None = (
            StateJS.model_validate_json(user_db.state_json)
            if user_db.state_json
            else None
        )
        self.next_state: StateJS | None = None
        self.response_methods_list: list[MethodTG] = []
        self._stateless_callback_handlers: dict[
            CallbackData,
            Callable[[int, int], Coroutine[Any, Any, list[MethodTG]]]
            | Callable[[int, int], list[MethodTG]],
        ] = {
            CallbackData.ADD_TICKET: self._handle_stateless_cb_add_ticket,
            CallbackData.TICKETS: self._handle_stateless_cb_tickets,
            CallbackData.ENABLE_HIRING: self._handle_stateless_cb_enable_hiring,
            CallbackData.DISABLE_HIRING: self._handle_stateless_cb_disable_hiring,
            CallbackData.WRITEOFF_DEVICES: self._handle_stateless_cb_writeoff_devices,
        }
        self._state_handlers: dict[
            Action,
            Callable[[], Coroutine[Any, Any, list[MethodTG]]]
            | Callable[[], list[MethodTG]],
        ] = {
            # New Ticket Scenario
            Action.ENTER_TICKET_NUMBER: self._handle_ac_enter_ticket_number,
            Action.ENTER_CONTRACT_NUMBER: self._handle_ac_enter_contract_number,
            Action.PICK_DEVICE_TYPE: self._handle_ac_pick_device_type,
            Action.PICK_INSTALL_OR_RETURN: self._handle_ac_pick_install_or_return,
            Action.ENTER_DEVICE_SERIAL_NUMBER: self._handle_ac_enter_device_serial_number,
            Action.PICK_TICKET_ACTION: self._handle_ac_pick_ticket_action,
            Action.EDIT_TICKET_NUMBER: self._handle_ac_edit_ticket_number,
            Action.EDIT_CONTRACT_NUMBER: self._handle_ac_edit_contract_number,
            Action.CONFIRM_DELETE_TICKET: self._handle_ac_confirm_delete_ticket,
            Action.PICK_DEVICE_ACTION: self._handle_ac_pick_device_action,
            Action.EDIT_DEVICE_TYPE: self._handle_ac_edit_device_type,
            Action.EDIT_INSTALL_OR_RETURN: self._handle_ac_edit_install_or_return,
            Action.EDIT_DEVICE_SERIAL_NUMBER: self._handle_ac_edit_device_serial_number,
            # Ticket History Scenario
            Action.TICKETS: self._handle_ac_tickets,
            # Writeoff Scenario
            Action.WRITEOFF_DEVICES: self._handle_ac_writeoff_devices,
            Action.PICK_WRITEOFF_DEVICE_TYPE: self._handle_ac_pick_writeoff_device_type,
            Action.ENTER_WRITEOFF_DEVICE_SERIAL_NUMBER: self._handle_ac_enter_writeoff_device_serial_number,
            Action.PICK_WRITEOFF_DEVICE_ACTION: self._handle_ac_pick_writeoff_device_action,
            Action.EDIT_WRITEOFF_DEVICE_TYPE: self._handle_ac_edit_writeoff_device_type,
            Action.EDIT_WRITEOFF_DEVICE_SERIAL_NUMBER: self._handle_ac_edit_writeoff_device_serial_number,
        }
        # logger.debug(f"{self.log_prefix}Conversation instance initialized.")

    @classmethod
    async def create(
        cls,
        update_tg: MessageUpdateTG | CallbackQueryUpdateTG,
        session: SessionDep,
    ) -> Conversation | None:
        """Asynchronously creates and initializes a Conversation
        instance by fetching or creating the relevant UserDB.
        Returns None if the user should be ignored
        (e.g., guest with no hiring)."""
        user_tg: UserTG | None = cls.get_user_tg(update_tg)
        if not user_tg:
            logger.error(
                f"{update_tg._log}Configuration error: "
                "Could not extract Telegram user "
                "from supported update types "
                "(private message/callback)."
            )
            return None
        user_db: UserDB | None = await session.scalar(
            select(UserDB)
            .where(UserDB.telegram_uid == user_tg.id)
            .options(selectinload(UserDB.roles))
        )
        guest_role: RoleDB | None = None
        if user_db is None:
            logger.info(f"{update_tg._log}Guest {user_tg.full_name} is not registered.")
            hiring = await session.scalar(
                select(exists().where(UserDB.is_hiring == True))  # noqa: E712
            )
            if not hiring:
                logger.info(
                    f"{update_tg._log}User registration is disabled, "
                    f"Telegram user {user_tg.full_name} "
                    "will be ignored."
                )
                return None
            logger.info(
                f"{update_tg._log}User registration is enabled, "
                f"Telegram user {user_tg.full_name} will be added to "
                f"the database with the default '{RoleName.GUEST}' "
                "role."
            )
            guest_role = await session.scalar(
                select(RoleDB).where(RoleDB.name == RoleName.GUEST)
            )
            if guest_role is None:
                error_message = (
                    f"{update_tg._log}Configuration error: "
                    f"Default role '{RoleName.GUEST}' not found in "
                    "the database. Cannot create new user instance."
                )
                logger.error(error_message)
                raise ValueError(error_message)
            user_db = UserDB(
                telegram_uid=user_tg.id,
                first_name=user_tg.first_name,
                last_name=user_tg.last_name,
            )
            user_db.roles.append(guest_role)
            session.add(user_db)
            await session.flush()
            logger.info(
                f"{update_tg._log}User instance "
                f"{user_db.full_name} was created with "
                f"id={user_db.id} and role '{RoleName.GUEST.name}' "
                "in the database. It won't get any visible feedback "
                "to prevent unnecessary interactions with strangers "
                "from happening."
            )
            return None
        if len(user_db.roles) == 1:
            if guest_role is None:
                guest_role = await session.scalar(
                    select(RoleDB).where(RoleDB.name == RoleName.GUEST)
                )
                if guest_role is None:
                    error_message = (
                        f"{update_tg._log}Configuration error: "
                        f"Default role '{RoleName.GUEST}' not found in "
                        "the database. Cannot create new user instance."
                    )
                    logger.error(error_message)
                    raise ValueError(error_message)
            if user_db.roles[0].id == guest_role.id:
                logger.info(
                    f"{update_tg._log}User {user_db.full_name} has "
                    f"only '{RoleName.GUEST}' role and won't get any "
                    "reply."
                )
                return None
        logger.info(f"{update_tg._log}Validated user {user_db.full_name} as employee.")
        return cls(update_tg, session, user_db)

    @staticmethod
    def get_user_tg(
        update_tg: MessageUpdateTG | CallbackQueryUpdateTG,
    ) -> UserTG | None:
        """Returns Telegram user (UserTG) by extracting it from relevant
        Telegram Update object. Returns None otherwise."""
        user_tg = None
        if (
            isinstance(update_tg, MessageUpdateTG)
            and not update_tg.message.from_.is_bot
            and update_tg.message.chat.type == "private"
        ):
            user_tg = update_tg.message.from_
            logger.info(
                f"{update_tg._log}Private message from "
                f"Telegram user {user_tg.full_name}."
            )
        elif (
            isinstance(update_tg, CallbackQueryUpdateTG)
            and not update_tg.callback_query.from_.is_bot
            and update_tg.callback_query.message
            and update_tg.callback_query.message.from_.is_bot
            and update_tg.callback_query.message.from_.id == settings.bot_id
            and update_tg.callback_query.message.chat.type == "private"
        ):
            user_tg = update_tg.callback_query.from_
            logger.info(
                f"{update_tg._log}Callback query from "
                f"Telegram user {user_tg.full_name}."
            )
        return user_tg

    async def _post_method_tg(self, method_tg: MethodTG) -> SuccessTG | ErrorTG | None:
        async with httpx.AsyncClient() as client:
            # logger.debug(
            #     f"{self.log_prefix}Method '{method_tg._url}' is being "
            #     f"sent in response to {self.user_db.full_name}."
            # )
            try:
                response: httpx.Response = await client.post(
                    url=settings.get_tg_endpoint(method_tg._url),
                    json=method_tg.model_dump(exclude_none=True),
                )
                response.raise_for_status()
                # logger.debug(
                #     f"{self.log_prefix}Method '{method_tg._url}' was "
                #     "delivered to Telegram API "
                #     f"(HTTP status {response.status_code})."
                # )

                try:
                    response_data = response.json()
                    success_tg = SuccessTG.model_validate(response_data)
                    # logger.debug(
                    #     f"{self.log_prefix}Method '{method_tg._url}' "
                    #     "was accepted by Telegram API."
                    # )
                    # logger.debug(f"{response_data}")
                    return success_tg
                except ValidationError as e:
                    logger.warning(
                        f"{self.log_prefix}Unable to validate response "
                        f"{response_data} as a successful response "
                        f"for method '{method_tg._url}': {e}"
                    )
                    return None
            except httpx.TimeoutException as e:
                # If ANY type of timeout occurs, this block is executed
                logger.error(
                    f"{self.log_prefix}Request timed out for "
                    f"method '{method_tg._url}': {e}"
                )
                # Handle the timeout (e.g., retry, log, return an error indicator)
                return None  # Or raise a custom exception
            except httpx.RequestError as e:
                # Catch other request errors (like network issues, DNS failures etc.)
                logger.error(
                    f"{self.log_prefix}An error occurred while "
                    f"delivering method '{method_tg._url}': {e}"
                )
                return None
            except httpx.HTTPStatusError as e:
                # Catch HTTP status errors (4xx, 5xx responses) - these are NOT timeouts
                logger.error(
                    f"{self.log_prefix}HTTP status error for "
                    f"method '{method_tg._url}': {e}"
                )
                try:
                    error_data = e.response.json()
                    error_tg = ErrorTG.model_validate(error_data)
                    logger.warning(
                        f"{self.log_prefix}"
                        "Telegram API Error Details for "
                        f"method '{method_tg._url}': "
                        f"error_code='{error_tg.error_code}', "
                        f"description='{error_tg.description}'"
                    )
                    return error_tg  # Return the response even on error status
                except (ValidationError, Exception) as error_parsing_error:
                    logger.error(
                        f"{self.log_prefix}"
                        "Could not validate/parse Telegram error "
                        "response JSON after HTTPStatusError for "
                        f"Method '{method_tg._url}': "
                        f"{error_parsing_error}"
                    )
                    if e.response and hasattr(
                        e.response, "text"
                    ):  # Log raw text if available
                        logger.error(
                            f"{self.log_prefix}Raw error response "
                            f"body text: {e.response.text}"
                        )
                    # Correct: Return None to indicate that an HTTP status error occurred,
                    # but the error details couldn't be parsed/validated into an ErrorTG model.
                    return None
            except Exception as e:
                logger.error(
                    f"{self.log_prefix}An unexpected error occurred "
                    "during API call for method "
                    f"'{method_tg._url}': {e}",
                    exc_info=True,
                )
                return None

    async def _make_delivery(
        self,
        method_generator: Callable[[], list[MethodTG]]
        | Callable[[], Coroutine[Any, Any, list[MethodTG]]],
        ensure_delivery: bool = True,
    ) -> bool:
        def _persist_next_state():
            """Saves the next state to the user's database object if it's a valid state."""
            if isinstance(self.next_state, StateJS):
                self.user_db.state_json = self.next_state.model_dump_json(
                    exclude_none=True
                )

        response_tg: SuccessTG | ErrorTG | None
        method_tg_list: list[MethodTG]
        if inspect.iscoroutinefunction(method_generator):
            method_tg_list = await method_generator()
        else:
            method_tg_list = method_generator()  # type: ignore
        last_method_tg_index = len(method_tg_list) - 1
        success = False
        for index, method_tg in enumerate(method_tg_list):
            response_tg = await self._post_method_tg(method_tg)
            if index == last_method_tg_index:
                if isinstance(response_tg, SuccessTG):
                    _persist_next_state()
                    success = True
                elif (
                    ensure_delivery is True
                    and isinstance(method_tg, EditMessageTextTG)
                    and isinstance(response_tg, ErrorTG)
                    and response_tg.error_code == 400
                    and response_tg.description
                    in (
                        "Bad Request: message not found",
                        "Bad Request: message to edit not found",
                    )
                ):
                    method_tg = SendMessageTG(
                        chat_id=method_tg.chat_id,
                        text=method_tg.text,
                        parse_mode=method_tg.parse_mode,
                        reply_markup=method_tg.reply_markup,
                    )
                    response_tg = await self._post_method_tg(method_tg)
                    if isinstance(response_tg, SuccessTG):
                        _persist_next_state()
                        success = True
        return success

    async def process(self) -> bool:
        success = False
        initial_state = self.state
        if initial_state is None:
            success = await self._make_delivery(self._stateless_conversation)
        else:
            if initial_state.action in Action:
                success = await self._make_delivery(self._state_action_conversation)
        if success:
            final_state = self.next_state
            if initial_state is None:
                if final_state is None:
                    logger.info(
                        f"{self.log_prefix}Stateless conversation has completed."
                    )
                else:
                    logger.info(
                        f"{self.log_prefix}Entered stateful "
                        "conversation with "
                        f"Action '{final_state.action.name}'."
                    )
            else:
                if final_state is None:
                    if self.user_db.state_json is None:
                        logger.info(
                            f"{self.log_prefix}Exited stateful "
                            "conversation from "
                            f"Action '{initial_state.action.name}'."
                        )
                    else:
                        logger.info(
                            f"{self.log_prefix}Still on "
                            f"Action '{initial_state.action.name}'."
                        )
                else:
                    if initial_state.action.name != final_state.action.name:
                        logger.info(
                            f"{self.log_prefix}Advancing from "
                            f"Action '{initial_state.action.name}' "
                            f"to '{final_state.action.name}'."
                        )
                    else:
                        logger.info(
                            f"{self.log_prefix}Refreshed "
                            f"Action '{initial_state.action.name}'."
                        )
        return success

    async def _stateless_conversation(self) -> list[MethodTG]:
        logger.info(
            f"{self.log_prefix}Starting new conversation with {self.user_db.full_name}."
        )
        if self.state is not None:
            error_msg = f"{self.log_prefix}'self.state' should be None at this point."
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, MessageUpdateTG):
            message_id = self.update_tg.message.message_id
            logger.info(
                f"{self.log_prefix}Message id={message_id} from {self.user_db.full_name}."
            )
            logger.info(
                f"{self.log_prefix}Preparing main menu for {self.user_db.full_name}."
            )
            methods_tg_list.append(
                self._build_stateless_mainmenu(f"{String.PICK_A_FUNCTION}.")
            )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
            raw_data = self.update_tg.callback_query.data
            message_id = self.update_tg.callback_query.message.message_id
            chat_id = self.update_tg.callback_query.message.chat.id
            try:
                received_callback_data = CallbackData(raw_data)
                callback_handler = self._stateless_callback_handlers.get(
                    received_callback_data
                )
                logger.info(
                    f"{self.log_prefix}Got {CallbackData.__name__} "
                    f"'{received_callback_data.value}'."
                )
                if callback_handler:
                    if inspect.iscoroutinefunction(callback_handler):
                        logger.info(
                            f"{self.log_prefix}Calling async handler for "
                            f"{received_callback_data.__class__.__name__} "
                            f"'{received_callback_data.value}'."
                        )
                        methods_tg_list.extend(
                            await callback_handler(chat_id, message_id)
                        )
                    else:
                        logger.info(
                            f"{self.log_prefix}Calling sync handler for "
                            f"{received_callback_data.__class__.__name__} "
                            f"'{received_callback_data.value}'."
                        )
                        methods_tg_list.extend(callback_handler(chat_id, message_id))  # type: ignore
                else:
                    logger.info(
                        f"{self.log_prefix}No handler for "
                        f"{received_callback_data.__class__.__name__} "
                        f"'{received_callback_data.value}' was found. "
                        "Calling unrecognized callback sync handler."
                    )
                    methods_tg_list.extend(
                        self._handle_unrecognized_stateless_callback(raw_data)
                    )
            except ValueError as e:
                logger.error(
                    f"{self.log_prefix}Error processing callback '{raw_data}': {e}",
                    exc_info=True,  # This will include the traceback
                )
                methods_tg_list.extend(
                    self._handle_unrecognized_stateless_callback(raw_data)
                )
        return methods_tg_list

    async def _state_action_conversation(self) -> list[MethodTG]:
        logger.info(
            f"{self.log_prefix}Continuing conversation with {self.user_db.full_name}."
        )
        if self.state is None:
            error_msg = f"{self.log_prefix}'self.state' cannot be None at this point."
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        action_handler = self._state_handlers.get(self.state.action)
        if action_handler:
            if inspect.iscoroutinefunction(action_handler):
                logger.info(
                    f"{self.log_prefix}Calling async handler for "
                    f"{self.state.action.__class__.__name__} "
                    f"'{self.state.action.value}'."
                )
                methods_tg_list.extend(await action_handler())
            else:
                logger.info(
                    f"{self.log_prefix}Calling sync handler for "
                    f"{self.state.action.__class__.__name__} "
                    f"'{self.state.action.value}'."
                )
                methods_tg_list.extend(action_handler())  # type: ignore
        else:
            error_msg = (
                f"{self.log_prefix}Unhandled action: "
                f"'{self.state.action.value}' for user "
                f"{self.user_db.full_name}. No handler implemented."
            )
            logger.error(error_msg)
            raise NotImplementedError(error_msg)
        return methods_tg_list

    def _handle_stateless_cb_add_ticket(
        self, chat_id: int, message_id: int
    ) -> list[MethodTG]:
        """Handles CallbackData.ADD_TICKET
        in a stateless conversation."""
        # chat_id, message_id are part of the uniform signature but
        # might not be used directly by all handlers.
        logger.info(f"{self.log_prefix}Initiating ticket creation.")
        methods_tg_list: list[MethodTG] = []
        methods_tg_list.append(
            self._build_edit_to_callback_button_text(),
        )
        self.next_state = StateJS(action=Action.ENTER_TICKET_NUMBER)
        methods_tg_list.append(
            self._build_new_text_message(f"{String.ENTER_TICKET_NUMBER}."),
        )
        return methods_tg_list

    async def _handle_stateless_cb_tickets(
        self, chat_id: int, message_id: int
    ) -> list[MethodTG]:
        """Handles CallbackData.TICKETS
        in a stateless conversation."""
        # chat_id, message_id are part of the uniform signature but
        # might not be used directly by all handlers.
        logger.info(f"{self.log_prefix}Initiating tickets scenario.")
        methods_tg_list: list[MethodTG] = []
        methods_tg_list.append(
            self._build_edit_to_callback_button_text(),
        )
        self.next_state = StateJS(action=Action.TICKETS)
        methods_tg_list.append(
            await self._build_pick_tickets(f"{String.PICK_TICKETS_ACTION}."),
        )
        return methods_tg_list

    async def _handle_stateless_cb_writeoff_devices(
        self, chat_id: int, message_id: int
    ) -> list[MethodTG]:
        """Handles CallbackData.WRITEOFF_DEVICES
        in a stateless conversation."""
        # chat_id, message_id are part of the uniform signature but
        # might not be used directly by all handlers.
        logger.info(f"{self.log_prefix}Initiating writeoff devices scenario.")
        methods_tg_list: list[MethodTG] = []
        self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
        methods_tg_list.append(
            self._build_edit_to_callback_button_text(),
        )
        methods_tg_list.append(
            await self._build_pick_writeoff_devices(
                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
            ),
        )
        return methods_tg_list

    def _handle_stateless_cb_enable_hiring(
        self, chat_id: int, message_id: int
    ) -> list[MethodTG]:
        """Handles CallbackData.ENABLE_HIRING_BTN in a stateless
        conversation."""
        logger.info(f"{self.log_prefix}Attempting to enable hiring.")
        methods_tg_list: list[MethodTG] = []
        method_tg = EditMessageTextTG(
            chat_id=chat_id,
            message_id=message_id,
            text="Hiring Enabled Placeholder",
        )
        if not self.user_db.is_hiring:
            self.user_db.is_hiring = True
            method_tg.text = f"{String.HIRING_ENABLED} {String.PICK_A_FUNCTION}."
        else:
            method_tg.text = (
                f"{String.HIRING_ALREADY_ENABLED} {String.PICK_A_FUNCTION}."
            )
        method_tg.reply_markup = InlineKeyboardMarkupTG(
            inline_keyboard=self._helper_mainmenu_keyboard_rows()
        )
        methods_tg_list.append(method_tg)
        return methods_tg_list

    def _handle_stateless_cb_disable_hiring(
        self, chat_id: int, message_id: int
    ) -> list[MethodTG]:
        """Handles CallbackData.DISABLE_HIRING_BTN in a stateless
        conversation."""
        logger.info(f"{self.log_prefix}Attempting to disable hiring.")
        methods_tg_list: list[MethodTG] = []
        method_tg = EditMessageTextTG(
            chat_id=chat_id,
            message_id=message_id,
            text="Hiring Disabled Placeholder",
        )
        if self.user_db.is_hiring:
            self.user_db.is_hiring = False
            method_tg.text = f"{String.HIRING_DISABLED} {String.PICK_A_FUNCTION}."
        else:
            method_tg.text = (
                f"{String.HIRING_ALREADY_DISABLED} {String.PICK_A_FUNCTION}."
            )
        method_tg.reply_markup = InlineKeyboardMarkupTG(
            inline_keyboard=self._helper_mainmenu_keyboard_rows()
        )
        methods_tg_list.append(method_tg)
        return methods_tg_list

    def _handle_unrecognized_stateless_callback(self, raw_data: str) -> list[MethodTG]:
        """Handles unrecognized callback data in a stateless conversation."""
        logger.info(
            f"{self.log_prefix}Preparing main menu for {self.user_db.full_name}."
        )
        methods_tg_list: list[MethodTG] = []
        methods_tg_list.append(
            self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
        )
        methods_tg_list.append(
            self._build_stateless_mainmenu(
                f"{String.GOT_UNEXPECTED_DATA}. "
                f"{String.PICK_A_FUNCTION} {String.FROM_OPTIONS_BELOW}."
            )
        )
        return methods_tg_list

    async def _handle_ac_enter_ticket_number(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting ticket number.")
        methods_tg_list: list[MethodTG] = []
        current_ticket_id = self.state.ticket_id
        if current_ticket_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}"
                f"{self.user_db.full_name} "
                "is already working on a ticket "
                f"under id={current_ticket_id}. "
                "Cannot create a new ticket."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(current_ticket_id exist). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text
                if (
                    re.fullmatch(settings.ticket_number_regex, message_text)
                    and message_text != "0"
                ):
                    logger.info(
                        f"{self.log_prefix}Got correct ticket number: '{message_text}'."
                    )
                    ticket_number = int(message_text)
                    new_ticket = TicketDB(
                        number=ticket_number,
                        user_id=self.user_db.id,
                    )
                    self.session.add(new_ticket)
                    await self.session.flush()
                    self.next_state = StateJS(
                        action=Action.ENTER_CONTRACT_NUMBER,
                        ticket_id=new_ticket.id,
                    )
                    methods_tg_list.append(
                        self._build_new_text_message(f"{String.ENTER_CONTRACT_NUMBER}.")
                    )
                else:
                    logger.info(
                        f"{self.log_prefix}Got incorrect "
                        f"ticket number: '{message_text}'."
                    )
                    methods_tg_list.append(
                        self._build_new_text_message(
                            f"{String.INCORRECT_TICKET_NUMBER}. "
                            f"{String.ENTER_TICKET_NUMBER}."
                        )
                    )
            else:
                logger.info(f"{self.log_prefix}Didn't get ticket number.")
                methods_tg_list.append(
                    self._build_new_text_message(
                        f"{String.INCORRECT_TICKET_NUMBER}. "
                        f"{String.ENTER_TICKET_NUMBER}."
                    )
                )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
            logger.info(f"{self.log_prefix}Got callback data instead of ticket number.")
            methods_tg_list.append(
                self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
            )
            methods_tg_list.append(
                self._build_new_text_message(
                    f"{String.GOT_DATA_NOT_TICKET_NUMBER}. "
                    f"{String.ENTER_TICKET_NUMBER}."
                )
            )
        return methods_tg_list

    async def _handle_ac_enter_contract_number(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting contract number.")
        methods_tg_list: list[MethodTG] = []
        current_ticket_id = self.state.ticket_id
        if not current_ticket_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing current_ticket_id). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        current_ticket = await self.session.scalar(
            select(TicketDB).where(TicketDB.id == current_ticket_id)
        )
        if not current_ticket:
            logger.warning(
                f"{self.log_prefix}Current ticket "
                "was not found in the database under "
                f"id={current_ticket_id}."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text
                if (
                    re.fullmatch(settings.contract_number_regex, message_text)
                    and message_text != "0"
                ):
                    logger.info(
                        f"{self.log_prefix}Got correct "
                        f"contract number: '{message_text}'."
                    )
                    contract_number = int(message_text)
                    contract_exist = await self.session.scalar(
                        select(ContractDB).where(ContractDB.number == contract_number)
                    )
                    if contract_exist:
                        logger.info(
                            f"{self.log_prefix}Contract "
                            f"number={contract_number} was found "
                            "in the database under "
                            f"id={contract_exist.id}."
                        )
                        current_ticket_contract = contract_exist
                    else:
                        logger.info(
                            f"{self.log_prefix}Contract "
                            f"number={contract_number} was not found "
                            "in the database and will be added."
                        )
                        new_contract = ContractDB(number=contract_number)
                        self.session.add(new_contract)
                        current_ticket_contract = new_contract
                        await self.session.flush()
                    current_ticket.contract_id = current_ticket_contract.id
                    await self.session.flush()
                    self.next_state = StateJS(
                        action=Action.PICK_DEVICE_TYPE,
                        ticket_id=current_ticket_id,
                    )
                    methods_tg_list.append(
                        await self._build_pick_device_type(
                            f"{String.PICK_DEVICE_TYPE}."
                        )
                    )
                else:
                    logger.info(
                        f"{self.log_prefix}Got incorrect "
                        f"contract number: '{message_text}'."
                    )
                    methods_tg_list.append(
                        self._build_new_text_message(
                            f"{String.INCORRECT_CONTRACT_NUMBER}. "
                            f"{String.ENTER_CONTRACT_NUMBER}."
                        )
                    )
            else:
                logger.info(f"{self.log_prefix}Didn't get contract number.")
                methods_tg_list.append(
                    self._build_new_text_message(
                        f"{String.INCORRECT_CONTRACT_NUMBER}. "
                        f"{String.ENTER_CONTRACT_NUMBER}."
                    )
                )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
            logger.info(
                f"{self.log_prefix}Got callback data instead of contract number."
            )
            methods_tg_list.append(
                self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
            )
            methods_tg_list.append(
                self._build_new_text_message(
                    f"{String.GOT_DATA_NOT_CONTRACT_NUMBER}. "
                    f"{String.ENTER_CONTRACT_NUMBER}."
                )
            )
        return methods_tg_list

    async def _handle_ac_pick_device_type(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting device type choice to be made.")
        methods_tg_list: list[MethodTG] = []
        current_ticket_id = self.state.ticket_id
        if not current_ticket_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing current_ticket_id). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        current_ticket = await self.session.scalar(
            select(TicketDB)
            .where(TicketDB.id == current_ticket_id)
            .options(selectinload(TicketDB.devices))
        )
        if not current_ticket:
            logger.warning(
                f"{self.log_prefix}Current ticket "
                "was not found in the database under "
                f"id={current_ticket_id}."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        device_index = self.state.ticket_device_index
        devices_list = current_ticket.devices
        total_devices = len(devices_list)
        if device_index is not None and not (0 <= device_index <= total_devices):
            logger.error(
                f"{self.log_prefix}Error: "
                f"device_index={device_index} and "
                f"total_devices={total_devices}. "
                "Expected: "
                "0 <= device_index <= total_devices."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(incorrect device_index). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                logger.info(
                    f"{self.log_prefix}Got {CallbackData.__name__} "
                    f"'{received_callback_data.value}'."
                )
                device_type_name = self._get_device_type_name_from_callback_data(
                    received_callback_data
                )
                methods_tg_list.append(self._build_edit_to_callback_button_text())
                device_type = await self.session.scalar(
                    select(DeviceTypeDB).where(DeviceTypeDB.name == device_type_name)
                )
                if device_type is None:
                    logger.info(
                        f"{self.log_prefix}No {DeviceTypeDB.__name__} "
                        f"found for {device_type_name.name}."
                    )
                    methods_tg_list.append(
                        await self._build_pick_device_type(
                            f"{String.GOT_UNEXPECTED_DATA}. "
                            f"{String.PICK_DEVICE_TYPE} "
                            f"{String.FROM_OPTIONS_BELOW}."
                        )
                    )
                elif not device_type.is_active:
                    logger.info(
                        f"{self.log_prefix}{DeviceTypeDB.__name__} "
                        f"'{device_type.name.name}' is disabled. "
                        f"Only active {DeviceTypeDB.__name__} "
                        "is allowed."
                    )
                    methods_tg_list.append(
                        await self._build_pick_device_type(
                            f"{String.DEVICE_TYPE_IS_DISABLED}. "
                            f"{String.PICK_DEVICE_TYPE} "
                            f"{String.FROM_OPTIONS_BELOW}."
                        )
                    )
                else:
                    logger.info(
                        f"{self.log_prefix}Found active "
                        f"{DeviceTypeDB.__name__}: "
                        f"name='{device_type.name.name}' "
                        f"id={device_type.id}."
                    )
                    if device_index is None:
                        device_index = 0
                    if device_index == total_devices:
                        logger.info(
                            f"{self.log_prefix}No existing "
                            f"{DeviceDB.__name__} to work with. "
                            f"Creating new {DeviceDB.__name__} at "
                            f"devices[{device_index}] with "
                            f"{DeviceTypeDB.__name__} "
                            f"'{device_type_name.name}'."
                        )
                        device = DeviceDB(
                            ticket_id=current_ticket.id,
                            type_id=device_type.id,
                        )
                        device.type = device_type
                        devices_list.append(device)

                    else:  # 0 <= device_index < total_devices
                        logger.info(
                            f"{self.log_prefix}Working with existing "
                            f"{DeviceDB.__name__} at "
                            f"devices[{device_index}]. Changing "
                            f"{DeviceTypeDB.__name__} to "
                            f"'{device_type_name.name}'."
                        )
                        device = devices_list[device_index]
                        device.type_id = device_type.id
                        device.type = device_type
                    if device_type.is_disposable:
                        logger.info(
                            f"{self.log_prefix}{DeviceTypeDB.__name__} "
                            f"'{device_type.name.name}' "
                            "is disposable. Install or return step "
                            "will be skipped."
                        )
                        device.removal = False
                        if device_type.has_serial_number:
                            logger.info(
                                f"{self.log_prefix}"
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' "
                                "has serial number parameter. "
                                "Next step is serial number."
                            )
                            self.next_state = StateJS(
                                action=Action.ENTER_DEVICE_SERIAL_NUMBER,
                                ticket_id=current_ticket_id,
                                ticket_device_index=device_index,
                            )
                            methods_tg_list.append(
                                self._build_new_text_message(
                                    f"{String.ENTER_SERIAL_NUMBER}."
                                )
                            )
                        else:
                            logger.info(
                                f"{self.log_prefix}"
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' "
                                "doesn't have serial number parameter. "
                                "Serial number step will be skipped. "
                                "Next step is ticket menu."
                            )
                            self.next_state = StateJS(
                                action=Action.PICK_TICKET_ACTION,
                                ticket_id=current_ticket_id,
                            )
                            methods_tg_list.append(
                                await self._build_pick_ticket_action(
                                    f"{String.PICK_TICKET_ACTION}."
                                )
                            )
                    else:
                        logger.info(
                            f"{self.log_prefix}{DeviceTypeDB.__name__} "
                            f"'{device_type.name.name}' "
                            "is non-disposable. "
                            "Next step is pick install or return."
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_INSTALL_OR_RETURN,
                            ticket_id=current_ticket_id,
                            ticket_device_index=device_index,
                        )
                        methods_tg_list.append(
                            self._build_pick_install_or_return_message(
                                f"{String.PICK_INSTALL_OR_RETURN}."
                            )
                        )
                    await self.session.flush()
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Got invalid callback data "
                    f"'{raw_data}' for current device type selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    await self._build_pick_device_type(
                        f"{String.GOT_UNEXPECTED_DATA}. "
                        f"{String.PICK_DEVICE_TYPE} "
                        f"{String.FROM_OPTIONS_BELOW}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                await self._build_pick_device_type(
                    f"{String.DEVICE_TYPE_WAS_NOT_PICKED}. "
                    f"{String.PICK_DEVICE_TYPE} "
                    f"{String.FROM_OPTIONS_BELOW}."
                )
            )
        return methods_tg_list

    async def _handle_ac_pick_install_or_return(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting install or return choice to be made.")
        methods_tg_list: list[MethodTG] = []
        current_ticket_id = self.state.ticket_id
        if not current_ticket_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing current_ticket_id). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        current_ticket = await self.session.scalar(
            select(TicketDB)
            .where(TicketDB.id == current_ticket_id)
            .options(selectinload(TicketDB.devices).selectinload(DeviceDB.type))
        )
        if not current_ticket:
            logger.warning(
                f"{self.log_prefix}Current ticket "
                "was not found in the database under "
                f"id={current_ticket_id}."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        device_index = self.state.ticket_device_index
        devices_list = current_ticket.devices
        total_devices = len(devices_list)
        if device_index is None or not (0 <= device_index < total_devices):
            logger.error(
                f"{self.log_prefix}Error: "
                f"device_index={device_index} and "
                f"total_devices={total_devices}. "
                "Expected: "
                "0 <= device_index < total_devices."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(incorrect device_index). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.INSTALL_DEVICE,
                CallbackData.RETURN_DEVICE,
            ]
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                if received_callback_data in expected_callback_data:
                    logger.info(
                        f"{self.log_prefix}Got expected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    if received_callback_data == CallbackData.INSTALL_DEVICE:
                        logger.info(
                            f"{self.log_prefix}{CallbackData.__name__} "
                            f"'{received_callback_data.value}' "
                            "matches 'install' option "
                            "(removal=False)."
                        )
                        removal = False
                    elif received_callback_data == CallbackData.RETURN_DEVICE:
                        logger.info(
                            f"{self.log_prefix}{CallbackData.__name__} "
                            f"'{received_callback_data.value}' "
                            "matches 'return' option "
                            "(removal=True)."
                        )
                        removal = True
                    else:
                        error_msg = (
                            f"{self.log_prefix}{CallbackData.__name__} "
                            f"{received_callback_data.value}"
                            "is in expected callback list, "
                            "but somehow doesn't match anything."
                        )
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                    methods_tg_list.append(self._build_edit_to_callback_button_text())
                    logger.info(
                        f"{self.log_prefix}Working with "
                        f"{DeviceDB.__name__} "
                        f"at devices[{device_index}]. Setting "
                        f"'removal' flag to '{removal}'."
                    )
                    device = devices_list[device_index]
                    device.removal = removal
                    await self.session.flush()
                    device_type = device.type
                    if device_type.has_serial_number:
                        logger.info(
                            f"{self.log_prefix}{DeviceTypeDB.__name__} "
                            f"'{device_type.name.name}' "
                            "has serial number parameter. "
                            "Next step is serial number."
                        )
                        self.next_state = StateJS(
                            action=Action.ENTER_DEVICE_SERIAL_NUMBER,
                            ticket_id=current_ticket_id,
                            ticket_device_index=device_index,
                        )
                        methods_tg_list.append(
                            self._build_new_text_message(
                                f"{String.ENTER_SERIAL_NUMBER}."
                            )
                        )
                    else:
                        logger.info(
                            f"{self.log_prefix}{DeviceTypeDB.__name__} "
                            f"'{device_type.name.name}' "
                            "doesn't have serial number parameter. "
                            "Serial number step will be skipped. "
                            "Next step is ticket menu."
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            ticket_id=current_ticket_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_ticket_action(
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                else:
                    logger.info(
                        f"{self.log_prefix}Got unexpected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    raise ValueError
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Got invalid callback data "
                    f"'{raw_data}' for current device action selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    self._build_pick_install_or_return_message(
                        f"{String.GOT_UNEXPECTED_DATA}. "
                        f"{String.PICK_INSTALL_OR_RETURN}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                self._build_pick_install_or_return_message(
                    f"{String.DEVICE_ACTION_WAS_NOT_PICKED}. "
                    f"{String.PICK_INSTALL_OR_RETURN}."
                )
            )
        return methods_tg_list

    async def _handle_ac_enter_device_serial_number(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting device serial number.")
        methods_tg_list: list[MethodTG] = []
        current_ticket_id = self.state.ticket_id
        if not current_ticket_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing current_ticket_id). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        current_ticket = await self.session.scalar(
            select(TicketDB)
            .where(TicketDB.id == current_ticket_id)
            .options(selectinload(TicketDB.devices))
        )
        if not current_ticket:
            logger.warning(
                f"{self.log_prefix}Current ticket "
                "was not found in the database under "
                f"id={current_ticket_id}."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        device_index = self.state.ticket_device_index
        devices_list = current_ticket.devices
        total_devices = len(devices_list)
        if device_index is None or not (0 <= device_index < total_devices):
            logger.error(
                f"{self.log_prefix}Error: "
                f"device_index={device_index} and "
                f"total_devices={total_devices}. "
                "Expected: "
                "0 <= device_index < total_devices."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(incorrect device_index). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text.upper()
                if (
                    re.fullmatch(settings.serial_number_regex, message_text)
                    and message_text != "0"
                ):
                    logger.info(
                        f"{self.log_prefix}Got correct device "
                        "serial number (forced uppercase): "
                        f"'{message_text}'."
                    )
                    logger.info(
                        f"{self.log_prefix}Working with "
                        f"{DeviceDB.__name__} "
                        f"at devices[{device_index}]. Setting "
                        f"serial number to '{message_text}'."
                    )
                    device = devices_list[device_index]
                    device.serial_number = message_text
                    self.next_state = StateJS(
                        action=Action.PICK_TICKET_ACTION,
                        ticket_id=current_ticket_id,
                    )
                    methods_tg_list.append(
                        await self._build_pick_ticket_action(
                            f"{String.PICK_TICKET_ACTION}."
                        )
                    )
                else:
                    logger.info(
                        f"{self.log_prefix}Got incorrect device "
                        "serial number (forced uppercase): "
                        f"'{message_text}'."
                    )
                    methods_tg_list.append(
                        self._build_new_text_message(
                            f"{String.INCORRECT_SERIAL_NUMBER}. "
                            f"{String.ENTER_SERIAL_NUMBER}."
                        )
                    )
            else:
                logger.info(f"{self.log_prefix}Didn't get device serial number.")
                methods_tg_list.append(
                    self._build_new_text_message(
                        f"{String.INCORRECT_SERIAL_NUMBER}. "
                        f"{String.ENTER_SERIAL_NUMBER}."
                    )
                )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
            logger.info(
                f"{self.log_prefix}Got callback data instead of device serial number."
            )
            methods_tg_list.append(
                self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
            )
            methods_tg_list.append(
                self._build_new_text_message(
                    f"{String.GOT_DATA_NOT_SERIAL_NUMBER}. "
                    f"{String.ENTER_SERIAL_NUMBER}."
                )
            )
        return methods_tg_list

    async def _handle_ac_pick_ticket_action(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting ticket menu choice to be made.")
        methods_tg_list: list[MethodTG] = []
        current_ticket_id = self.state.ticket_id
        if not current_ticket_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing current_ticket_id). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        current_ticket = await self.session.scalar(
            select(TicketDB)
            .where(TicketDB.id == current_ticket_id)
            .options(
                selectinload(TicketDB.contract),
                selectinload(TicketDB.devices),
            )
        )
        if not current_ticket:
            logger.warning(
                f"{self.log_prefix}Current ticket "
                "was not found in the database under "
                f"id={current_ticket_id}."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.EDIT_TICKET_NUMBER,
                CallbackData.EDIT_CONTRACT_NUMBER,
                CallbackData.DELETE_TICKET,
                CallbackData.RETURN_TO_TICKETS,
                CallbackData.RETURN_TO_MAIN_MENU,
            ]
            total_devices = len(current_ticket.devices)
            if total_devices < settings.devices_per_ticket:
                expected_callback_data.append(CallbackData.ADD_DEVICE)
            if current_ticket.is_closed:
                expected_callback_data.append(CallbackData.REOPEN_TICKET)
            elif (
                total_devices > 0
                and current_ticket.contract
                and current_ticket.contract.number
            ):
                expected_callback_data.append(CallbackData.CLOSE_TICKET)
            expected_devices_callbacks = [
                CallbackData[f"DEVICE_{index}"]
                for index in range(min(total_devices, settings.devices_per_ticket))
            ]
            expected_callback_data.extend(expected_devices_callbacks)
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                if received_callback_data in expected_callback_data:
                    logger.info(
                        f"{self.log_prefix}Got expected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    if received_callback_data == CallbackData.EDIT_TICKET_NUMBER:
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.EDIT_TICKET_NUMBER}."
                            )
                        )
                        self.next_state = StateJS(
                            action=Action.EDIT_TICKET_NUMBER,
                            ticket_id=current_ticket_id,
                        )
                        methods_tg_list.append(
                            self._build_new_text_message(
                                f"{String.ENTER_NEW_TICKET_NUMBER}."
                            )
                        )
                    elif received_callback_data == CallbackData.EDIT_CONTRACT_NUMBER:
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.EDIT_CONTRACT_NUMBER}."
                            )
                        )
                        self.next_state = StateJS(
                            action=Action.EDIT_CONTRACT_NUMBER,
                            ticket_id=current_ticket_id,
                        )
                        methods_tg_list.append(
                            self._build_new_text_message(
                                f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                            )
                        )
                    elif received_callback_data in expected_devices_callbacks:
                        callback_device_index = (
                            self._get_callback_data_ending_as_integer(
                                received_callback_data
                            )
                        )
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.EDIT_DEVICE} {callback_device_index + 1}."
                            )
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_DEVICE_ACTION,
                            ticket_id=current_ticket_id,
                            ticket_device_index=callback_device_index,
                        )
                        methods_tg_list.append(
                            await self._build_pick_device_action_message(
                                f"{String.PICK_DEVICE_ACTION}."
                            )
                        )
                    elif received_callback_data == CallbackData.ADD_DEVICE:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_DEVICE_TYPE,
                            ticket_id=current_ticket_id,
                            ticket_device_index=total_devices,
                        )
                        methods_tg_list.append(
                            await self._build_pick_device_type(
                                f"{String.PICK_DEVICE_TYPE}."
                            )
                        )
                    elif received_callback_data == CallbackData.REOPEN_TICKET:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        current_ticket.is_closed = False
                        await self.session.flush()
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            ticket_id=current_ticket_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_ticket_action(
                                f"{String.OPEN_TICKET_ICON} "
                                f"{String.TICKET_REOPENED}. "
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                    elif received_callback_data == CallbackData.CLOSE_TICKET:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        current_ticket.is_closed = True
                        await self.session.flush()
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            ticket_id=current_ticket_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_ticket_action(
                                f"{String.CLOSED_TICKET_ICON} "
                                f"{String.TICKET_CLOSED}. "
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                    elif received_callback_data == CallbackData.DELETE_TICKET:
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.TRASHCAN_ICON} "
                                f"{String.CLOSE_TICKET_NUMBER} "
                                f"{current_ticket.number}."
                            )
                        )
                        self.next_state = StateJS(
                            action=Action.CONFIRM_DELETE_TICKET,
                            ticket_id=current_ticket_id,
                        )
                        methods_tg_list.append(
                            self._build_pick_confirm_delete_ticket_message(
                                f"{String.CONFIRM_TICKET_DELETION}."
                            )
                        )
                    elif received_callback_data == CallbackData.RETURN_TO_TICKETS:
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.RETURNING_TO_TICKETS}."
                            )
                        )
                        self.next_state = StateJS(action=Action.TICKETS)
                        methods_tg_list.append(
                            await self._build_pick_tickets(
                                f"{String.YOU_LEFT_TICKET}. {String.PICK_TICKETS_ACTION}."
                            )
                        )
                    elif received_callback_data == CallbackData.RETURN_TO_MAIN_MENU:
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.RETURNING_TO_MAIN_MENU}."
                            )
                        )
                        self.next_state = None
                        self.user_db.state_json = None
                        methods_tg_list.append(
                            self._build_stateless_mainmenu(
                                f"{String.YOU_LEFT_TICKET}. {String.PICK_A_FUNCTION}."
                            )
                        )
                else:
                    logger.info(
                        f"{self.log_prefix}Got unexpected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    raise ValueError
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Got invalid callback data "
                    f"'{raw_data}' for current ticket menu selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    await self._build_pick_ticket_action(
                        f"{String.GOT_UNEXPECTED_DATA}. {String.PICK_TICKET_ACTION}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                await self._build_pick_ticket_action(
                    f"{String.TICKET_ACTION_WAS_NOT_PICKED}. "
                    f"{String.PICK_TICKET_ACTION}."
                )
            )
        return methods_tg_list

    async def _handle_ac_edit_ticket_number(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting new ticket number.")
        methods_tg_list: list[MethodTG] = []
        current_ticket_id = self.state.ticket_id
        if not current_ticket_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing current_ticket_id). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        current_ticket = await self.session.scalar(
            select(TicketDB).where(TicketDB.id == current_ticket_id)
        )
        if not current_ticket:
            logger.warning(
                f"{self.log_prefix}Current ticket "
                "was not found in the database under "
                f"id={current_ticket_id}."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text
                if (
                    re.fullmatch(settings.ticket_number_regex, message_text)
                    and message_text != "0"
                ):
                    logger.info(
                        f"{self.log_prefix}Got correct new "
                        f"ticket number: '{message_text}'."
                    )
                    new_ticket_number = int(message_text)
                    if current_ticket.number != new_ticket_number:
                        logger.info(
                            f"{self.log_prefix}New ticket "
                            f"number={new_ticket_number} "
                            "is different from old ticket "
                            f"number={current_ticket.number}. "
                            "Applying change."
                        )
                        current_ticket.number = new_ticket_number
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            ticket_id=current_ticket_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_ticket_action(
                                f"{String.TICKET_NUMBER_WAS_EDITED}. "
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                    else:
                        logger.info(
                            f"{self.log_prefix}New ticket "
                            f"number={new_ticket_number} "
                            "is the same as old ticket "
                            f"number={current_ticket.number}. "
                            "No change needed."
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            ticket_id=current_ticket_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_ticket_action(
                                f"{String.TICKET_NUMBER_REMAINS_THE_SAME}. "
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                else:
                    logger.info(
                        f"{self.log_prefix}Got incorrect "
                        f"new ticket number: '{message_text}'."
                    )
                    methods_tg_list.append(
                        self._build_new_text_message(
                            f"{String.INCORRECT_TICKET_NUMBER}. "
                            f"{String.ENTER_NEW_TICKET_NUMBER}."
                        )
                    )
            else:
                logger.info(f"{self.log_prefix}Didn't get new ticket number.")
                methods_tg_list.append(
                    self._build_new_text_message(
                        f"{String.INCORRECT_TICKET_NUMBER}. "
                        f"{String.ENTER_NEW_TICKET_NUMBER}."
                    )
                )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
            logger.info(
                f"{self.log_prefix}Got callback data instead of new ticket number."
            )
            methods_tg_list.append(
                self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
            )
            methods_tg_list.append(
                self._build_new_text_message(
                    f"{String.GOT_DATA_NOT_TICKET_NUMBER}. "
                    f"{String.ENTER_NEW_TICKET_NUMBER}."
                )
            )
        return methods_tg_list

    async def _handle_ac_edit_contract_number(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting new contract number.")
        methods_tg_list: list[MethodTG] = []
        current_ticket_id = self.state.ticket_id
        if not current_ticket_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing current_ticket_id). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        current_ticket = await self.session.scalar(
            select(TicketDB)
            .where(TicketDB.id == current_ticket_id)
            .options(selectinload(TicketDB.contract))
        )
        if not current_ticket:
            logger.warning(
                f"{self.log_prefix}Current ticket "
                "was not found in the database under "
                f"id={current_ticket_id}."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text
                if (
                    re.fullmatch(settings.contract_number_regex, message_text)
                    and message_text != "0"
                ):
                    logger.info(
                        f"{self.log_prefix}Got correct new "
                        f"contract number: '{message_text}'."
                    )
                    new_contract_number = int(message_text)
                    if not current_ticket.contract:
                        logger.info(
                            f"{self.log_prefix}Ticket had no previous "
                            "contract number. Applying new contract "
                            f"number={new_contract_number}."
                        )
                        contract_exist = await self.session.scalar(
                            select(ContractDB).where(
                                ContractDB.number == new_contract_number
                            )
                        )
                        if contract_exist:
                            logger.info(
                                f"{self.log_prefix}New contract "
                                f"number={new_contract_number} was "
                                "found in the database under "
                                f"id={contract_exist.id}. "
                            )
                            current_ticket.contract = contract_exist
                        else:
                            logger.info(
                                f"{self.log_prefix}New contract "
                                f"number={new_contract_number} was not "
                                "found in the database and will be added."
                            )
                            new_contract = ContractDB(number=new_contract_number)
                            self.session.add(new_contract)
                            current_ticket.contract = new_contract
                        await self.session.flush()
                        await self.session.refresh(current_ticket)
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            ticket_id=current_ticket_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_ticket_action(
                                f"{String.CONTRACT_NUMBER_WAS_EDITED}. "
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                    elif current_ticket.contract.number != new_contract_number:
                        logger.info(
                            f"{self.log_prefix}New contract "
                            f"number={new_contract_number} "
                            "is different from old contract "
                            f"number={current_ticket.contract.number}. "
                            "Applying change."
                        )
                        old_contract_id = current_ticket.contract.id
                        old_contract_number = current_ticket.contract.number
                        contract_exist = await self.session.scalar(
                            select(ContractDB).where(
                                ContractDB.number == new_contract_number
                            )
                        )
                        if contract_exist:
                            logger.info(
                                f"{self.log_prefix}New contract "
                                f"number={new_contract_number} was "
                                "found in the database under "
                                f"id={contract_exist.id}. "
                            )
                            current_ticket.contract = contract_exist
                        else:
                            logger.info(
                                f"{self.log_prefix}New contract "
                                f"number={new_contract_number} was not "
                                "found in the database and will be added."
                            )
                            new_contract = ContractDB(number=new_contract_number)
                            self.session.add(new_contract)
                            current_ticket.contract = new_contract
                        await self.session.flush()
                        await self.session.refresh(current_ticket)
                        old_contract = await self.session.scalar(
                            select(ContractDB)
                            .where(ContractDB.number == old_contract_number)
                            .options(selectinload(ContractDB.tickets))
                        )
                        if old_contract:
                            if not old_contract.tickets:
                                logger.info(
                                    f"{self.log_prefix}Old contract "
                                    f"number={old_contract.number} "
                                    f"id={old_contract.id} "
                                    "was associated only with the "
                                    "current ticket "
                                    f"number={current_ticket.number} "
                                    f"id={current_ticket.id}. "
                                    "Marking old contract for deletion."
                                )
                                await self.session.delete(old_contract)
                            else:
                                logger.info(
                                    f"{self.log_prefix}Old contract "
                                    f"number={old_contract.number} "
                                    f"id={old_contract.id} "
                                    "is still associated with other "
                                    "ticket IDs: "
                                    f"{[ticket.id for ticket in old_contract.tickets]}. "
                                    "It will NOT be deleted."
                                )
                        else:
                            logger.info(
                                f"{self.log_prefix}Old contract "
                                f"number={old_contract_number} "
                                f"id={old_contract_id} "
                                "was not found in the database."
                            )
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            ticket_id=current_ticket_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_ticket_action(
                                f"{String.CONTRACT_NUMBER_WAS_EDITED}. "
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                    else:
                        logger.info(
                            f"{self.log_prefix}New contract "
                            f"number={new_contract_number} "
                            "is the same as old contract "
                            f"number={current_ticket.contract.number}. "
                            "No change needed."
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            ticket_id=current_ticket_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_ticket_action(
                                f"{String.CONTRACT_NUMBER_REMAINS_THE_SAME}. "
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                else:
                    logger.info(
                        f"{self.log_prefix}Got incorrect new "
                        f"contract number: '{message_text}'."
                    )
                    methods_tg_list.append(
                        self._build_new_text_message(
                            f"{String.INCORRECT_CONTRACT_NUMBER}. "
                            f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                        )
                    )
            else:
                logger.info(f"{self.log_prefix}Didn't get new contract number.")
                methods_tg_list.append(
                    self._build_new_text_message(
                        f"{String.INCORRECT_CONTRACT_NUMBER}. "
                        f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                    )
                )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
            logger.info(
                f"{self.log_prefix}Got callback data instead of new contract number."
            )
            methods_tg_list.append(
                self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
            )
            methods_tg_list.append(
                self._build_new_text_message(
                    f"{String.GOT_DATA_NOT_CONTRACT_NUMBER}. "
                    f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                )
            )
        return methods_tg_list

    async def _handle_ac_confirm_delete_ticket(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting delete ticket confirmation.")
        methods_tg_list: list[MethodTG] = []
        current_ticket_id = self.state.ticket_id
        if not current_ticket_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing current_ticket_id). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        current_ticket = await self.session.scalar(
            select(TicketDB).where(TicketDB.id == current_ticket_id)
        )
        if not current_ticket:
            logger.warning(
                f"{self.log_prefix}Current ticket "
                "was not found in the database under "
                f"id={current_ticket_id}."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.CONFIRM_DELETE_TICKET,
                CallbackData.CHANGED_MY_MIND,
            ]
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                if received_callback_data in expected_callback_data:
                    logger.info(
                        f"{self.log_prefix}Got expected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    if received_callback_data == CallbackData.CONFIRM_DELETE_TICKET:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        current_contract_id = current_ticket.contract_id
                        current_ticket_number = current_ticket.number
                        logger.info(
                            f"{self.log_prefix}Marking ticket "
                            f"number={current_ticket.number} "
                            f"id={current_ticket.id} for deletion."
                        )
                        await self.session.delete(current_ticket)
                        await self.session.flush()
                        if current_contract_id:
                            current_contract = await self.session.scalar(
                                select(ContractDB)
                                .where(ContractDB.id == current_contract_id)
                                .options(selectinload(ContractDB.tickets))
                            )
                            if current_contract:
                                if not current_contract.tickets:
                                    logger.info(
                                        f"{self.log_prefix}Associated "
                                        "contract "
                                        f"number={current_contract.number} "
                                        f"id={current_contract.id} "
                                        "was associated only with the "
                                        "current ticket "
                                        f"number={current_ticket_number} "
                                        f"id={current_ticket_id}. "
                                        "Marking contract for deletion."
                                    )
                                    await self.session.delete(current_contract)
                                else:
                                    logger.info(
                                        f"{self.log_prefix}Associated "
                                        "contract "
                                        f"number={current_contract.number} "
                                        f"id={current_contract.id} "
                                        "is still associated with other "
                                        "ticket IDs: "
                                        f"{[ticket.id for ticket in current_contract.tickets]}. "
                                        "It will NOT be deleted."
                                    )
                            else:
                                logger.warning(
                                    f"{self.log_prefix}Associated "
                                    "contract was not found "
                                    "in the database under "
                                    f"id={current_contract_id}. "
                                    "Skipping associated contract "
                                    "deletion."
                                )
                        else:
                            logger.info(
                                f"{self.log_prefix}Current ticket "
                                f"number={current_ticket_number} "
                                f"id={current_ticket_id} was not "
                                "associated with any contract."
                            )
                        self.next_state = StateJS(action=Action.TICKETS)
                        methods_tg_list.append(
                            await self._build_pick_tickets(
                                f"{String.TRASHCAN_ICON} "
                                f"{String.TICKET_DELETED}. "
                                f"{String.PICK_TICKETS_ACTION}."
                            )
                        )
                    else:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            ticket_id=current_ticket_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_ticket_action(
                                f"{String.TICKET_DELETION_CANCELLED}. "
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                else:
                    logger.info(
                        f"{self.log_prefix}Got unexpected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    raise ValueError
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Got invalid callback data "
                    f"'{raw_data}' for current ticket deletion "
                    "confirmation menu selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    self._build_pick_confirm_delete_ticket_message(
                        f"{String.GOT_UNEXPECTED_DATA}. "
                        f"{String.CONFIRM_TICKET_DELETION}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                self._build_pick_confirm_delete_ticket_message(
                    f"{String.DELETE_TICKET_ACTION_WAS_NOT_PICKED}. "
                    f"{String.CONFIRM_TICKET_DELETION}."
                )
            )
        return methods_tg_list

    async def _handle_ac_pick_device_action(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting device menu choice to be made.")
        methods_tg_list: list[MethodTG] = []
        current_ticket_id = self.state.ticket_id
        if not current_ticket_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing current_ticket_id). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        current_ticket = await self.session.scalar(
            select(TicketDB)
            .where(TicketDB.id == current_ticket_id)
            .options(selectinload(TicketDB.devices).selectinload(DeviceDB.type))
        )
        if not current_ticket:
            logger.warning(
                f"{self.log_prefix}Current ticket "
                "was not found in the database under "
                f"id={current_ticket_id}."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        device_index = self.state.ticket_device_index
        devices_list = current_ticket.devices
        total_devices = len(devices_list)
        if device_index is None or not (0 <= device_index < total_devices):
            logger.error(
                f"{self.log_prefix}Error: "
                f"device_index={device_index} and "
                f"total_devices={total_devices}. "
                "Expected: "
                "0 <= device_index < total_devices."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(incorrect device_index). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.EDIT_DEVICE_TYPE,
                CallbackData.DELETE_DEVICE,
            ]
            logger.info(
                f"{self.log_prefix}Working with "
                f"{DeviceDB.__name__} "
                f"at devices[{device_index}]."
            )
            device = devices_list[device_index]
            if not device.type.is_disposable:
                if device.removal is True:
                    expected_callback_data.append(CallbackData.RETURN_DEVICE)
                elif device.removal is False:
                    expected_callback_data.append(CallbackData.INSTALL_DEVICE)
                else:
                    raise ValueError(
                        f"{DeviceDB.__name__} is non-disposable "
                        f"but 'removal' is '{device.removal}'."
                    )
            if device.type.has_serial_number:
                expected_callback_data.append(CallbackData.EDIT_DEVICE_SERIAL_NUMBER)
                if device.serial_number is not None:
                    expected_callback_data.append(CallbackData.EDIT_TICKET)
            else:
                if device.serial_number is None:
                    expected_callback_data.append(CallbackData.EDIT_TICKET)
                else:
                    raise ValueError(
                        f"{DeviceTypeDB.__name__} has no serial number "
                        f"but {DeviceDB.__name__} has "
                        f"serial_number={device.serial_number}"
                    )
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                if received_callback_data in expected_callback_data:
                    logger.info(
                        f"{self.log_prefix}Got expected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    if received_callback_data == CallbackData.EDIT_DEVICE_TYPE:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state = StateJS(
                            action=Action.EDIT_DEVICE_TYPE,
                            ticket_id=current_ticket_id,
                            ticket_device_index=device_index,
                        )
                        methods_tg_list.append(
                            await self._build_pick_device_type(
                                f"{String.PICK_NEW_DEVICE_TYPE}."
                            )
                        )
                    elif received_callback_data in [
                        CallbackData.RETURN_DEVICE,
                        CallbackData.INSTALL_DEVICE,
                    ]:
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.EDIT_INSTALL_OR_RETURN}."
                            )
                        )
                        self.next_state = StateJS(
                            action=Action.EDIT_INSTALL_OR_RETURN,
                            ticket_id=current_ticket_id,
                            ticket_device_index=device_index,
                        )
                        methods_tg_list.append(
                            self._build_pick_install_or_return_message(
                                f"{String.PICK_INSTALL_OR_RETURN}."
                            )
                        )
                    elif (
                        received_callback_data == CallbackData.EDIT_DEVICE_SERIAL_NUMBER
                    ):
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.EDIT_SERIAL_NUMBER}."
                            )
                        )
                        self.next_state = StateJS(
                            action=Action.EDIT_DEVICE_SERIAL_NUMBER,
                            ticket_id=current_ticket_id,
                            ticket_device_index=device_index,
                        )
                        methods_tg_list.append(
                            self._build_new_text_message(
                                f"{String.ENTER_NEW_SERIAL_NUMBER}."
                            )
                        )
                    elif received_callback_data == CallbackData.EDIT_TICKET:
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.RETURNING_TO_TICKET}."
                            )
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            ticket_id=current_ticket_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_ticket_action(
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                    elif received_callback_data == CallbackData.DELETE_DEVICE:
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.RETURNING_TO_TICKET}."
                            )
                        )
                        await self.session.delete(device)
                        await self.session.flush()
                        await self.session.refresh(current_ticket, ["devices"])
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.DEVICE_WAS_DELETED_FROM_TICKET}."
                            )
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            ticket_id=current_ticket_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_ticket_action(
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                else:
                    logger.info(
                        f"{self.log_prefix}Got unexpected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    raise ValueError
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Got invalid callback data "
                    f"'{raw_data}' for current device menu selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    await self._build_pick_device_action_message(
                        f"{String.GOT_UNEXPECTED_DATA}. {String.PICK_DEVICE_ACTION}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                await self._build_pick_device_action_message(
                    f"{String.DEVICE_ACTION_WAS_NOT_PICKED}. "
                    f"{String.PICK_DEVICE_ACTION}."
                )
            )
        return methods_tg_list

    async def _handle_ac_edit_device_type(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting new device type choice to be made.")
        methods_tg_list: list[MethodTG] = []
        current_ticket_id = self.state.ticket_id
        if not current_ticket_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing current_ticket_id). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        current_ticket = await self.session.scalar(
            select(TicketDB)
            .where(TicketDB.id == current_ticket_id)
            .options(selectinload(TicketDB.devices).selectinload(DeviceDB.type))
        )
        if not current_ticket:
            logger.warning(
                f"{self.log_prefix}Current ticket "
                "was not found in the database under "
                f"id={current_ticket_id}."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        device_index = self.state.ticket_device_index
        devices_list = current_ticket.devices
        total_devices = len(devices_list)
        if device_index is None or not (0 <= device_index < total_devices):
            logger.error(
                f"{self.log_prefix}Error: "
                f"device_index={device_index} and "
                f"total_devices={total_devices}. "
                "Expected: "
                "0 <= device_index < total_devices."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(incorrect device_index). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                logger.info(
                    f"{self.log_prefix}Got {CallbackData.__name__} "
                    f"'{received_callback_data.value}'."
                )
                device_type_name = self._get_device_type_name_from_callback_data(
                    received_callback_data
                )
                methods_tg_list.append(self._build_edit_to_callback_button_text())
                device_type = await self.session.scalar(
                    select(DeviceTypeDB).where(DeviceTypeDB.name == device_type_name)
                )
                if device_type is None:
                    logger.info(
                        f"{self.log_prefix}No {DeviceTypeDB.__name__} "
                        f"found for {received_callback_data.name}."
                    )
                    methods_tg_list.append(
                        await self._build_pick_device_type(
                            f"{String.GOT_UNEXPECTED_DATA}. "
                            f"{String.PICK_DEVICE_TYPE} "
                            f"{String.FROM_OPTIONS_BELOW}."
                        )
                    )
                elif not device_type.is_active:
                    logger.info(
                        f"{self.log_prefix}{DeviceTypeDB.__name__} "
                        f"'{device_type.name.name}' is disabled."
                    )
                    methods_tg_list.append(
                        await self._build_pick_device_type(
                            f"{String.DEVICE_TYPE_IS_DISABLED}. "
                            f"{String.PICK_DEVICE_TYPE} "
                            f"{String.FROM_OPTIONS_BELOW}."
                        )
                    )
                else:
                    logger.info(
                        f"{self.log_prefix}Found active "
                        f"{DeviceTypeDB.__name__}: "
                        f"name='{device_type.name.name}' "
                        f"id={device_type.id}."
                    )
                    logger.info(
                        f"{self.log_prefix}Working with "
                        f"{DeviceDB.__name__} at "
                        f"devices[{device_index}]."
                    )
                    device = devices_list[device_index]
                    if device.type.name.name != device_type.name.name:
                        logger.info(
                            f"{self.log_prefix}New "
                            f"{DeviceTypeDB.__name__} "
                            f"'{device_type.name.name}' "
                            "is different from old "
                            f"{DeviceTypeDB.__name__} "
                            f"'{device.type.name.name}'. "
                            "Applying change."
                        )
                        device.type_id = device_type.id
                        device.type = device_type
                        if device_type.is_disposable:
                            logger.info(
                                f"{self.log_prefix}"
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' is "
                                "disposable. Install action set."
                            )
                            device.removal = False
                        else:
                            logger.info(
                                f"{self.log_prefix}"
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' "
                                "is not disposable. "
                                "Keeping install or return as is."
                            )
                        if device_type.has_serial_number:
                            logger.info(
                                f"{self.log_prefix}"
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' "
                                "has serial number parameter. "
                                "Keeping serial number intact."
                            )
                        else:
                            logger.info(
                                f"{self.log_prefix}"
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' "
                                "doesn't have serial number parameter. "
                                "Setting serial number to None."
                            )
                            device.serial_number = None
                        self.next_state = StateJS(
                            action=Action.PICK_DEVICE_ACTION,
                            ticket_id=current_ticket_id,
                            ticket_device_index=device_index,
                        )
                        methods_tg_list.append(
                            await self._build_pick_device_action_message(
                                f"{String.DEVICE_TYPE_WAS_EDITED}. "
                                f"{String.PICK_DEVICE_ACTION}."
                            )
                        )
                    else:
                        logger.info(
                            f"{self.log_prefix}New "
                            f"{DeviceTypeDB.__name__} "
                            f"'{device_type.name.name}' "
                            "is the same as old "
                            f"{DeviceTypeDB.__name__} "
                            f"'{device.type.name.name}'. "
                            "No change needed."
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_DEVICE_ACTION,
                            ticket_id=current_ticket_id,
                            ticket_device_index=device_index,
                        )
                        methods_tg_list.append(
                            await self._build_pick_device_action_message(
                                f"{String.DEVICE_TYPE_REMAINS_THE_SAME}. "
                                f"{String.PICK_DEVICE_ACTION}."
                            )
                        )
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Got invalid callback data "
                    f"'{raw_data}' for current device new type selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    await self._build_pick_device_type(
                        f"{String.GOT_UNEXPECTED_DATA}. "
                        f"{String.PICK_DEVICE_TYPE} "
                        f"{String.FROM_OPTIONS_BELOW}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                await self._build_pick_device_type(
                    f"{String.DEVICE_TYPE_WAS_NOT_PICKED}. "
                    f"{String.PICK_DEVICE_TYPE} "
                    f"{String.FROM_OPTIONS_BELOW}."
                )
            )
        return methods_tg_list

    async def _handle_ac_edit_install_or_return(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(
            f"{self.log_prefix}Awaiting new install or return choice to be made."
        )
        methods_tg_list: list[MethodTG] = []
        current_ticket_id = self.state.ticket_id
        if not current_ticket_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing current_ticket_id). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        current_ticket = await self.session.scalar(
            select(TicketDB)
            .where(TicketDB.id == current_ticket_id)
            .options(selectinload(TicketDB.devices).selectinload(DeviceDB.type))
        )
        if not current_ticket:
            logger.warning(
                f"{self.log_prefix}Current ticket "
                "was not found in the database under "
                f"id={current_ticket_id}."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        device_index = self.state.ticket_device_index
        devices_list = current_ticket.devices
        total_devices = len(devices_list)
        if device_index is None or not (0 <= device_index < total_devices):
            logger.error(
                f"{self.log_prefix}Error: "
                f"device_index={device_index} and "
                f"total_devices={total_devices}. "
                "Expected: "
                "0 <= device_index < total_devices."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(incorrect device_index). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.INSTALL_DEVICE,
                CallbackData.RETURN_DEVICE,
            ]
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                if received_callback_data in expected_callback_data:
                    logger.info(
                        f"{self.log_prefix}Got expected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    if received_callback_data == CallbackData.INSTALL_DEVICE:
                        logger.info(
                            f"{self.log_prefix}{CallbackData.__name__} "
                            f"'{received_callback_data.value}' "
                            "matches 'install' option "
                            "(removal=False)."
                        )
                        removal = False
                    elif received_callback_data == CallbackData.RETURN_DEVICE:
                        logger.info(
                            f"{self.log_prefix}{CallbackData.__name__} "
                            f"'{received_callback_data.value}' "
                            "matches 'return' option "
                            "(removal=True)."
                        )
                        removal = True
                    else:
                        error_msg = (
                            f"{self.log_prefix}{CallbackData.__name__} "
                            f"{received_callback_data.value}"
                            "is in expected callback list, "
                            "but somehow doesn't match anything."
                        )
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                    methods_tg_list.append(self._build_edit_to_callback_button_text())
                    logger.info(
                        f"{self.log_prefix}Working with "
                        f"{DeviceDB.__name__} "
                        f"at devices[{device_index}]."
                    )
                    device = devices_list[device_index]
                    if device.removal != removal:
                        logger.info(
                            f"{self.log_prefix}{DeviceDB.__name__} "
                            f"new removal flag '{removal}' "
                            "is different from device "
                            f"old removal flag '{device.removal}'. "
                            "Applying change."
                        )
                        device.removal = removal
                        device_type = device.type
                        if device_type.is_disposable:
                            error_msg = (
                                f"{self.log_prefix}"
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' "
                                "is disposable, install or return "
                                "selection not available."
                            )
                            logger.error(error_msg)
                            raise ValueError(error_msg)
                        self.next_state = StateJS(
                            action=Action.PICK_DEVICE_ACTION,
                            ticket_id=current_ticket_id,
                            ticket_device_index=device_index,
                        )
                        methods_tg_list.append(
                            await self._build_pick_device_action_message(
                                f"{String.INSTALL_OR_RETURN_WAS_EDITED}. "
                                f"{String.PICK_DEVICE_ACTION}."
                            )
                        )
                    else:
                        logger.info(
                            f"{self.log_prefix}{DeviceDB.__name__} "
                            f"new removal flag '{removal}' "
                            "is the same as device "
                            f"old removal flag '{device.removal}'. "
                            "No change needed."
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_DEVICE_ACTION,
                            ticket_id=current_ticket_id,
                            ticket_device_index=device_index,
                        )
                        methods_tg_list.append(
                            await self._build_pick_device_action_message(
                                f"{String.INSTALL_OR_RETURN_REMAINS_THE_SAME}. "
                                f"{String.PICK_DEVICE_ACTION}."
                            )
                        )
                else:
                    logger.info(
                        f"{self.log_prefix}Got unexpected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    raise ValueError
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Got invalid callback data "
                    f"'{raw_data}' for current device new action selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    self._build_pick_install_or_return_message(
                        f"{String.GOT_UNEXPECTED_DATA}. "
                        f"{String.PICK_INSTALL_OR_RETURN}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                self._build_pick_install_or_return_message(
                    f"{String.DEVICE_ACTION_WAS_NOT_PICKED}. "
                    f"{String.PICK_INSTALL_OR_RETURN}."
                )
            )
        return methods_tg_list

    async def _handle_ac_edit_device_serial_number(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting new device serial number.")
        methods_tg_list: list[MethodTG] = []
        current_ticket_id = self.state.ticket_id
        if not current_ticket_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing current_ticket_id). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        current_ticket = await self.session.scalar(
            select(TicketDB)
            .where(TicketDB.id == current_ticket_id)
            .options(selectinload(TicketDB.devices))
        )
        if not current_ticket:
            logger.warning(
                f"{self.log_prefix}Current ticket "
                "was not found in the database under "
                f"id={current_ticket_id}."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        device_index = self.state.ticket_device_index
        devices_list = current_ticket.devices
        total_devices = len(devices_list)
        if device_index is None or not (0 <= device_index < total_devices):
            logger.error(
                f"{self.log_prefix}Error: "
                f"device_index={device_index} and "
                f"total_devices={total_devices}. "
                "Expected: "
                "0 <= device_index < total_devices."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(incorrect device_index). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text.upper()
                if (
                    re.fullmatch(settings.serial_number_regex, message_text)
                    and message_text != "0"
                ):
                    logger.info(
                        f"{self.log_prefix}Got correct new device "
                        "serial number (forced uppercase): "
                        f"'{message_text}'."
                    )
                    logger.info(
                        f"{self.log_prefix}Working with "
                        f"{DeviceDB.__name__} "
                        f"at devices[{device_index}]."
                    )
                    device = devices_list[device_index]
                    if device.serial_number != message_text:
                        logger.info(
                            f"{self.log_prefix}Device new "
                            f"serial_number={message_text} "
                            "is different from device old "
                            f"serial_number={device.serial_number}. "
                            "Applying change."
                        )
                        device.serial_number = message_text
                        self.next_state = StateJS(
                            action=Action.PICK_DEVICE_ACTION,
                            ticket_id=current_ticket_id,
                            ticket_device_index=device_index,
                        )
                        methods_tg_list.append(
                            await self._build_pick_device_action_message(
                                f"{String.SERIAL_NUMBER_WAS_EDITED}. "
                                f"{String.PICK_DEVICE_ACTION}."
                            )
                        )
                    else:
                        logger.info(
                            f"{self.log_prefix}Device new "
                            f"serial_number={message_text} "
                            "is the same as device old "
                            f"serial_number={device.serial_number}. "
                            "No change needed."
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_DEVICE_ACTION,
                            ticket_id=current_ticket_id,
                            ticket_device_index=device_index,
                        )
                        methods_tg_list.append(
                            await self._build_pick_device_action_message(
                                f"{String.SERIAL_NUMBER_REMAINS_THE_SAME}. "
                                f"{String.PICK_DEVICE_ACTION}."
                            )
                        )
                else:
                    logger.info(
                        f"{self.log_prefix}Got incorrect new device "
                        "serial number (forced uppercase): "
                        f"'{message_text}'."
                    )
                    methods_tg_list.append(
                        self._build_new_text_message(
                            f"{String.INCORRECT_SERIAL_NUMBER}. "
                            f"{String.ENTER_NEW_SERIAL_NUMBER}."
                        )
                    )
            else:
                logger.info(f"{self.log_prefix}Didn't get new device serial number.")
                methods_tg_list.append(
                    self._build_new_text_message(
                        f"{String.INCORRECT_SERIAL_NUMBER}. "
                        f"{String.ENTER_NEW_SERIAL_NUMBER}."
                    )
                )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
            logger.info(
                f"{self.log_prefix}Got callback data instead of new device serial number."
            )
            methods_tg_list.append(
                self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
            )
            methods_tg_list.append(
                self._build_new_text_message(
                    f"{String.GOT_DATA_NOT_SERIAL_NUMBER}. "
                    f"{String.ENTER_NEW_SERIAL_NUMBER}."
                )
            )
        return methods_tg_list

    async def _handle_ac_tickets(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting tickets choice to be made.")
        methods_tg_list: list[MethodTG] = []
        if self.state.tickets_dict is None:
            logger.error(
                f"{self.log_prefix}Current state is missing "
                "in-memory tickets dictionary."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing tickets_dict). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            lookback_days = settings.tickets_history_lookback_days
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)
            total_recent_tickets = (
                await self.session.scalar(
                    select(func.count())
                    .select_from(TicketDB)
                    .where(
                        TicketDB.user_id == self.user_db.id,
                        TicketDB.created_at >= cutoff_date,
                    )
                )
                or 0  # Mypy fix
            )
            tickets_per_page = settings.tickets_per_page
            total_pages = max(
                1,
                (total_recent_tickets + tickets_per_page - 1) // tickets_per_page,
            )
            last_page_index = total_pages - 1
            page_index = self.state.tickets_page
            if page_index is None:
                page_index = 0
            logger.info(
                f"{self.log_prefix}The user is on "
                f"page {page_index + 1} of {total_pages}."
            )
            logger.debug(
                f"page_index={page_index}, "
                f"last_page_index={last_page_index}, "
                f"total_pages={total_pages}."
            )
            expected_callback_data = [
                CallbackData.ADD_TICKET,
                CallbackData.RETURN_TO_MAIN_MENU,
            ]
            if page_index < last_page_index:
                expected_callback_data.append(CallbackData.PREV_ONES)
            if page_index > 0:
                expected_callback_data.append(CallbackData.NEXT_ONES)
            expected_tickets_callbacks = [
                CallbackData[f"TICKET_{index}"]
                for index in range(min(total_recent_tickets, settings.tickets_per_page))
            ]
            expected_callback_data.extend(expected_tickets_callbacks)
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                if received_callback_data in expected_callback_data:
                    logger.info(
                        f"{self.log_prefix}Got expected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    if received_callback_data == CallbackData.ADD_TICKET:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state = StateJS(action=Action.ENTER_TICKET_NUMBER)
                        methods_tg_list.append(
                            self._build_new_text_message(
                                f"{String.ENTER_TICKET_NUMBER}."
                            ),
                        )
                    elif received_callback_data == CallbackData.RETURN_TO_MAIN_MENU:
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.RETURNING_TO_MAIN_MENU}."
                            )
                        )
                        self.next_state = None
                        self.user_db.state_json = None
                        methods_tg_list.append(
                            self._build_stateless_mainmenu(
                                f"{String.YOU_LEFT_TICKETS}. {String.PICK_A_FUNCTION}."
                            )
                        )
                    elif received_callback_data in expected_tickets_callbacks:
                        callback_ticket_index = (
                            self._get_callback_data_ending_as_integer(
                                received_callback_data
                            )
                        )
                        ticket_id = self.state.tickets_dict.get(callback_ticket_index)
                        if ticket_id is None:
                            logger.error(
                                f"{self.log_prefix}"
                                "Could not find ticket id "
                                f"for index {callback_ticket_index}. "
                                f"{CallbackData.__name__} "
                                f"'{received_callback_data.value}' "
                                "may be stale or current state "
                                "may be inconsistent."
                            )
                            raise ValueError("Invalid ticket index in state.")
                        button_text = self._get_callback_button_text()
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.EDIT_TICKET} {button_text}."
                            )
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            ticket_id=ticket_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_ticket_action(
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                    elif received_callback_data == CallbackData.PREV_ONES:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state = StateJS(
                            action=Action.TICKETS,
                            tickets_page=page_index + 1,
                        )
                        methods_tg_list.append(
                            await self._build_pick_tickets(
                                f"{String.PICK_TICKETS_ACTION}."
                            )
                        )
                    elif received_callback_data == CallbackData.NEXT_ONES:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state = StateJS(
                            action=Action.TICKETS,
                            tickets_page=page_index - 1,
                        )
                        methods_tg_list.append(
                            await self._build_pick_tickets(
                                f"{String.PICK_TICKETS_ACTION}."
                            )
                        )
                else:
                    logger.info(
                        f"{self.log_prefix}Got unexpected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    raise ValueError
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Got invalid callback data "
                    f"'{raw_data}' for tickets selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    await self._build_pick_tickets(
                        f"{String.GOT_UNEXPECTED_DATA}. {String.PICK_TICKETS_ACTION}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                await self._build_pick_tickets(
                    f"{String.ACTION_WAS_NOT_PICKED}. {String.PICK_TICKETS_ACTION}."
                )
            )
        return methods_tg_list

    async def _handle_ac_writeoff_devices(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting writeoff devices choice to be made.")
        methods_tg_list: list[MethodTG] = []
        if self.state.writeoff_devices_dict is None:
            logger.error(
                f"{self.log_prefix}Current state is missing "
                "in-memory writeoff devices dictionary."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing writeoff_devices_dict). "
                f"{String.PICK_A_FUNCTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            writeoffs_per_page = settings.writeoffs_per_page
            total_writeoff_devices = (
                await self.session.scalar(
                    select(func.count())
                    .select_from(WriteoffDeviceDB)
                    .where(WriteoffDeviceDB.user_id == self.user_db.id)
                )
                or 0  # Mypy fix
            )
            total_pages = max(
                1,
                (total_writeoff_devices + writeoffs_per_page - 1) // writeoffs_per_page,
            )
            last_page_index = total_pages - 1
            page_index = self.state.writeoff_devices_page
            if page_index is None:
                page_index = 0
            logger.info(
                f"{self.log_prefix}The user is on "
                f"page {page_index + 1} of {total_pages}."
            )
            logger.debug(
                f"page_index={page_index}, "
                f"last_page_index={last_page_index}, "
                f"total_pages={total_pages}."
            )
            expected_callback_data = [
                CallbackData.ADD_WRITEOFF_DEVICE,
                CallbackData.RETURN_TO_MAIN_MENU,
            ]
            if page_index < last_page_index:
                expected_callback_data.append(CallbackData.PREV_ONES)
            if page_index > 0:
                expected_callback_data.append(CallbackData.NEXT_ONES)
            expected_writeoff_devices_callbacks = [
                CallbackData[f"DEVICE_{index}"]
                for index in range(
                    min(total_writeoff_devices, settings.writeoffs_per_page)
                )
            ]
            expected_callback_data.extend(expected_writeoff_devices_callbacks)
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                if received_callback_data in expected_callback_data:
                    logger.info(
                        f"{self.log_prefix}Got expected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    if received_callback_data == CallbackData.ADD_WRITEOFF_DEVICE:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_WRITEOFF_DEVICE_TYPE
                        )
                        methods_tg_list.append(
                            await self._build_pick_writeoff_device_type(
                                f"{String.PICK_WRITEOFF_DEVICE_TYPE}."
                            )
                        )
                    elif received_callback_data == CallbackData.RETURN_TO_MAIN_MENU:
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.RETURNING_TO_MAIN_MENU}."
                            )
                        )
                        self.next_state = None
                        self.user_db.state_json = None
                        methods_tg_list.append(
                            self._build_stateless_mainmenu(
                                f"{String.YOU_LEFT_WRITEOFF_DEVICES}. {String.PICK_A_FUNCTION}."
                            )
                        )
                    elif received_callback_data in expected_writeoff_devices_callbacks:
                        callback_device_index = (
                            self._get_callback_data_ending_as_integer(
                                received_callback_data
                            )
                        )
                        writeoff_device_id = self.state.writeoff_devices_dict.get(
                            callback_device_index
                        )
                        if writeoff_device_id is None:
                            logger.error(
                                f"{self.log_prefix}"
                                "Could not find writeoff device id "
                                f"for index {callback_device_index}. "
                                f"{CallbackData.__name__} "
                                f"'{received_callback_data.value}' "
                                "may be stale or current state "
                                "may be inconsistent."
                            )
                            raise ValueError("Invalid writeoff device index in state.")
                        button_text = self._get_callback_button_text()
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.EDIT_WRITEOFF_DEVICE} {button_text}."
                            )
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_WRITEOFF_DEVICE_ACTION,
                            writeoff_device_id=writeoff_device_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_writeoff_device(
                                f"{String.PICK_WRITEOFF_DEVICE_ACTION}."
                            )
                        )
                    elif received_callback_data == CallbackData.PREV_ONES:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state = StateJS(
                            action=Action.WRITEOFF_DEVICES,
                            writeoff_devices_page=page_index + 1,
                        )
                        methods_tg_list.append(
                            await self._build_pick_writeoff_devices(
                                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
                            )
                        )
                    elif received_callback_data == CallbackData.NEXT_ONES:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state = StateJS(
                            action=Action.WRITEOFF_DEVICES,
                            writeoff_devices_page=page_index - 1,
                        )
                        methods_tg_list.append(
                            await self._build_pick_writeoff_devices(
                                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
                            )
                        )
                else:
                    logger.info(
                        f"{self.log_prefix}Got unexpected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    raise ValueError
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Got invalid callback data "
                    f"'{raw_data}' for writeoff devices selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    await self._build_pick_writeoff_devices(
                        f"{String.GOT_UNEXPECTED_DATA}. {String.PICK_WRITEOFF_DEVICES_ACTION}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                await self._build_pick_writeoff_devices(
                    f"{String.ACTION_WAS_NOT_PICKED}. {String.PICK_WRITEOFF_DEVICES_ACTION}."
                )
            )
        return methods_tg_list

    async def _handle_ac_pick_writeoff_device_type(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(
            f"{self.log_prefix}Awaiting writeoff device type choice to be made."
        )
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                logger.info(
                    f"{self.log_prefix}Got {CallbackData.__name__} "
                    f"'{received_callback_data.value}'."
                )
                device_type_name = self._get_device_type_name_from_callback_data(
                    received_callback_data
                )
                methods_tg_list.append(self._build_edit_to_callback_button_text())
                device_type = await self.session.scalar(
                    select(DeviceTypeDB).where(DeviceTypeDB.name == device_type_name)
                )
                if device_type is None:
                    logger.info(
                        f"{self.log_prefix}No {DeviceTypeDB.__name__} "
                        f"found for {device_type_name.name}."
                    )
                    methods_tg_list.append(
                        await self._build_pick_writeoff_device_type(
                            f"{String.GOT_UNEXPECTED_DATA}. "
                            f"{String.PICK_WRITEOFF_DEVICE_TYPE} "
                            f"{String.FROM_OPTIONS_BELOW}."
                        )
                    )
                elif device_type.is_disposable:
                    logger.info(
                        f"{self.log_prefix}{DeviceTypeDB.__name__} "
                        f"'{device_type.name.name}' is disposable. "
                        f"Only non-disposable {DeviceTypeDB.__name__} "
                        "is allowed."
                    )
                    methods_tg_list.append(
                        await self._build_pick_writeoff_device_type(
                            f"{String.DEVICE_TYPE_IS_DISPOSABLE}. "
                            f"{String.PICK_WRITEOFF_DEVICE_TYPE} "
                            f"{String.FROM_OPTIONS_BELOW}."
                        )
                    )
                else:
                    logger.info(
                        f"{self.log_prefix}Found non-disposable "
                        f"{DeviceTypeDB.__name__}: "
                        f"name='{device_type.name.name}' "
                        f"id={device_type.id}."
                    )
                    logger.info(
                        f"{self.log_prefix}Creating new "
                        f"{WriteoffDeviceDB.__name__} with "
                        f"{DeviceTypeDB.__name__} "
                        f"'{device_type_name.name}'."
                    )
                    writeoff_device = WriteoffDeviceDB(
                        user_id=self.user_db.id,
                        type_id=device_type.id,
                    )
                    writeoff_device.type = device_type
                    self.session.add(writeoff_device)
                    await self.session.flush()
                    if device_type.has_serial_number:
                        logger.info(
                            f"{self.log_prefix}{DeviceTypeDB.__name__} "
                            f"'{device_type.name.name}' "
                            "has serial number parameter. "
                            "Next step is serial number."
                        )
                        self.next_state = StateJS(
                            action=Action.ENTER_WRITEOFF_DEVICE_SERIAL_NUMBER,
                            writeoff_device_id=writeoff_device.id,
                        )
                        methods_tg_list.append(
                            self._build_new_text_message(
                                f"{String.ENTER_SERIAL_NUMBER}."
                            )
                        )
                    else:
                        logger.info(
                            f"{self.log_prefix}{DeviceTypeDB.__name__} "
                            f"'{device_type.name.name}' "
                            "doesn't have serial number parameter. "
                            "Serial number step will be skipped. "
                            "Next step is writeoff devices menu."
                        )
                        self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
                        methods_tg_list.append(
                            await self._build_pick_writeoff_devices(
                                f"{String.YOU_ADDED_WRITEOFF_DEVICE}. "
                                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
                            ),
                        )
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Got invalid callback data "
                    f"'{raw_data}' for current writeoff device "
                    "type selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    await self._build_pick_writeoff_device_type(
                        f"{String.GOT_UNEXPECTED_DATA}. "
                        f"{String.PICK_WRITEOFF_DEVICE_TYPE} "
                        f"{String.FROM_OPTIONS_BELOW}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                await self._build_pick_writeoff_device_type(
                    f"{String.DEVICE_TYPE_WAS_NOT_PICKED}. "
                    f"{String.PICK_WRITEOFF_DEVICE_TYPE} "
                    f"{String.FROM_OPTIONS_BELOW}."
                )
            )
        return methods_tg_list

    async def _handle_ac_enter_writeoff_device_serial_number(
        self,
    ) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting writeoff device serial number.")
        methods_tg_list: list[MethodTG] = []
        writeoff_device_id = self.state.writeoff_device_id
        if not writeoff_device_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any writeoff device."
            )
            logger.info(f"{self.log_prefix}Going back to the writeoff devices menu.")
            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
            method_tg = await self._build_pick_writeoff_devices(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing writeoff_device_id). "
                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        writeoff_device = await self.session.scalar(
            select(WriteoffDeviceDB).where(WriteoffDeviceDB.id == writeoff_device_id)
        )
        if not writeoff_device:
            logger.error(
                f"{self.log_prefix}Current writeoff device "
                "was not found in the database under "
                f"id={writeoff_device_id}. "
                "Cannot populate its serial number."
            )
            logger.info(f"{self.log_prefix} Going back to the writeoff devices menu.")
            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
            method_tg = await self._build_pick_writeoff_devices(
                f"{String.WRITEOFF_DEVICE_WAS_NOT_FOUND}. "
                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text.upper()
                if (
                    re.fullmatch(settings.serial_number_regex, message_text)
                    and message_text != "0"
                ):
                    logger.info(
                        f"{self.log_prefix}Got correct writeoff device "
                        "serial number (forced uppercase): "
                        f"'{message_text}'."
                    )
                    logger.info(
                        f"{self.log_prefix}Working with "
                        f"{WriteoffDeviceDB.__name__} "
                        f"id={writeoff_device.id}. Setting "
                        f"serial number to '{message_text}'."
                    )
                    writeoff_device.serial_number = message_text
                    await self.session.flush()
                    logger.info(f"{self.log_prefix}Next step is writeoff devices menu.")
                    self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
                    methods_tg_list.append(
                        await self._build_pick_writeoff_devices(
                            f"{String.YOU_ADDED_WRITEOFF_DEVICE}. "
                            f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
                        ),
                    )
                else:
                    logger.info(
                        f"{self.log_prefix}Got incorrect writeoff "
                        "device serial number (forced uppercase): "
                        f"'{message_text}'."
                    )
                    methods_tg_list.append(
                        self._build_new_text_message(
                            f"{String.INCORRECT_SERIAL_NUMBER}. "
                            f"{String.ENTER_SERIAL_NUMBER}."
                        )
                    )
            else:
                logger.info(
                    f"{self.log_prefix}Didn't get writeoff device serial number."
                )
                methods_tg_list.append(
                    self._build_new_text_message(
                        f"{String.INCORRECT_SERIAL_NUMBER}. "
                        f"{String.ENTER_SERIAL_NUMBER}."
                    )
                )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
            logger.info(
                f"{self.log_prefix}Got callback data "
                "instead of writeoff device serial number."
            )
            methods_tg_list.append(
                self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
            )
            methods_tg_list.append(
                self._build_new_text_message(
                    f"{String.GOT_DATA_NOT_SERIAL_NUMBER}. "
                    f"{String.ENTER_SERIAL_NUMBER}."
                )
            )
        return methods_tg_list

    async def _handle_ac_pick_writeoff_device_action(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(
            f"{self.log_prefix}Awaiting writeoff device menu choice to be made."
        )
        methods_tg_list: list[MethodTG] = []
        writeoff_device_id = self.state.writeoff_device_id
        if not writeoff_device_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any writeoff device."
            )
            logger.info(f"{self.log_prefix} Going back to the writeoff devices menu.")
            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
            method_tg = await self._build_pick_writeoff_devices(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing writeoff_device_id). "
                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        writeoff_device = await self.session.scalar(
            select(WriteoffDeviceDB)
            .where(WriteoffDeviceDB.id == writeoff_device_id)
            .options(selectinload(WriteoffDeviceDB.type))
        )
        if not writeoff_device:
            logger.error(
                f"{self.log_prefix}Current writeoff device "
                "was not found in the database under "
                f"id={writeoff_device_id}. Cannot edit it."
            )
            logger.info(f"{self.log_prefix} Going back to the writeoff devices menu.")
            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
            method_tg = await self._build_pick_writeoff_devices(
                f"{String.WRITEOFF_DEVICE_WAS_NOT_FOUND}. "
                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.EDIT_WRITEOFF_DEVICE_TYPE,
                CallbackData.DELETE_WRITEOFF_DEVICE,
            ]
            device_type_name = String[writeoff_device.type.name.name]
            logger.info(
                f"{self.log_prefix}Working with "
                f"{WriteoffDeviceDB.__name__} "
                f"id={writeoff_device_id} device type "
                f"'{device_type_name}'."
            )
            if writeoff_device.type.is_disposable:
                logger.error(
                    f"{self.log_prefix}Configuration error: "
                    f"{DeviceTypeDB.__name__} '{device_type_name}' "
                    "is not an eligible writeoff device type "
                    "as it is disposable. You shouldn't be seeing "
                    "this ever."
                )
                logger.info(
                    f"{self.log_prefix}Going back to the writeoff devices menu."
                )
                self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
                methods_tg_list.append(
                    await self._build_pick_writeoff_devices(
                        f"{String.WRITEOFF_DEVICE_IS_INCORRECT}. "
                        f"{String.CONTACT_THE_ADMINISTRATOR}. "
                        f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
                    )
                )
            else:
                if writeoff_device.type.has_serial_number:
                    expected_callback_data.append(
                        CallbackData.EDIT_WRITEOFF_DEVICE_SERIAL_NUMBER
                    )
                    if writeoff_device.serial_number is not None:
                        expected_callback_data.append(CallbackData.WRITEOFF_DEVICES)
                else:
                    expected_callback_data.append(CallbackData.WRITEOFF_DEVICES)
                    if writeoff_device.serial_number is not None:
                        logger.error(
                            f"{self.log_prefix}Database integrity error: "
                            f"{DeviceTypeDB.__name__} has no serial number "
                            f"but {WriteoffDeviceDB.__name__} has "
                            f"serial_number={writeoff_device.serial_number}. "
                            "Investigate the logic."
                        )
                raw_data = self.update_tg.callback_query.data
                try:
                    received_callback_data = CallbackData(raw_data)
                    if received_callback_data in expected_callback_data:
                        logger.info(
                            f"{self.log_prefix}Got expected "
                            f"{CallbackData.__name__} "
                            f"'{received_callback_data.value}'."
                        )
                        if (
                            received_callback_data
                            == CallbackData.EDIT_WRITEOFF_DEVICE_TYPE
                        ):
                            methods_tg_list.append(
                                self._build_edit_to_callback_button_text()
                            )
                            self.next_state = StateJS(
                                action=Action.EDIT_WRITEOFF_DEVICE_TYPE,
                                writeoff_device_id=writeoff_device_id,
                            )
                            methods_tg_list.append(
                                await self._build_pick_writeoff_device_type(
                                    f"{String.PICK_NEW_WRITEOFF_DEVICE_TYPE}."
                                )
                            )
                        elif (
                            received_callback_data
                            == CallbackData.EDIT_WRITEOFF_DEVICE_SERIAL_NUMBER
                        ):
                            methods_tg_list.append(
                                self._build_edit_to_text_message(
                                    f"{String.EDIT_SERIAL_NUMBER}."
                                )
                            )
                            self.next_state = StateJS(
                                action=Action.EDIT_WRITEOFF_DEVICE_SERIAL_NUMBER,
                                writeoff_device_id=writeoff_device_id,
                            )
                            methods_tg_list.append(
                                self._build_new_text_message(
                                    f"{String.ENTER_NEW_SERIAL_NUMBER}."
                                )
                            )
                        elif received_callback_data == CallbackData.WRITEOFF_DEVICES:
                            methods_tg_list.append(
                                self._build_edit_to_text_message(
                                    f"{String.RETURNING_TO_WRITEOFF_DEVICES}."
                                )
                            )
                            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
                            methods_tg_list.append(
                                await self._build_pick_writeoff_devices(
                                    f"{String.YOU_LEFT_WRITEOFF_DEVICE}. "
                                    f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
                                ),
                            )
                        elif (
                            received_callback_data
                            == CallbackData.DELETE_WRITEOFF_DEVICE
                        ):
                            await self.session.delete(writeoff_device)
                            await self.session.flush()
                            methods_tg_list.append(
                                self._build_edit_to_text_message(
                                    f"{String.DEVICE_WAS_DELETED_FROM_WRITEOFF}."
                                )
                            )
                            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
                            methods_tg_list.append(
                                await self._build_pick_writeoff_devices(
                                    f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
                                )
                            )
                    else:
                        logger.info(
                            f"{self.log_prefix}Got unexpected "
                            f"{CallbackData.__name__} "
                            f"'{received_callback_data.value}'."
                        )
                        raise ValueError
                except ValueError:
                    logger.info(
                        f"{self.log_prefix}Got invalid callback data "
                        f"'{raw_data}' for current writeoff device "
                        "menu selection."
                    )
                    methods_tg_list.append(
                        self._build_edit_to_text_message(
                            f"{String.GOT_UNEXPECTED_DATA}."
                        )
                    )
                    methods_tg_list.append(
                        await self._build_pick_writeoff_device(
                            f"{String.GOT_UNEXPECTED_DATA}. "
                            f"{String.PICK_WRITEOFF_DEVICE_ACTION}."
                        )
                    )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                await self._build_pick_writeoff_device(
                    f"{String.WRITEOFF_DEVICE_ACTION_WAS_NOT_PICKED}. "
                    f"{String.PICK_WRITEOFF_DEVICE_ACTION}."
                )
            )
        return methods_tg_list

    async def _handle_ac_edit_writeoff_device_type(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(
            f"{self.log_prefix}Awaiting new writeoff device type choice to be made."
        )
        methods_tg_list: list[MethodTG] = []
        writeoff_device_id = self.state.writeoff_device_id
        if not writeoff_device_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any writeoff device."
            )
            logger.info(f"{self.log_prefix} Going back to the writeoff devices menu.")
            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
            method_tg = await self._build_pick_writeoff_devices(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing writeoff_device_id). "
                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        writeoff_device = await self.session.scalar(
            select(WriteoffDeviceDB)
            .where(WriteoffDeviceDB.id == writeoff_device_id)
            .options(selectinload(WriteoffDeviceDB.type))
        )
        if not writeoff_device:
            logger.error(
                f"{self.log_prefix}Current writeoff device "
                "was not found in the database under "
                f"id={writeoff_device_id}. "
                "Cannot edit its device type."
            )
            logger.info(f"{self.log_prefix} Going back to the writeoff devices menu.")
            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
            method_tg = await self._build_pick_writeoff_devices(
                f"{String.WRITEOFF_DEVICE_WAS_NOT_FOUND}. "
                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                logger.info(
                    f"{self.log_prefix}Got {CallbackData.__name__} "
                    f"'{received_callback_data.value}'."
                )
                device_type_name = self._get_device_type_name_from_callback_data(
                    received_callback_data
                )
                methods_tg_list.append(self._build_edit_to_callback_button_text())
                device_type = await self.session.scalar(
                    select(DeviceTypeDB).where(DeviceTypeDB.name == device_type_name)
                )
                if device_type is None:
                    logger.info(
                        f"{self.log_prefix}No {DeviceTypeDB.__name__} "
                        f"found for {received_callback_data.name}."
                    )
                    methods_tg_list.append(
                        await self._build_pick_writeoff_device_type(
                            f"{String.GOT_UNEXPECTED_DATA}. "
                            f"{String.PICK_NEW_WRITEOFF_DEVICE_TYPE} "
                            f"{String.FROM_OPTIONS_BELOW}."
                        )
                    )
                elif device_type.is_disposable:
                    logger.info(
                        f"{self.log_prefix}{DeviceTypeDB.__name__} "
                        f"'{device_type.name.name}' is disposable. "
                        f"Only non-disposable {DeviceTypeDB.__name__} "
                        "is allowed."
                    )
                    methods_tg_list.append(
                        await self._build_pick_writeoff_device_type(
                            f"{String.DEVICE_TYPE_IS_DISPOSABLE}. "
                            f"{String.PICK_NEW_WRITEOFF_DEVICE_TYPE} "
                            f"{String.FROM_OPTIONS_BELOW}."
                        )
                    )
                else:
                    logger.info(
                        f"{self.log_prefix}Found non-disposable "
                        f"{DeviceTypeDB.__name__}: "
                        f"name='{device_type.name.name}' "
                        f"id={device_type.id}."
                    )
                    if writeoff_device.type.name.name != device_type.name.name:
                        logger.info(
                            f"{self.log_prefix}New "
                            f"{DeviceTypeDB.__name__} "
                            f"'{device_type.name.name}' "
                            "is different from old "
                            f"{DeviceTypeDB.__name__} "
                            f"'{writeoff_device.type.name.name}'. "
                            "Applying change."
                        )
                        writeoff_device.type_id = device_type.id
                        writeoff_device.type = device_type
                        if device_type.has_serial_number:
                            logger.info(
                                f"{self.log_prefix}"
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' "
                                "has serial number parameter. "
                                "Keeping serial number intact."
                            )
                        else:
                            logger.info(
                                f"{self.log_prefix}"
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' "
                                "doesn't have serial number parameter. "
                                "Setting serial number to None."
                            )
                            writeoff_device.serial_number = None
                        await self.session.flush()
                        self.next_state = StateJS(
                            action=Action.PICK_WRITEOFF_DEVICE_ACTION,
                            writeoff_device_id=writeoff_device_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_writeoff_device(
                                f"{String.DEVICE_TYPE_WAS_EDITED}. "
                                f"{String.PICK_WRITEOFF_DEVICE_ACTION}."
                            )
                        )
                    else:
                        logger.info(
                            f"{self.log_prefix}New "
                            f"{DeviceTypeDB.__name__} "
                            f"'{device_type.name.name}' "
                            "is the same as old "
                            f"{DeviceTypeDB.__name__} "
                            f"'{writeoff_device.type.name.name}'. "
                            "No change needed."
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_WRITEOFF_DEVICE_ACTION,
                            writeoff_device_id=writeoff_device_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_writeoff_device(
                                f"{String.DEVICE_TYPE_REMAINS_THE_SAME}. "
                                f"{String.PICK_WRITEOFF_DEVICE_ACTION}."
                            )
                        )
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Got invalid callback data "
                    f"'{raw_data}' for current device new type selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    await self._build_pick_writeoff_device_type(
                        f"{String.GOT_UNEXPECTED_DATA}. "
                        f"{String.PICK_NEW_WRITEOFF_DEVICE_TYPE} "
                        f"{String.FROM_OPTIONS_BELOW}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                await self._build_pick_writeoff_device_type(
                    f"{String.DEVICE_TYPE_WAS_NOT_PICKED}. "
                    f"{String.PICK_NEW_WRITEOFF_DEVICE_TYPE} "
                    f"{String.FROM_OPTIONS_BELOW}."
                )
            )
        return methods_tg_list

    async def _handle_ac_edit_writeoff_device_serial_number(self) -> list[MethodTG]:
        assert self.state is not None
        logger.info(f"{self.log_prefix}Awaiting new writeoff device serial number.")
        methods_tg_list: list[MethodTG] = []
        writeoff_device_id = self.state.writeoff_device_id
        if not writeoff_device_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any writeoff device."
            )
            logger.info(f"{self.log_prefix} Going back to the writeoff devices menu.")
            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
            method_tg = await self._build_pick_writeoff_devices(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing writeoff_device_id). "
                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        writeoff_device = await self.session.scalar(
            select(WriteoffDeviceDB).where(WriteoffDeviceDB.id == writeoff_device_id)
        )
        if not writeoff_device:
            logger.error(
                f"{self.log_prefix}Current writeoff device "
                "was not found in the database under "
                f"id={writeoff_device_id}. "
                "Cannot edit its serial number."
            )
            logger.info(f"{self.log_prefix} Going back to the writeoff devices menu.")
            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
            method_tg = await self._build_pick_writeoff_devices(
                f"{String.WRITEOFF_DEVICE_WAS_NOT_FOUND}. "
                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
            )
            methods_tg_list.append(method_tg)
            return methods_tg_list
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text.upper()
                if (
                    re.fullmatch(settings.serial_number_regex, message_text)
                    and message_text != "0"
                ):
                    logger.info(
                        f"{self.log_prefix}Got correct new writeoff "
                        "device serial number (forced uppercase): "
                        f"'{message_text}'."
                    )
                    logger.info(
                        f"{self.log_prefix}Working with "
                        f"{WriteoffDeviceDB.__name__} "
                        f"id={writeoff_device.id}."
                    )
                    if writeoff_device.serial_number != message_text:
                        logger.info(
                            f"{self.log_prefix}Writeoff device new "
                            f"serial_number={message_text} "
                            "is different from writeoff device old "
                            f"serial_number={writeoff_device.serial_number}. "
                            "Applying change."
                        )
                        writeoff_device.serial_number = message_text
                        await self.session.flush()
                        self.next_state = StateJS(
                            action=Action.PICK_WRITEOFF_DEVICE_ACTION,
                            writeoff_device_id=writeoff_device_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_writeoff_device(
                                f"{String.SERIAL_NUMBER_WAS_EDITED}. "
                                f"{String.PICK_WRITEOFF_DEVICE_ACTION}."
                            ),
                        )
                    else:
                        logger.info(
                            f"{self.log_prefix}Writeoff device new "
                            f"serial_number={message_text} "
                            "is the same as writeoff device old "
                            f"serial_number={writeoff_device.serial_number}. "
                            "No change needed."
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_WRITEOFF_DEVICE_ACTION,
                            writeoff_device_id=writeoff_device_id,
                        )
                        methods_tg_list.append(
                            await self._build_pick_writeoff_device(
                                f"{String.SERIAL_NUMBER_REMAINS_THE_SAME}. "
                                f"{String.PICK_WRITEOFF_DEVICE_ACTION}."
                            )
                        )
                else:
                    logger.info(
                        f"{self.log_prefix}Got incorrect new writeoff "
                        "device serial number (forced uppercase): "
                        f"'{message_text}'."
                    )
                    methods_tg_list.append(
                        self._build_new_text_message(
                            f"{String.INCORRECT_SERIAL_NUMBER}. "
                            f"{String.ENTER_NEW_SERIAL_NUMBER}."
                        )
                    )
            else:
                logger.info(
                    f"{self.log_prefix}Didn't get new writeoff device serial number."
                )
                methods_tg_list.append(
                    self._build_new_text_message(
                        f"{String.INCORRECT_SERIAL_NUMBER}. "
                        f"{String.ENTER_NEW_SERIAL_NUMBER}."
                    )
                )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
            logger.info(
                f"{self.log_prefix}Got callback data "
                "instead of new writeoff device serial number."
            )
            methods_tg_list.append(
                self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
            )
            methods_tg_list.append(
                self._build_new_text_message(
                    f"{String.GOT_DATA_NOT_SERIAL_NUMBER}. "
                    f"{String.ENTER_NEW_SERIAL_NUMBER}."
                )
            )
        return methods_tg_list

    def _helper_mainmenu_keyboard_rows(self) -> list[list[InlineKeyboardButtonTG]]:
        inline_keyboard_rows = []
        if self.user_db.is_engineer:
            inline_keyboard_rows.append(
                [
                    InlineKeyboardButtonTG(
                        text=String.ADD_TICKET_BTN,
                        callback_data=CallbackData.ADD_TICKET,
                    )
                ],
            )
            inline_keyboard_rows.append(
                [
                    InlineKeyboardButtonTG(
                        text=String.TICKETS_BTN,
                        callback_data=CallbackData.TICKETS,
                    ),
                    InlineKeyboardButtonTG(
                        text=String.WRITEOFF_DEVICES_BTN,
                        callback_data=CallbackData.WRITEOFF_DEVICES,
                    ),
                ],
            )
        if self.user_db.is_manager:
            inline_keyboard_rows.append(
                [
                    InlineKeyboardButtonTG(
                        text=String.FORM_REPORT_BTN,
                        callback_data=CallbackData.FORM_REPORT,
                    )
                ],
            )
            if self.user_db.is_hiring:
                inline_keyboard_rows.append(
                    [
                        InlineKeyboardButtonTG(
                            text=String.DISABLE_HIRING_BTN,
                            callback_data=CallbackData.DISABLE_HIRING,
                        )
                    ],
                )
            else:
                inline_keyboard_rows.append(
                    [
                        InlineKeyboardButtonTG(
                            text=String.ENABLE_HIRING_BTN,
                            callback_data=CallbackData.ENABLE_HIRING,
                        )
                    ],
                )
        return inline_keyboard_rows

    def _build_stateless_mainmenu(
        self, text: str = f"{String.PICK_A_FUNCTION}."
    ) -> SendMessageTG:
        mainmenu_keyboard_rows = self._helper_mainmenu_keyboard_rows()
        if mainmenu_keyboard_rows:
            text = text
            reply_markup = InlineKeyboardMarkupTG(
                inline_keyboard=mainmenu_keyboard_rows
            )
        else:
            text = f"{String.NO_FUNCTIONS_ARE_AVAILABLE}."
            reply_markup = None
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=reply_markup,
        )

    async def _build_pick_tickets(
        self, text: str = f"{String.PICK_TICKETS_ACTION}."
    ) -> SendMessageTG:
        if self.next_state:
            current_state = self.next_state
        elif self.state:
            current_state = self.state
        else:
            logger.error(
                f"{self.log_prefix}As a fallback option, "
                "'self.state' cannot be None if "
                "'self.next_state' is None too."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            return self._build_stateless_mainmenu(
                f"{String.CONFIGURATION_ERROR_DETECTED} "
                "(missing both self.next_state and self.state). "
                f"{String.CONTACT_THE_ADMINISTRATOR}. "
                f"{String.PICK_A_FUNCTION}."
            )
        page_index = current_state.tickets_page
        if page_index is not None and page_index < 0:
            logger.error(
                f"{self.log_prefix}Configuration error: "
                "Current tickets list page has negative index."
            )
            self.next_state = None
            self.user_db.state_json = None
            return self._build_stateless_mainmenu(
                f"{String.CONFIGURATION_ERROR_DETECTED} "
                "(negative tickets page_index). "
                f"{String.CONTACT_THE_ADMINISTRATOR}. "
                f"{String.PICK_A_FUNCTION}."
            )
        lookback_days = settings.tickets_history_lookback_days
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        recent_tickets_result = await self.session.scalars(
            select(TicketDB).where(
                TicketDB.user_id == self.user_db.id,
                TicketDB.created_at >= cutoff_date,
            )
        )
        recent_tickets = recent_tickets_result.all()
        total_recent_tickets = len(recent_tickets)
        tickets_per_page = settings.tickets_per_page
        total_pages = max(
            1,
            (total_recent_tickets + tickets_per_page - 1) // tickets_per_page,
        )
        last_page_index = total_pages - 1
        if page_index is None:
            page_index = 0
        logger.info(
            f"{self.log_prefix}The user is on page {page_index + 1} of {total_pages}."
        )
        logger.debug(
            f"page_index={page_index}, "
            f"last_page_index={last_page_index}, "
            f"total_pages={total_pages}."
        )
        inline_keyboard_rows: list[list[InlineKeyboardButtonTG]] = []
        add_ticket_button_row: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=f"{String.ADD_TICKET_BTN}",
                callback_data=CallbackData.ADD_TICKET,
            ),
        ]
        inline_keyboard_rows.append(add_ticket_button_row)
        existing_tickets_button_rows: list[list[InlineKeyboardButtonTG]] = []
        recent_ticket_index_offset = page_index * settings.tickets_per_page
        sorted_recent_tickets = sorted(
            recent_tickets, key=lambda ticket: ticket.created_at, reverse=True
        )
        current_page_tickets = sorted_recent_tickets[
            recent_ticket_index_offset : recent_ticket_index_offset
            + settings.tickets_per_page
        ]
        current_page_tickets_count = len(current_page_tickets)
        user_timezone = ZoneInfo(self.user_db.timezone)
        months = {
            1: String.JAN.value,
            2: String.FEB.value,
            3: String.MAR.value,
            4: String.APR.value,
            5: String.MAY.value,
            6: String.JUN.value,
            7: String.JUL.value,
            8: String.AUG.value,
            9: String.SEP.value,
            10: String.OCT.value,
            11: String.NOV.value,
            12: String.DEC.value,
        }
        tickets_dict: dict[int, int] = {}
        for index, ticket in enumerate(current_page_tickets):
            closed_status = (
                String.CLOSED_TICKET_ICON
                if ticket.is_closed
                else String.OPEN_TICKET_ICON
            )
            ticket_number = ticket.number
            ticket_created_at_local_timestamp = ticket.created_at.astimezone(
                user_timezone
            )
            day_number = ticket_created_at_local_timestamp.day
            month_number = ticket_created_at_local_timestamp.month
            hh_mm = ticket_created_at_local_timestamp.strftime("%H:%M")
            ticket_button = [
                InlineKeyboardButtonTG(
                    text=(
                        f"{closed_status} "
                        f"{String.NUMBER_SYMBOL} "  # nbsp
                        f"{ticket_number} {String.FROM_X.value} "
                        f"{day_number} {months[month_number]} "
                        f"{hh_mm} >>"
                    ),
                    callback_data=CallbackData[f"TICKET_{index}"],
                ),
            ]
            tickets_dict[index] = ticket.id
            existing_tickets_button_rows.append(ticket_button)
        inline_keyboard_rows.extend(existing_tickets_button_rows)
        prev_next_buttons_row: list[InlineKeyboardButtonTG] = []
        if total_recent_tickets > current_page_tickets_count:
            if page_index > last_page_index:
                page_index = last_page_index
            prev_button = InlineKeyboardButtonTG(
                text=f"{String.PREV_ONES}",
                callback_data=CallbackData.PREV_ONES,
            )
            next_button = InlineKeyboardButtonTG(
                text=f"{String.NEXT_ONES}",
                callback_data=CallbackData.NEXT_ONES,
            )
            if page_index < last_page_index:
                prev_next_buttons_row.append(prev_button)
            if page_index > 0:
                prev_next_buttons_row.append(next_button)
        if prev_next_buttons_row:
            inline_keyboard_rows.append(prev_next_buttons_row)
        return_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=String.DONE_BTN,
            callback_data=CallbackData.RETURN_TO_MAIN_MENU,
        )
        inline_keyboard_rows.append([return_button])
        if self.next_state:
            self.next_state.tickets_page = page_index
            self.next_state.tickets_dict = tickets_dict
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard_rows),
        )

    async def _build_pick_writeoff_devices(
        self, text: str = f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
    ) -> SendMessageTG:
        if self.next_state:
            current_state = self.next_state
        elif self.state:
            current_state = self.state
        else:
            logger.error(
                f"{self.log_prefix}As a fallback option, "
                "'self.state' cannot be None if "
                "'self.next_state' is None too."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            return self._build_stateless_mainmenu(
                f"{String.CONFIGURATION_ERROR_DETECTED} "
                "(missing both self.next_state and self.state). "
                f"{String.CONTACT_THE_ADMINISTRATOR}. "
                f"{String.PICK_A_FUNCTION}."
            )
        page_index = current_state.writeoff_devices_page
        if page_index is not None and page_index < 0:
            logger.error(
                f"{self.log_prefix}Configuration error: "
                "Current writeoff devices list page has negative index."
            )
            self.next_state = None
            self.user_db.state_json = None
            return self._build_stateless_mainmenu(
                f"{String.CONFIGURATION_ERROR_DETECTED} "
                "(negative writeoff devices page_index). "
                f"{String.CONTACT_THE_ADMINISTRATOR}. "
                f"{String.PICK_A_FUNCTION}."
            )
        await self.session.refresh(
            self.user_db,
            attribute_names=["writeoff_devices"],
        )
        total_writeoff_devices = len(self.user_db.writeoff_devices)
        writeoffs_per_page = settings.writeoffs_per_page
        total_pages = max(
            1,
            (total_writeoff_devices + writeoffs_per_page - 1) // writeoffs_per_page,
        )
        last_page_index = total_pages - 1
        if page_index is None:
            page_index = 0
        logger.info(
            f"{self.log_prefix}The user is on page {page_index + 1} of {total_pages}."
        )
        logger.debug(
            f"page_index={page_index}, "
            f"last_page_index={last_page_index}, "
            f"total_pages={total_pages}."
        )
        inline_keyboard_rows: list[list[InlineKeyboardButtonTG]] = []
        add_writeoff_device_button_row: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=f"{String.ADD_WRITEOFF_DEVICE_BTN}",
                callback_data=CallbackData.ADD_WRITEOFF_DEVICE,
            ),
        ]
        inline_keyboard_rows.append(add_writeoff_device_button_row)
        existing_writeoff_devices_button_rows: list[list[InlineKeyboardButtonTG]] = []
        writeoff_device_index_offset = page_index * settings.writeoffs_per_page
        sorted_writeoff_devices = sorted(
            self.user_db.writeoff_devices, key=lambda device: device.id, reverse=True
        )
        current_page_writeoff_devices = sorted_writeoff_devices[
            writeoff_device_index_offset : writeoff_device_index_offset
            + settings.writeoffs_per_page
        ]
        current_page_writeoff_devices_count = len(current_page_writeoff_devices)
        if current_page_writeoff_devices:
            await self.session.execute(
                select(WriteoffDeviceDB)
                .where(
                    WriteoffDeviceDB.id.in_(
                        (device.id for device in current_page_writeoff_devices)
                    )
                )
                .options(selectinload(WriteoffDeviceDB.type)),
                execution_options={"populate_existing": True},
            )
        writeoff_devices_dict: dict[int, int] = {}
        for index, writeoff_device in enumerate(current_page_writeoff_devices):
            writeoff_device_number = (
                total_writeoff_devices - writeoff_device_index_offset - index
            )
            if (
                writeoff_device.type.has_serial_number
                and writeoff_device.serial_number is not None
            ):
                writeoff_device_serial_number_string = (
                    f" {writeoff_device.serial_number}"
                )
            else:
                writeoff_device_serial_number_string = ""
            writeoff_device_icon = "💩"
            device_type_name = String[writeoff_device.type.name.name]
            if writeoff_device.type.is_disposable:
                writeoff_device_is_disposable_check_string = f" {String.DISPOSABLE}"
            else:
                writeoff_device_is_disposable_check_string = " >>"
            writeoff_device_button = [
                InlineKeyboardButtonTG(
                    text=(
                        f"{writeoff_device_number}. "
                        f"{writeoff_device_icon} "
                        f"{device_type_name.value}"
                        f"{writeoff_device_serial_number_string}"
                        f"{writeoff_device_is_disposable_check_string}"
                    ),
                    callback_data=CallbackData[f"DEVICE_{index}"],
                ),
            ]
            writeoff_devices_dict[index] = writeoff_device.id
            existing_writeoff_devices_button_rows.append(writeoff_device_button)
        inline_keyboard_rows.extend(existing_writeoff_devices_button_rows)
        prev_next_buttons_row: list[InlineKeyboardButtonTG] = []
        if total_writeoff_devices > current_page_writeoff_devices_count:
            if page_index > last_page_index:
                page_index = last_page_index
            prev_button = InlineKeyboardButtonTG(
                text=f"{String.PREV_ONES}",
                callback_data=CallbackData.PREV_ONES,
            )
            next_button = InlineKeyboardButtonTG(
                text=f"{String.NEXT_ONES}",
                callback_data=CallbackData.NEXT_ONES,
            )
            if page_index < last_page_index:
                prev_next_buttons_row.append(prev_button)
            if page_index > 0:
                prev_next_buttons_row.append(next_button)
        if prev_next_buttons_row:
            inline_keyboard_rows.append(prev_next_buttons_row)
        return_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=String.DONE_BTN,
            callback_data=CallbackData.RETURN_TO_MAIN_MENU,
        )
        inline_keyboard_rows.append([return_button])
        if self.next_state:
            self.next_state.writeoff_devices_page = page_index
            self.next_state.writeoff_devices_dict = writeoff_devices_dict
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard_rows),
        )

    def _build_edit_to_callback_button_text(
        self, prefix_text: str = ""
    ) -> EditMessageTextTG:
        """Modifies callback message text to the string provided."""
        if not isinstance(self.update_tg, CallbackQueryUpdateTG):
            raise TypeError(
                "This method only works with "
                f"{CallbackQueryUpdateTG.__name__} update type only."
            )
        if self.update_tg.callback_query.message.reply_markup is None:
            error_msg = f"{self.log_prefix}This method only works with inline keyboard attached."
            logger.error(error_msg)
            raise ValueError(error_msg)
        chat_id = self.update_tg.callback_query.message.chat.id
        message_id = self.update_tg.callback_query.message.message_id
        button_text = self._get_callback_button_text()
        logger.info(
            f"{self.log_prefix}Editing message id={message_id} text "
            f"to button text '{button_text}'."
        )
        method_tg = EditMessageTextTG(
            chat_id=chat_id,
            message_id=message_id,
            text=f"{prefix_text}{button_text}",
        )
        return method_tg

    def _build_edit_to_text_message(
        self, text: str, html_mode: bool = False
    ) -> EditMessageTextTG:
        """Modifies callback message text to the string provided."""
        if not isinstance(self.update_tg, CallbackQueryUpdateTG):
            raise TypeError(
                "This method works with "
                f"{CallbackQueryUpdateTG.__name__} update type only."
            )
        chat_id = self.update_tg.callback_query.message.chat.id
        message_id = self.update_tg.callback_query.message.message_id
        # old_text = self.update_tg.callback_query.message.text
        logger.info(
            f"{self.log_prefix}Editing message id={message_id} text to '{text}'."
        )
        method_tg = EditMessageTextTG(
            chat_id=chat_id,
            message_id=message_id,
            # text=f"<s>{old_text}</s>\n\n{String.YOU_HAVE_CHOSEN}: {string}.",
            text=text,
            # parse_mode="HTML",
        )
        if html_mode:
            method_tg.parse_mode = "HTML"
        return method_tg

    def _build_new_text_message(self, text: str) -> SendMessageTG:
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
        )

    async def _build_pick_device_type(
        self, text: str = f"{String.PICK_DEVICE_TYPE}."
    ) -> SendMessageTG:
        device_types = await self.session.scalars(
            select(DeviceTypeDB).where(DeviceTypeDB.is_active == True)  # noqa: E712
        )
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = []
        for device_type in device_types:
            try:
                button_text = String[device_type.name.name]
                button_callback_data = CallbackData[device_type.name.name]
                inline_keyboard.append(
                    [
                        InlineKeyboardButtonTG(
                            text=button_text,
                            callback_data=button_callback_data,
                        )
                    ]
                )
            except KeyError as e:
                missing_member_value = device_type.name.name
                logger.error(
                    f"{self.log_prefix}Configuration error: Missing "
                    f"{String.__name__} or {CallbackData.__name__} "
                    f"enum member for {DeviceTypeName.__name__} "
                    f"'{missing_member_value}'. Original error: {e}"
                )
                logger.info(f"{self.log_prefix}Going back to the main menu.")
                self.next_state = None
                self.user_db.state_json = None
                method_tg = self._build_stateless_mainmenu(
                    f"{String.CONFIGURATION_ERROR_DETECTED} (missing "
                    f"{String.__name__}.{missing_member_value} or "
                    f"{CallbackData.__name__}.{missing_member_value}). "
                    f"{String.CONTACT_THE_ADMINISTRATOR}. "
                    f"{String.PICK_A_FUNCTION}."
                )
                return method_tg
        if not inline_keyboard:
            logger.error(
                f"{self.log_prefix}Configuration error: Not a single eligible "
                f"(active) {DeviceTypeDB.__name__} was found in the "
                f"database. Cannot build {DeviceTypeDB.__name__} "
                "selection keyboard. Investigate the logic."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.CONFIGURATION_ERROR_DETECTED}. "
                f"{String.NO_ACTIVE_DEVICE_TYPE_AVAILABLE}. "
                f"{String.CONTACT_THE_ADMINISTRATOR}. "
                f"{String.PICK_A_FUNCTION}."
            )
        else:
            method_tg = SendMessageTG(
                chat_id=self.user_db.telegram_uid,
                text=text,
                reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard),
            )
        return method_tg

    def _build_pick_confirm_delete_ticket_message(
        self, text: str = f"{String.CONFIRM_TICKET_DELETION}."
    ) -> SendMessageTG:
        method_tg = SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(
                inline_keyboard=[
                    [
                        InlineKeyboardButtonTG(
                            text=String.CONFIRM_DELETE_TICKET,
                            callback_data=CallbackData.CONFIRM_DELETE_TICKET,
                        ),
                        InlineKeyboardButtonTG(
                            text=String.CHANGED_MY_MIND,
                            callback_data=CallbackData.CHANGED_MY_MIND,
                        ),
                    ],
                ]
            ),
        )
        return method_tg

    async def _build_pick_writeoff_device_type(
        self, text: str = f"{String.PICK_WRITEOFF_DEVICE_TYPE}."
    ) -> SendMessageTG:
        device_types = await self.session.scalars(
            select(DeviceTypeDB).where(DeviceTypeDB.is_disposable == False)  # noqa: E712
        )
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = []
        for device_type in device_types:
            try:
                button_text = String[device_type.name.name]
                button_callback_data = CallbackData[device_type.name.name]
                inline_keyboard.append(
                    [
                        InlineKeyboardButtonTG(
                            text=button_text,
                            callback_data=button_callback_data,
                        )
                    ]
                )
            except KeyError as e:
                missing_member_value = device_type.name.name
                logger.error(
                    f"{self.log_prefix}Configuration error: Missing "
                    f"{String.__name__} or {CallbackData.__name__} "
                    f"enum member for {DeviceTypeName.__name__} "
                    f"'{missing_member_value}'. Original error: {e}"
                )
                logger.info(f"{self.log_prefix}Going back to the main menu.")
                self.next_state = None
                self.user_db.state_json = None
                method_tg = self._build_stateless_mainmenu(
                    f"{String.CONFIGURATION_ERROR_DETECTED} (missing "
                    f"{String.__name__}.{missing_member_value} or "
                    f"{CallbackData.__name__}.{missing_member_value}). "
                    f"{String.CONTACT_THE_ADMINISTRATOR}. "
                    f"{String.PICK_A_FUNCTION}."
                )
                return method_tg
        if not inline_keyboard:
            logger.warning(
                f"{self.log_prefix}Warning: Not a single eligible "
                f"(not disposable) {DeviceTypeDB.__name__} was found in the "
                f"database. Cannot build {DeviceTypeDB.__name__} "
                "selection keyboard."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu(
                f"{String.NO_WRITEOFF_DEVICE_TYPE_AVAILABLE}. "
                f"{String.CONTACT_THE_ADMINISTRATOR}. "
                f"{String.PICK_A_FUNCTION}."
            )
        else:
            method_tg = SendMessageTG(
                chat_id=self.user_db.telegram_uid,
                text=text,
                reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard),
            )
        return method_tg

    def _build_pick_install_or_return_message(
        self, text: str = f"{String.PICK_INSTALL_OR_RETURN}."
    ) -> SendMessageTG:
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(
                inline_keyboard=[
                    [
                        InlineKeyboardButtonTG(
                            text=String.INSTALL_DEVICE_BTN,
                            callback_data=CallbackData.INSTALL_DEVICE,
                        ),
                        InlineKeyboardButtonTG(
                            text=String.RETURN_DEVICE_BTN,
                            callback_data=CallbackData.RETURN_DEVICE,
                        ),
                    ],
                ]
            ),
        )

    async def _build_pick_ticket_action(
        self, text: str = f"{String.PICK_TICKET_ACTION}."
    ) -> SendMessageTG:
        assert self.state is not None
        if self.next_state:
            current_state = self.next_state
        else:
            current_state = self.state
        assert current_state.ticket_id is not None
        current_ticket_id = current_state.ticket_id
        current_ticket = await self.session.scalar(
            select(TicketDB)
            .where(TicketDB.id == current_ticket_id)
            .options(
                selectinload(TicketDB.contract),
                selectinload(TicketDB.devices).selectinload(DeviceDB.type),
            )
        )
        if not current_ticket:
            logger.warning(
                f"{self.log_prefix}Current ticket "
                "was not found in the database under "
                f"id={current_ticket_id}."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            return self._build_stateless_mainmenu(
                f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
        ticket_number = current_ticket.number
        contract_text = (
            f"{String.CONTRACT_NUMBER_BTN} {current_ticket.contract.number}"
            if current_ticket.contract
            else f"{String.ATTENTION_ICON} {String.ENTER_CONTRACT_NUMBER}"
        )
        devices_list = current_ticket.devices
        inline_keyboard_rows: list[list[InlineKeyboardButtonTG]] = []
        ticket_number_button: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=f"{String.TICKET_NUMBER_BTN} {ticket_number} {String.EDIT}",
                callback_data=CallbackData.EDIT_TICKET_NUMBER,
            ),
        ]
        contract_number_button: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=f"{contract_text} {String.EDIT}",
                callback_data=CallbackData.EDIT_CONTRACT_NUMBER,
            ),
        ]
        device_button_rows: list[list[InlineKeyboardButtonTG]] = []
        for index, device in enumerate(devices_list):
            device_number = index + 1
            device_icon = (
                String.INSTALL_DEVICE_ICON
                if device.removal is False
                else String.RETURN_DEVICE_ICON
                if device.removal is True
                else String.UNSET_DEVICE_ICON
            )
            if not isinstance(device.type.name, DeviceTypeName):
                error_msg = (
                    f"{self.log_prefix}Configuration error: "
                    "device.type.name is not "
                    f"{DeviceTypeName.__name__}."
                )
                logger.error(error_msg)
                raise AssertionError(error_msg)
            device_type_name = String[device.type.name.name]
            device_serial_number = device.serial_number
            if device_serial_number is not None:
                device_button_text = (
                    f"{device_number}. "
                    f"{device_icon} {device_type_name} "
                    f"{device_serial_number} >>"
                )
            else:
                device_button_text = (
                    f"{device_number}. {device_icon} {device_type_name} >>"
                )
            device_button_rows.append(
                [
                    InlineKeyboardButtonTG(
                        text=device_button_text,
                        callback_data=CallbackData[f"DEVICE_{index}"],
                    )
                ]
            )
        add_device_button: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=String.ADD_DEVICE_BTN,
                callback_data=CallbackData.ADD_DEVICE,
            ),
        ]
        reopen_ticket_button: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=f"{String.OPEN_TICKET_ICON} {String.REOPEN_TICKET_BTN}",
                callback_data=CallbackData.REOPEN_TICKET,
            ),
        ]
        close_ticket_button: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=f"{String.CLOSED_TICKET_ICON} {String.CLOSE_TICKET_BTN}",
                callback_data=CallbackData.CLOSE_TICKET,
            ),
        ]
        delete_ticket_button: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=f"{String.TRASHCAN_ICON} {String.DELETE_TICKET_BTN}",
                callback_data=CallbackData.DELETE_TICKET,
            ),
        ]
        return_buttons_row: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=String.RETURN_TO_TICKETS,
                callback_data=CallbackData.RETURN_TO_TICKETS,
            ),
            InlineKeyboardButtonTG(
                text=String.RETURN_TO_MAIN_MENU,
                callback_data=CallbackData.RETURN_TO_MAIN_MENU,
            ),
        ]
        inline_keyboard_rows.append(ticket_number_button)
        inline_keyboard_rows.append(contract_number_button)
        inline_keyboard_rows.extend(device_button_rows)
        total_devices = len(devices_list)
        if total_devices < settings.devices_per_ticket:
            inline_keyboard_rows.append(add_device_button)
        if current_ticket.is_closed:
            inline_keyboard_rows.append(reopen_ticket_button)
        elif (
            total_devices > 0
            and current_ticket.contract
            and current_ticket.contract.number
        ):
            inline_keyboard_rows.append(close_ticket_button)
        inline_keyboard_rows.append(delete_ticket_button)
        inline_keyboard_rows.append(return_buttons_row)
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard_rows),
        )

    async def _build_pick_device_action_message(
        self, text: str = f"{String.PICK_DEVICE_ACTION}."
    ) -> SendMessageTG:
        assert self.state is not None
        if self.next_state:
            current_state = self.next_state
        else:  # self.state is guaranteed by the parent function.
            current_state = self.state
        current_ticket_id = current_state.ticket_id
        current_ticket = await self.session.scalar(
            select(TicketDB)
            .where(TicketDB.id == current_ticket_id)
            .options(
                selectinload(TicketDB.devices).selectinload(DeviceDB.type),
            )
        )
        if not current_ticket:
            logger.warning(
                f"{self.log_prefix}Current ticket "
                "was not found in the database under "
                f"id={current_ticket_id}."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            return self._build_stateless_mainmenu(
                f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
        device_index = current_state.ticket_device_index
        devices_list = current_ticket.devices
        total_devices = len(devices_list)
        if device_index is None or not (0 <= device_index < total_devices):
            logger.error(
                f"{self.log_prefix}Error: "
                f"device_index={device_index} and "
                f"total_devices={total_devices}. "
                "Expected: "
                "0 <= device_index < total_devices."
            )
            logger.info(f"{self.log_prefix}Going back to the main menu.")
            self.next_state = None
            self.user_db.state_json = None
            return self._build_stateless_mainmenu(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(incorrect device_index). "
                f"{String.PICK_A_FUNCTION}."
            )
        inline_keyboard_rows: list[list[InlineKeyboardButtonTG]] = []
        device = devices_list[device_index]
        device_type_name = String[device.type.name.name]
        device_serial_number_text = (
            device.serial_number
            if device.serial_number is not None
            else f"{String.ATTENTION_ICON} {String.ENTER_SERIAL_NUMBER}"
        )
        device_type_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=f"{device_type_name} {String.EDIT}",
            callback_data=CallbackData.EDIT_DEVICE_TYPE,
        )
        if device.removal is True:
            device_action_text = f"{String.RETURN_DEVICE_BTN} {String.EDIT}"
            device_action_data = CallbackData.RETURN_DEVICE
        elif device.removal is False:
            device_action_text = f"{String.INSTALL_DEVICE_BTN} {String.EDIT}"
            device_action_data = CallbackData.INSTALL_DEVICE
        else:
            logger.error(
                f"{self.log_prefix}Configuration error: "
                f"{DeviceDB.__name__} type='{device.type.name.name}' "
                f"id={device.id} is missing 'removal' bool status. "
                "Investigate the logic."
            )
            device_action_text = f"{String.INSTALL_RETURN_BTN} {String.EDIT}"
            device_action_data = CallbackData.INSTALL_RETURN
        device_action_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=device_action_text,
            callback_data=device_action_data,
        )
        serial_number_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=f"{device_serial_number_text} {String.EDIT}",
            callback_data=CallbackData.EDIT_DEVICE_SERIAL_NUMBER,
        )
        return_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=String.RETURN_BTN,
            callback_data=CallbackData.EDIT_TICKET,
        )
        delete_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=String.DELETE_DEVICE_FROM_TICKET,
            callback_data=CallbackData.DELETE_DEVICE,
        )
        inline_keyboard_rows.append([device_type_button])
        if not device.type.is_disposable:
            inline_keyboard_rows.append([device_action_button])
        if device.type.has_serial_number:
            inline_keyboard_rows.append([serial_number_button])
        if (
            device.type.has_serial_number
            and device.serial_number is not None
            or not device.type.has_serial_number
        ):
            inline_keyboard_rows.append([return_button])
        inline_keyboard_rows.append([delete_button])
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard_rows),
        )

    async def _build_pick_writeoff_device(
        self, text: str = f"{String.PICK_WRITEOFF_DEVICE_ACTION}."
    ) -> SendMessageTG:
        assert self.state is not None
        if self.next_state:
            current_state = self.next_state
        else:  # self.state is guaranteed by the parent function.
            current_state = self.state
        writeoff_device_id = current_state.writeoff_device_id
        if not writeoff_device_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any writeoff device."
            )
            logger.info(f"{self.log_prefix} Going back to the writeoff devices menu.")
            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
            return await self._build_pick_writeoff_devices(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing writeoff_device_id). "
                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
            )
        writeoff_device = await self.session.scalar(
            select(WriteoffDeviceDB)
            .where(WriteoffDeviceDB.id == writeoff_device_id)
            .options(selectinload(WriteoffDeviceDB.type))
        )
        if not writeoff_device:
            logger.error(
                f"{self.log_prefix}Current writeoff device "
                "was not found in the database under "
                f"id={writeoff_device_id}. Cannot edit it."
            )
            logger.info(f"{self.log_prefix} Going back to the writeoff devices menu.")
            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
            return await self._build_pick_writeoff_devices(
                f"{String.WRITEOFF_DEVICE_WAS_NOT_FOUND}. "
                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
            )
        inline_keyboard_rows: list[list[InlineKeyboardButtonTG]] = []
        device_type_name = String[writeoff_device.type.name.name]
        if writeoff_device.type.is_disposable:
            logger.error(
                f"{self.log_prefix}Configuration error: "
                f"{DeviceTypeDB.__name__} '{device_type_name}' "
                "is not an eligible writeoff device type "
                "as it is disposable. You shouldn't be seeing "
                "this ever."
            )
            logger.info(f"{self.log_prefix}Going back to the writeoff devices menu.")
            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
            method_tg = await self._build_pick_writeoff_devices(
                f"{String.WRITEOFF_DEVICE_IS_INCORRECT}. "
                f"{String.CONTACT_THE_ADMINISTRATOR}. "
                f"{String.PICK_WRITEOFF_DEVICES_ACTION}."
            )
        else:
            device_serial_number_text = (
                writeoff_device.serial_number
                if writeoff_device.serial_number is not None
                else String.ENTER_SERIAL_NUMBER.value
            )
            device_type_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
                text=f"{device_type_name} {String.EDIT}",
                callback_data=CallbackData.EDIT_WRITEOFF_DEVICE_TYPE,
            )
            serial_number_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
                text=f"{device_serial_number_text} {String.EDIT}",
                callback_data=CallbackData.EDIT_WRITEOFF_DEVICE_SERIAL_NUMBER,
            )
            return_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
                text=String.RETURN_BTN,
                callback_data=CallbackData.WRITEOFF_DEVICES,
            )
            delete_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
                text=String.DELETE_DEVICE_FROM_WRITEOFF,
                callback_data=CallbackData.DELETE_WRITEOFF_DEVICE,
            )
            inline_keyboard_rows.append([device_type_button])
            if writeoff_device.type.has_serial_number:
                inline_keyboard_rows.append([serial_number_button])
            if (
                writeoff_device.type.has_serial_number
                and writeoff_device.serial_number is not None
                or not writeoff_device.type.has_serial_number
            ):
                inline_keyboard_rows.append([return_button])
            inline_keyboard_rows.append([delete_button])
            method_tg = SendMessageTG(
                chat_id=self.user_db.telegram_uid,
                text=text,
                reply_markup=InlineKeyboardMarkupTG(
                    inline_keyboard=inline_keyboard_rows
                ),
            )
        return method_tg

    def _get_device_type_name_from_callback_data(
        self, callback_data: CallbackData
    ) -> DeviceTypeName:
        """
        Validates that a CallbackData member name exists in
        DeviceTypeName and returns the corresponding
        DeviceTypeName member.

        Raises:
            ValueError: If the name does not exist in DeviceTypeName,
                        allowing the calling function to handle it
                        as a generic "invalid data" error.
        """
        try:
            device_type_name = DeviceTypeName[callback_data.name]
            logger.info(
                f"{self.log_prefix}{CallbackData.__name__} "
                f"'{callback_data.value}' "
                f"matches {DeviceTypeName.__name__} "
                f"'{device_type_name.name}'."
            )
            return device_type_name
        except KeyError:
            logger.info(
                f"{self.log_prefix}{CallbackData.__name__} "
                f"'{callback_data.value}' "
                "doesn't match any "
                f"{DeviceTypeName.__name__}."
            )
            raise ValueError(
                "Callback data does not correspond to a device type"
            )  # from None

    def _get_callback_data_ending_as_integer(self, callback_data: CallbackData) -> int:
        assert isinstance(callback_data.value, str)
        pattern = r"(\d+)$"
        match = re.search(pattern, callback_data.value)
        if not match:
            error_msg = (
                f"{self.log_prefix}"
                f"{CallbackData.__name__} "
                f"'{callback_data.value}' "
                "doesn't end with an integer."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        number_as_string = match.group(1)
        return int(number_as_string)

    def _get_callback_button_text(self) -> str:
        """Returns text of a button with matching callback data."""
        if not isinstance(self.update_tg, CallbackQueryUpdateTG):
            raise TypeError(
                "This method only works with "
                f"{CallbackQueryUpdateTG.__name__} update type only."
            )
        if self.update_tg.callback_query.message.reply_markup is None:
            error_msg = (
                f"{self.log_prefix}This method only works with "
                "inline keyboard attached."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        callback_data = self.update_tg.callback_query.data
        inline_keyboard = (
            self.update_tg.callback_query.message.reply_markup.inline_keyboard
        )
        button_text: str = ""
        for row in inline_keyboard:
            for button in row:
                if button.callback_data == callback_data:
                    button_text = button.text
                    logger.info(
                        f"{self.log_prefix}Button text '{button_text}' "
                        f"found for callback data '{callback_data}'."
                    )
                    break
            else:
                continue
            break
        return button_text

    # async def _handle_pick_confirm_close_ticket(self) -> list[MethodTG]:
    #     assert self.state is not None
    #     logger.info(f"{self.log_prefix}Awaiting close ticket confirmation.")
    #     methods_tg_list: list[MethodTG] = []
    #     current_ticket_id = self.state.ticket_id
    #     if not current_ticket_id:  # Both None and 0 are covered this way.
    #         logger.error(
    #             f"{self.log_prefix}{self.user_db.full_name} "
    #             "is not working on any ticket."
    #         )
    #         logger.info(f"{self.log_prefix}Going back to the main menu.")
    #         self.next_state = None
    #         self.user_db.state_json = None
    #         method_tg = self._build_stateless_mainmenu(
    #             f"{String.INCONSISTENT_STATE_DETECTED} "
    #             "(missing current_ticket_id). "
    #             f"{String.PICK_A_FUNCTION}."
    #         )
    #         methods_tg_list.append(method_tg)
    #         return methods_tg_list
    #     current_ticket = await self.session.scalar(
    #         select(TicketDB)
    #         .where(TicketDB.id == current_ticket_id)
    #         .options(selectinload(TicketDB.devices))
    #     )
    #     if not current_ticket:
    #         logger.warning(
    #             f"{self.log_prefix}Current ticket "
    #             "was not found in the database under "
    #             f"id={current_ticket_id}."
    #         )
    #         logger.info(f"{self.log_prefix}Going back to the main menu.")
    #         self.next_state = None
    #         self.user_db.state_json = None
    #         method_tg = self._build_stateless_mainmenu(
    #             f"{String.TICKET_WAS_NOT_FOUND}. {String.PICK_A_FUNCTION}."
    #         )
    #         methods_tg_list.append(method_tg)
    #         return methods_tg_list
    #     if isinstance(self.update_tg, CallbackQueryUpdateTG):
    #         expected_callback_data = [
    #             CallbackData.CONFIRM_CLOSE_TICKET_BTN,
    #             CallbackData.CHANGED_MY_MIND_BTN,
    #         ]
    #         raw_data = self.update_tg.callback_query.data
    #         try:
    #             received_callback_data = CallbackData(raw_data)
    #             if received_callback_data in expected_callback_data:
    #                 logger.info(
    #                     f"{self.log_prefix}Got expected "
    #                     f"{CallbackData.__name__} "
    #                     f"'{received_callback_data.value}'."
    #                 )
    #                 if received_callback_data == CallbackData.CONFIRM_CLOSE_TICKET_BTN:
    #                     methods_tg_list.append(
    #                         self._build_edit_to_callback_button_text()
    #                     )
    #                     ticket_number = current_ticket.number
    #                     total_devices = len(current_ticket.devices)
    #                     if total_devices == 1:
    #                         device_string = String.X_DEVICE
    #                     else:
    #                         device_string = String.X_DEVICES
    #                     ticket_closed = await self.close_ticket()
    #                     if ticket_closed:
    #                         self.next_state = None
    #                         self.user_db.state_json = None
    #                         methods_tg_list.append(
    #                             self._build_new_text_message(
    #                                 f"{String.TICKET_NUMBER_BTN} "  # nbsp
    #                                 f"{ticket_number} "
    #                                 f"{String.WITH_X} "
    #                                 f"{total_devices} "
    #                                 f"{device_string}."
    #                             )
    #                         )
    #                         methods_tg_list.append(
    #                             self._build_stateless_mainmenu(
    #                                 f"{String.YOU_CLOSED_TICKET}. {String.PICK_A_FUNCTION}."
    #                             )
    #                         )
    #                     else:
    #                         self.next_state = StateJS(
    #                             action=Action.PICK_TICKET_ACTION,
    #                             ticket_id=current_ticket_id,
    #                         )
    #                         methods_tg_list.append(
    #                             await self._build_pick_ticket_action(
    #                                 f"{String.TICKET_CLOSE_FAILED}. "
    #                                 f"{String.PICK_TICKET_ACTION}."
    #                             )
    #                         )
    #                 elif received_callback_data == CallbackData.CHANGED_MY_MIND_BTN:
    #                     methods_tg_list.append(
    #                         self._build_edit_to_callback_button_text()
    #                     )
    #                     self.next_state = StateJS(
    #                         action=Action.PICK_TICKET_ACTION,
    #                         ticket_id=current_ticket_id,
    #                     )
    #                     methods_tg_list.append(
    #                         await self._build_pick_ticket_action(
    #                             f"{String.PICK_TICKET_ACTION}."
    #                         )
    #                     )
    #             else:
    #                 logger.info(
    #                     f"{self.log_prefix}Got unexpected "
    #                     f"{CallbackData.__name__} "
    #                     f"'{received_callback_data.value}'."
    #                 )
    #                 raise ValueError
    #         except ValueError:
    #             logger.info(
    #                 f"{self.log_prefix}Got invalid callback data "
    #                 f"'{raw_data}' for current ticket close "
    #                 "confirmation menu selection."
    #             )
    #             methods_tg_list.append(
    #                 self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
    #             )
    #             methods_tg_list.append(
    #                 self._build_pick_confirm_close_ticket_message(
    #                     f"{String.GOT_UNEXPECTED_DATA}. "
    #                     f"{String.CONFIRM_YOU_WANT_TO_CLOSE_TICKET}"
    #                 )
    #             )
    #     elif isinstance(self.update_tg, MessageUpdateTG):
    #         logger.info(f"{self.log_prefix}Got message instead of callback data.")
    #         methods_tg_list.append(
    #             self._build_pick_confirm_close_ticket_message(
    #                 f"{String.CLOSE_TICKET_ACTION_WAS_NOT_PICKED}. "
    #                 f"{String.CONFIRM_YOU_WANT_TO_CLOSE_TICKET}"
    #             )
    #         )
    #     return methods_tg_list

    # async def _handle_pick_confirm_quit_ticket_without_saving(self) -> list[MethodTG]:
    #     assert self.state is not None
    #     logger.info(
    #         f"{self.log_prefix}Awaiting quit ticket without saving confirmation."
    #     )
    #     current_ticket = self.user_db.current_ticket
    #     if current_ticket is None:
    #         error_msg = (
    #             f"{self.log_prefix}{self.user_db.full_name} "
    #             "is not working on any ticket."
    #         )
    #         logger.error(error_msg)
    #         raise ValueError(error_msg)
    #     methods_tg_list: list[MethodTG] = []
    #     if isinstance(self.update_tg, CallbackQueryUpdateTG):
    #         expected_callback_data = [
    #             CallbackData.CONFIRM_QUIT_BTN,
    #             CallbackData.CHANGED_MY_MIND_BTN,
    #         ]
    #         raw_data = self.update_tg.callback_query.data
    #         try:
    #             received_callback_data = CallbackData(raw_data)
    #             if received_callback_data in expected_callback_data:
    #                 logger.info(
    #                     f"{self.log_prefix}Got expected "
    #                     f"{CallbackData.__name__} "
    #                     f"'{received_callback_data.value}'."
    #                 )
    #                 if received_callback_data == CallbackData.CONFIRM_QUIT_BTN:
    #                     methods_tg_list.append(
    #                         self._build_edit_to_callback_button_text()
    #                     )
    #                     await self.drop_current_ticket()
    #                     self.next_state = None
    #                     self.user_db.state_json = None
    #                     methods_tg_list.append(
    #                         self._build_stateless_mainmenu(
    #                             f"{String.YOU_QUIT_WITHOUT_SAVING}. {String.PICK_A_FUNCTION}."
    #                         )
    #                     )
    #                 elif received_callback_data == CallbackData.CHANGED_MY_MIND_BTN:
    #                     methods_tg_list.append(
    #                         self._build_edit_to_callback_button_text()
    #                     )
    #                     self.next_state = StateJS(action=Action.PICK_TICKET_ACTION)
    #                     methods_tg_list.append(
    #                         await self._build_pick_ticket_action(
    #                             f"{String.PICK_TICKET_ACTION}."
    #                         )
    #                     )
    #             else:
    #                 logger.info(
    #                     f"{self.log_prefix}Got unexpected "
    #                     f"{CallbackData.__name__} "
    #                     f"'{received_callback_data.value}'."
    #                 )
    #                 raise ValueError
    #         except ValueError:
    #             logger.info(
    #                 f"{self.log_prefix}Got invalid callback data "
    #                 f"'{raw_data}' for quit ticket without saving "
    #                 "confirmation menu selection."
    #             )
    #             methods_tg_list.append(
    #                 self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
    #             )
    #             methods_tg_list.append(
    #                 self._build_pick_confirm_quit_without_saving(
    #                     f"{String.GOT_UNEXPECTED_DATA}. "
    #                     f"{String.ARE_YOU_SURE_YOU_WANT_TO_QUIT_WITHOUT_SAVING}?"
    #                 )
    #             )
    #     elif isinstance(self.update_tg, MessageUpdateTG):
    #         logger.info(f"{self.log_prefix}Got message instead of callback data.")
    #         methods_tg_list.append(
    #             self._build_pick_confirm_quit_without_saving(
    #                 f"{String.QUIT_WITHOUT_SAVING_ACTION_WAS_NOT_PICKED}. "
    #                 f"{String.ARE_YOU_SURE_YOU_WANT_TO_QUIT_WITHOUT_SAVING}?"
    #             )
    #         )
    #     return methods_tg_list

    # async def close_ticket(self) -> bool:
    #     if self.state is None:
    #         error_msg = f"{self.log_prefix}'self.state' cannot be None at this point."
    #         logger.error(error_msg)
    #         raise ValueError(error_msg)
    #     current_ticket = self.user_db.current_ticket
    #     if current_ticket is None:
    #         error_msg = (
    #             f"{self.log_prefix}{self.user_db.full_name} "
    #             "is not working on any ticket."
    #         )
    #         logger.error(error_msg)
    #         raise ValueError(error_msg)
    #     if current_ticket.contract is None:
    #         error_msg = (
    #             f"{self.log_prefix}Cannot close ticket "
    #             f"number={current_ticket.number} "
    #             f"id={current_ticket.id}: contract is missing."
    #         )
    #         logger.error(error_msg)
    #         raise ValueError(error_msg)
    #     devices_list = current_ticket.devices
    #     if not devices_list:
    #         error_msg = (
    #             f"{self.log_prefix}Configuration error: "
    #             "Attempting to close a ticket with no devices. "
    #             "You shouldn't see this."
    #         )
    #         logger.error(error_msg)
    #         raise ValueError(error_msg)
    #     for device in devices_list:
    #         try:
    #             log_prefix_val = f"{self.log_prefix}Ticket "
    #             f"number={current_ticket.number} "
    #             f"id={current_ticket.id}: Device validation: "
    #             if device.type.is_active is False:
    #                 raise ValueError(
    #                     f"{log_prefix_val}{DeviceTypeDB.__name__} "
    #                     f"'{device.type.name.name}' is inactive "
    #                     "but was assigned to device."
    #                 )
    #             if device.removal is None:
    #                 raise ValueError(
    #                     f"{log_prefix_val}Missing 'removal' flag "
    #                     "used for identifying install from return "
    #                     "action."
    #                 )
    #             if device.type.is_disposable and device.removal:
    #                 raise ValueError(
    #                     f"{log_prefix_val}{DeviceTypeDB.__name__} "
    #                     f"'{device.type.name.name}' is "
    #                     "disposable, but device flag 'removal' is "
    #                     f"{device.removal} (expected False)."
    #                 )
    #             if device.type.has_serial_number:
    #                 if device.serial_number is None:
    #                     raise ValueError(
    #                         f"{log_prefix_val}{DeviceTypeDB.__name__} "
    #                         f"'{device.type.name.name}' requires a "
    #                         "serial number, but device is missing it."
    #                     )
    #             else:
    #                 if device.serial_number is not None:
    #                     raise ValueError(
    #                         f"{log_prefix_val}{DeviceTypeDB.__name__} "
    #                         f"'{device.type.name.name}' does not "
    #                         "use a serial number, but one is provided "
    #                         f"('{device.serial_number}')."
    #                     )
    #         except ValueError as e:
    #             logger.error(str(e))
    #             return False
    #     for device in devices_list:
    #         if device.is_draft is True:
    #             device.is_draft = False
    #     ticket_id = current_ticket.id
    #     ticket_number = current_ticket.number
    #     if current_ticket.is_closed is True:
    #         current_ticket.is_closed = False
    #     self.user_db.current_ticket = None
    #     logger.info(
    #         f"{self.log_prefix}Successfully closed and saved "
    #         f"ticket number={ticket_number} id={ticket_id} "
    #         f"with {len(devices_list)} devices."
    #     )
    #     return True

    # async def drop_current_ticket(self) -> bool:
    #     if self.state is None:
    #         error_msg = f"{self.log_prefix}'self.state' cannot be None at this point."
    #         logger.error(error_msg)
    #         raise ValueError(error_msg)
    #     if self.user_db.current_ticket is None:
    #         error_msg = (
    #             f"{self.log_prefix}'user_db.current_ticket' "
    #             "cannot be None at this point."
    #         )
    #         logger.error(error_msg)
    #         raise ValueError(error_msg)
    #     current_ticket = self.user_db.current_ticket
    #     current_ticket_id = self.user_db.current_ticket.id
    #     current_ticket_number = self.user_db.current_ticket.number
    #     current_contract = current_ticket.contract
    #     for device in current_ticket.devices.copy():
    #         if device.is_draft:
    #             logger.info(
    #                 f"{self.log_prefix}Marking draft device "
    #                 f"type='{device.type.name.name}' "
    #                 f"id={device.id} for deletion. "
    #                 "Associated with ticket "
    #                 f"number={current_ticket_number} "
    #                 f"id={current_ticket_id}."
    #             )
    #             await self.session.delete(device)
    #     if current_ticket.is_closed:
    #         logger.info(
    #             f"{self.log_prefix}Marking draft ticket "
    #             f"number={current_ticket_number} "
    #             f"id={current_ticket_id} for deletion."
    #         )
    #         await self.session.delete(current_ticket)
    #     else:
    #         logger.info(
    #             f"{self.log_prefix}Unlocking ticket "
    #             f"number={current_ticket_number} "
    #             f"id={current_ticket_id}. "
    #             "Reverting draft additions/changes."
    #         )
    #         current_ticket.locked_by_user_id = None
    #     if current_contract is not None:
    #         current_contract_id = current_contract.id
    #         current_contract_number = current_contract.number
    #         await self.session.flush()
    #         await self.session.refresh(current_contract, ["tickets"])
    #         if not current_contract.tickets:
    #             logger.info(
    #                 f"{self.log_prefix}Contract "
    #                 f"number={current_contract_number} "
    #                 f"id={current_contract_id} was associated "
    #                 "only with the current ticket "
    #                 f"number={current_ticket_number} "
    #                 f"id={current_ticket_id} being deleted. "
    #                 "Marking contract for deletion."
    #             )
    #             await self.session.delete(current_contract)
    #         else:
    #             logger.info(
    #                 f"{self.log_prefix}Contract "
    #                 f"number={current_contract_number} "
    #                 f"id={current_contract_id} is still "
    #                 "associated with other ticket IDs: "
    #                 f"{[ticket.id for ticket in current_contract.tickets]}. "
    #                 "It will NOT be deleted."
    #             )
    #     self.user_db.current_ticket = None
    #     return True

    # def _build_pick_confirm_quit_without_saving(
    #     self, text: str = f"{String.ARE_YOU_SURE_YOU_WANT_TO_QUIT_WITHOUT_SAVING}"
    # ) -> SendMessageTG:
    #     return SendMessageTG(
    #         chat_id=self.user_db.telegram_uid,
    #         text=text,
    #         reply_markup=InlineKeyboardMarkupTG(
    #             inline_keyboard=[
    #                 [
    #                     InlineKeyboardButtonTG(
    #                         text=String.CONFIRM_QUIT_BTN,
    #                         callback_data=CallbackData.CONFIRM_QUIT_BTN,
    #                     ),
    #                     InlineKeyboardButtonTG(
    #                         text=String.CHANGED_MY_MIND_BTN,
    #                         callback_data=CallbackData.CHANGED_MY_MIND_BTN,
    #                     ),
    #                 ],
    #             ]
    #         ),
    #     )
