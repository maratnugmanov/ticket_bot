from __future__ import annotations
from typing import Any, Callable, Coroutine
import inspect
import re
import httpx
from pydantic import ValidationError
from sqlalchemy import select, exists
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
from src.db.engine import SessionDepDB
from src.db.models import (
    RoleDB,
    UserDB,
    TicketDB,
    ReportDB,
    WriteoffDB,
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
        session_db: SessionDepDB,
        user_db: UserDB,
    ):
        self.update_tg: MessageUpdateTG | CallbackQueryUpdateTG = update_tg
        self.log_prefix: str = self.update_tg._log
        self.session_db: SessionDepDB = session_db
        self.user_db: UserDB = user_db
        self.state: StateJS | None = (
            StateJS.model_validate_json(user_db.state_json)
            if user_db.state_json
            else None
        )
        self.next_state: StateJS | None = None
        self.response_methods_list: list[MethodTG] = []
        logger.info(
            f"{self.log_prefix}User {self.user_db.full_name} state: {self.state}"
        )
        self._stateless_callback_handlers: dict[
            CallbackData, Callable[[int, int], list[MethodTG]]
        ] = {
            CallbackData.ENTER_TICKET_NUMBER: self._handle_stateless_cb_enter_ticket_number,
            CallbackData.ENABLE_HIRING_BTN: self._handle_stateless_cb_enable_hiring,
            CallbackData.DISABLE_HIRING_BTN: self._handle_stateless_cb_disable_hiring,
        }
        self._state_handlers: dict[
            Action,
            Callable[[StateJS], Coroutine[Any, Any, list[MethodTG]]]
            | Callable[[StateJS], list[MethodTG]],
        ] = {
            Action.ENTER_TICKET_NUMBER: self._handle_action_enter_ticket_number,
            Action.ENTER_CONTRACT_NUMBER: self._handle_action_enter_contract_number,
            Action.PICK_DEVICE_TYPE: self._handle_action_pick_device_type,
            Action.PICK_INSTALL_OR_RETURN: self._handle_action_pick_install_or_return,
            Action.ENTER_SERIAL_NUMBER: self._handle_action_enter_serial_number,
            Action.PICK_TICKET_ACTION: self._handle_action_pick_ticket_action,
            Action.EDIT_TICKET_NUMBER: self._handle_action_edit_ticket_number,
            Action.EDIT_CONTRACT_NUMBER: self._handle_action_edit_contract_number,
            Action.PICK_DEVICE_ACTION: self._handle_action_pick_device_action,
            Action.CONFIRM_CLOSE_TICKET: self._handle_pick_confirm_close_ticket,
            Action.CONFIRM_QUIT_WITHOUT_SAVING: self._handle_pick_confirm_quit_without_saving,
        }
        logger.info(
            f"{self.log_prefix}Conversation with {self.user_db.full_name} initialized."
        )

    @classmethod
    async def create(
        cls,
        update_tg: MessageUpdateTG | CallbackQueryUpdateTG,
        session_db: SessionDepDB,
    ) -> Conversation | None:
        """Asynchronously creates and initializes a Conversation
        instance by fetching or creating the relevant UserDB.
        Returns None if the user should be ignored
        (e.g., guest with no hiring)."""
        user_tg: UserTG | None = cls.get_user_tg(update_tg)
        if not user_tg:
            logger.error(
                f"{update_tg._log}CRITICAL: Could not extract Telegram user from "
                "supported update types (private message/callback)."
            )
            return None
        user_db: UserDB | None = await session_db.scalar(
            select(UserDB)
            .where(UserDB.telegram_uid == user_tg.id)
            .options(selectinload(UserDB.roles))
        )
        guest_role: RoleDB | None = None
        if user_db is None:
            logger.info(f"{update_tg._log}Guest {user_tg.full_name} is not registered.")
            hiring = await session_db.scalar(
                select(exists().where(UserDB.is_hiring == True))  # noqa: E712
            )
            if not hiring:
                logger.info(
                    f"{update_tg._log}User registration is disabled, "
                    f"Telegram user {user_tg.full_name} will be ignored."
                )
                return None
            logger.info(
                f"{update_tg._log}User registration is enabled, Telegram "
                f"user {user_tg.full_name} will be added to the "
                f"database with the default '{RoleName.GUEST}' role."
            )
            guest_role = await session_db.scalar(
                select(RoleDB).where(RoleDB.name == RoleName.GUEST)
            )
            if guest_role is None:
                error_message = (
                    f"{update_tg._log}CRITICAL: Default role "
                    f"'{RoleName.GUEST}' not found in the database. "
                    "Cannot create new UserDB instance."
                )
                logger.error(error_message)
                raise ValueError(error_message)
            user_db = UserDB(
                telegram_uid=user_tg.id,
                first_name=user_tg.first_name,
                last_name=user_tg.last_name,
            )
            user_db.roles.append(guest_role)
            session_db.add(user_db)
            await session_db.flush()
            logger.info(
                f"{update_tg._log}UserDB {user_db.full_name} "
                f"was created with id='{user_db.id}' and role="
                f"'{RoleName.GUEST.name}' in the database. It won't "
                "get any visible feedback to prevent unnecessary "
                "interactions with strangers from happening."
            )
            return None
        if len(user_db.roles) == 1:
            if guest_role is None:
                guest_role = await session_db.scalar(
                    select(RoleDB).where(RoleDB.name == RoleName.GUEST)
                )
                if guest_role is None:
                    error_message = (
                        f"{update_tg._log}CRITICAL: Default role "
                        f"'{RoleName.GUEST}' not found in the "
                        "database. Cannot create new UserDB instance."
                    )
                    logger.error(error_message)
                    raise ValueError(error_message)
            if user_db.roles[0].id == guest_role.id:
                logger.info(
                    f"{update_tg._log}UserDB {user_db.full_name} has only "
                    f"'{RoleName.GUEST}' role and won't get any reply."
                )
                return None
        logger.info(
            f"{update_tg._log}Validated UserDB {user_db.full_name} as employee."
        )
        return cls(update_tg, session_db, user_db)

    @staticmethod
    def get_user_tg(
        update_tg: MessageUpdateTG | CallbackQueryUpdateTG,
    ) -> UserTG | None:
        """Returns Telegram User (UserTG) by extracting it from relevant
        Telegram Update object. Returns None otherwise."""
        user_tg = None
        if (
            isinstance(update_tg, MessageUpdateTG)
            and not update_tg.message.from_.is_bot
            and update_tg.message.chat.type == "private"
        ):
            user_tg = update_tg.message.from_
            logger.info(f"{update_tg._log}Private message from {user_tg.full_name}.")
        elif (
            isinstance(update_tg, CallbackQueryUpdateTG)
            and not update_tg.callback_query.from_.is_bot
            and update_tg.callback_query.message
            and update_tg.callback_query.message.from_.is_bot
            and update_tg.callback_query.message.from_.id == settings.bot_id
            and update_tg.callback_query.message.chat.type == "private"
        ):
            user_tg = update_tg.callback_query.from_
            logger.info(f"{update_tg._log}Callback query from {user_tg.full_name}.")
        return user_tg

    async def _post_method_tg(self, method_tg: MethodTG) -> SuccessTG | ErrorTG | None:
        async with httpx.AsyncClient() as client:
            logger.info(
                f"{self.log_prefix}Method '{method_tg._url}' is being "
                f"sent in response to {self.user_db.full_name}."
            )
            try:
                response: httpx.Response = await client.post(
                    url=settings.get_tg_endpoint(method_tg._url),
                    json=method_tg.model_dump(exclude_none=True),
                )
                response.raise_for_status()
                logger.info(
                    f"{self.log_prefix}Method '{method_tg._url}' was "
                    "delivered to Telegram API "
                    f"(HTTP status {response.status_code})."
                )

                try:
                    response_data = response.json()
                    success_tg = SuccessTG.model_validate(response_data)
                    logger.info(
                        f"{self.log_prefix}Method '{method_tg._url}' "
                        "was accepted by Telegram API."
                        # f" Response JSON: {response_data}"
                    )
                    logger.debug(f"{response_data}")
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
                        "Telegram API Error Details for "
                        f"method '{method_tg._url}': "
                        f"error_code='{error_tg.error_code}', "
                        f"description='{error_tg.description}'"
                    )
                    return error_tg  # Return the response even on error status
                except (ValidationError, Exception) as error_parsing_error:
                    logger.error(
                        f"Could not validate/parse Telegram error "
                        f"response JSON after HTTPStatusError for "
                        f"Method '{method_tg._url}': {error_parsing_error}"
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
                    f"during API call for method '{method_tg._url}': {e}",
                    exc_info=True,
                )
                return None

    async def _make_delivery(
        self,
        method_generator: Callable[[], list[MethodTG]]
        | Callable[[], Coroutine[Any, Any, list[MethodTG]]],
        ensure_delivery: bool = True,
    ) -> bool:
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
                    if isinstance(self.next_state, StateJS):
                        next_state_json = self.next_state.model_dump_json(
                            exclude_none=True
                        )
                        self.user_db.state_json = next_state_json
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
                        if isinstance(self.next_state, StateJS):
                            next_state_json = self.next_state.model_dump_json(
                                exclude_none=True
                            )
                            self.user_db.state_json = next_state_json
                        success = True
        return success

    async def process(self) -> bool:
        success = False
        if self.state is None:
            if self.next_state is not None:
                self.next_state = None
            success = await self._make_delivery(self._stateless_conversation)
        else:
            if self.state.script == Script.INITIAL_DATA:
                success = await self._make_delivery(self._state_action_conversation)
        return success

    def _stateless_conversation(self) -> list[MethodTG]:
        logger.info(
            f"{self.log_prefix}Starting new conversation with {self.user_db.full_name}."
        )
        if self.state is not None:
            raise ValueError("'self.state' should be None at this point.")
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, MessageUpdateTG):
            message_id = self.update_tg.message.message_id
            logger.info(
                f"{self.log_prefix}Message #{message_id} from {self.user_db.full_name}."
            )
            logger.info(
                f"{self.log_prefix}Preparing main menu for {self.user_db.full_name}."
            )
            methods_tg_list.append(
                self._build_stateless_mainmenu_message(f"{String.PICK_A_FUNCTION}.")
            )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
            raw_data = self.update_tg.callback_query.data
            message_id = self.update_tg.callback_query.message.message_id
            chat_id = self.update_tg.callback_query.message.chat.id
            logger.info(
                f"{self.log_prefix}Received callback query "
                f"'{raw_data}' from {self.user_db.full_name}."
            )
            try:
                received_callback_enum = CallbackData(raw_data)
                callback_handler = self._stateless_callback_handlers.get(
                    received_callback_enum
                )
                if callback_handler:
                    methods_tg_list.extend(callback_handler(chat_id, message_id))
                else:
                    methods_tg_list.extend(
                        self._handle_unrecognized_stateless_callback(raw_data)
                    )
            except ValueError:
                methods_tg_list.extend(
                    self._handle_unrecognized_stateless_callback(raw_data)
                )
        return methods_tg_list

    async def _state_action_conversation(self) -> list[MethodTG]:
        logger.info(
            f"{self.log_prefix}Continuing conversation with {self.user_db.full_name}."
        )
        if self.state is None:
            error_msg = "'self.state' cannot be None at this point."
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        action_handler = self._state_handlers.get(self.state.action)
        if action_handler:
            if inspect.iscoroutinefunction(action_handler):
                logger.info(
                    f"{self.log_prefix}Calling async handler for '{self.state.action}'."
                )
                methods_tg_list.extend(await action_handler(self.state))
            else:
                logger.info(
                    f"{self.log_prefix}Calling sync handler for '{self.state.action}'."
                )
                methods_tg_list.extend(action_handler(self.state))  # type: ignore
        else:
            error_msg = (
                f"{self.log_prefix}Unhandled action: "
                f"'{self.state.action.value}' for user "
                f"{self.user_db.full_name}. No handler implemented."
            )
            logger.error(error_msg)
            raise NotImplementedError(error_msg)
        return methods_tg_list

    def _handle_stateless_cb_enter_ticket_number(
        self, chat_id: int, message_id: int
    ) -> list[MethodTG]:
        """Handles CallbackData.ENTER_TICKET_NUMBER in a stateless
        conversation."""
        # chat_id, message_id are part of the uniform signature but
        # might not be used directly by all handlers.
        logger.info(
            f"{self.log_prefix}Callback "
            f"'{CallbackData.ENTER_TICKET_NUMBER}' received. "
            "Preparing for ticket number input."
        )
        self.next_state = StateJS(
            action=Action.ENTER_TICKET_NUMBER,
            script=Script.INITIAL_DATA,
        )
        methods_tg_list: list[MethodTG] = [
            self._build_edit_to_text_message(String.CLOSE_TICKET_BTN),
            self._build_new_text_message(f"{String.ENTER_TICKET_NUMBER}."),
        ]
        return methods_tg_list

    def _handle_stateless_cb_enable_hiring(
        self, chat_id: int, message_id: int
    ) -> list[MethodTG]:
        """Handles CallbackData.ENABLE_HIRING_BTN in a stateless
        conversation."""
        logger.info(
            f"{self.log_prefix}Callback "
            f"'{CallbackData.ENABLE_HIRING_BTN}' received. "
            "Attempting to enable hiring."
        )
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
            inline_keyboard=self._helper_mainmenu_keyboard_array()
        )
        methods_tg_list: list[MethodTG] = [method_tg]
        return methods_tg_list

    def _handle_stateless_cb_disable_hiring(
        self, chat_id: int, message_id: int
    ) -> list[MethodTG]:
        """Handles CallbackData.DISABLE_HIRING_BTN in a stateless
        conversation."""
        logger.info(
            f"{self.log_prefix}Callback "
            f"'{CallbackData.DISABLE_HIRING_BTN}' received. "
            "Attempting to disable hiring."
        )
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
            inline_keyboard=self._helper_mainmenu_keyboard_array()
        )
        methods_tg_list: list[MethodTG] = [method_tg]
        return methods_tg_list

    def _handle_unrecognized_stateless_callback(self, raw_data: str) -> list[MethodTG]:
        """Handles unrecognized callback data in a stateless conversation."""
        logger.info(
            f"{self.log_prefix}Unrecognized callback data='{raw_data}'. "
            f"Preparing main menu for {self.user_db.full_name}."
        )
        methods_tg_list: list[MethodTG] = []
        methods_tg_list.append(
            self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
        )
        methods_tg_list.append(
            self._build_stateless_mainmenu_message(
                f"{String.GOT_UNEXPECTED_DATA}. "
                f"{String.PICK_A_FUNCTION} {String.FROM_OPTIONS_BELOW}."
            )
        )
        return methods_tg_list

    def _handle_action_enter_ticket_number(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting ticket number.")
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text
                if re.fullmatch(r"\d+", message_text):
                    logger.info(
                        f"{self.log_prefix}Got correct ticket number: '{message_text}'."
                    )
                    self.next_state = state.model_copy(deep=True)
                    self.next_state.action = Action.ENTER_CONTRACT_NUMBER
                    self.next_state.ticket_number = int(message_text)
                    methods_tg_list.append(
                        self._build_new_text_message(f"{String.ENTER_CONTRACT_NUMBER}.")
                    )
                else:
                    methods_tg_list.append(
                        self._build_new_text_message(
                            f"{String.INCORRECT_TICKET_NUMBER}. "
                            f"{String.ENTER_TICKET_NUMBER}."
                        )
                    )
            else:
                methods_tg_list.append(
                    self._build_new_text_message(
                        f"{String.INCORRECT_TICKET_NUMBER}. "
                        f"{String.ENTER_TICKET_NUMBER}."
                    )
                )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
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

    async def _handle_action_enter_contract_number(
        self, state: StateJS
    ) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting contract number.")
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text
                if re.fullmatch(r"\d+", message_text):
                    logger.info(
                        f"{self.log_prefix}Got correct "
                        f"contract number: '{message_text}'."
                    )
                    self.next_state = state.model_copy(deep=True)
                    self.next_state.action = Action.PICK_DEVICE_TYPE
                    self.next_state.contract_number = int(message_text)
                    methods_tg_list.append(
                        await self._build_pick_device_type_message(
                            f"{String.PICK_DEVICE_TYPE}."
                        )
                    )
                else:
                    methods_tg_list.append(
                        self._build_new_text_message(
                            f"{String.INCORRECT_CONTRACT_NUMBER}. "
                            f"{String.ENTER_CONTRACT_NUMBER}."
                        )
                    )
            else:
                methods_tg_list.append(
                    self._build_new_text_message(
                        f"{String.INCORRECT_CONTRACT_NUMBER}. "
                        f"{String.ENTER_CONTRACT_NUMBER}."
                    )
                )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
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

    async def _handle_action_pick_device_type(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting device type choice to be made.")
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                logger.info(
                    f"{self.log_prefix}CallbackData is '{received_callback_data}'."
                )
                if received_callback_data.name not in DeviceTypeName.__members__:
                    logger.info(
                        f"{self.log_prefix}CallbackData is not a DeviceTypeName."
                    )
                    raise ValueError
                methods_tg_list.append(self._build_edit_to_callback_button_text())
                device_type_db = await self.session_db.scalar(
                    select(DeviceTypeDB).where(
                        DeviceTypeDB.name == DeviceTypeName[received_callback_data.name]
                    )
                )
                if device_type_db is None:
                    logger.info(
                        f"{self.log_prefix}No DeviceTypeDB found for "
                        f"{received_callback_data.name}."
                    )
                    methods_tg_list.append(
                        await self._build_pick_device_type_message(
                            f"{String.GOT_UNEXPECTED_DATA}. "
                            f"{String.PICK_DEVICE_TYPE} "
                            f"{String.FROM_OPTIONS_BELOW}."
                        )
                    )
                elif device_type_db.is_disabled:
                    logger.info(
                        f"{self.log_prefix}DeviceTypeDB "
                        f"'{device_type_db.name}' is disabled."
                    )
                    methods_tg_list.append(
                        await self._build_pick_device_type_message(
                            f"{String.DEVICE_TYPE_IS_DISABLED}. "
                            f"{String.PICK_DEVICE_TYPE} "
                            f"{String.FROM_OPTIONS_BELOW}."
                        )
                    )
                else:
                    logger.info(
                        f"{self.log_prefix}Found active DeviceTypeDB: "
                        f"id={device_type_db.id}, name='{device_type_db.name}'."
                    )
                    self.next_state = state.model_copy(deep=True)
                    device_type_js = DeviceTypeJS.model_validate(device_type_db)
                    devices_list = self.next_state.devices_list
                    device_index = self.next_state.device_index
                    device_list_length = len(devices_list)
                    if device_index == device_list_length:
                        device = DeviceJS(
                            type=device_type_js,
                        )
                        devices_list.append(device)
                    elif 0 <= device_index < device_list_length:
                        devices_list[device_index].type = device_type_js
                    else:
                        error_msg = (
                            f"{self.log_prefix}Error: "
                            f"device_index='{device_index}' "
                            f"and device_list_length='{device_list_length}'. "
                            f"Expected: 0 <= device_index <= device_list_length."
                        )
                        logger.error(error_msg)
                        raise IndexError(error_msg)
                    if device_type_js.is_returnable:
                        logger.info(f"Device type '{device_type_js}' is returnable.")
                        self.next_state.action = Action.PICK_INSTALL_OR_RETURN
                        methods_tg_list.append(
                            self._build_pick_install_or_return_message(
                                f"{String.PICK_INSTALL_OR_RETURN}."
                            )
                        )
                    else:
                        logger.info(
                            f"Device type '{device_type_js}' is not "
                            "returnable. Install or return step "
                            "will be skipped."
                        )
                        devices_list[device_index].is_defective = False
                        if device_type_js.has_serial_number:
                            logger.info(
                                f"Device type '{device_type_js}' has serial number parameter."
                            )
                            self.next_state.action = Action.ENTER_SERIAL_NUMBER
                            methods_tg_list.append(
                                self._build_new_text_message(
                                    f"{String.ENTER_SERIAL_NUMBER}."
                                )
                            )
                        else:
                            logger.info(
                                f"Device type '{device_type_js}' doesn't have "
                                "serial number parameter. Serial number "
                                "step will be skipped."
                            )
                            self.next_state.action = Action.PICK_TICKET_ACTION
                            # self.next_state.device_index = 0
                            methods_tg_list.append(
                                self._build_pick_ticket_action_message(
                                    f"{String.PICK_TICKET_ACTION}."
                                )
                            )
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Received invalid callback "
                    f"'{raw_data}' for device type selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    await self._build_pick_device_type_message(
                        f"{String.GOT_UNEXPECTED_DATA}. "
                        f"{String.PICK_DEVICE_TYPE} "
                        f"{String.FROM_OPTIONS_BELOW}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(
                f"{self.log_prefix}User {self.user_db.full_name} "
                "responded with message while callback data "
                "was awaited."
            )
            methods_tg_list.append(
                await self._build_pick_device_type_message(
                    f"{String.DEVICE_TYPE_WAS_NOT_PICKED}. "
                    f"{String.PICK_DEVICE_TYPE} "
                    f"{String.FROM_OPTIONS_BELOW}."
                )
            )
        logger.info(f"{self.log_prefix}Methods are ready to be posted.")
        return methods_tg_list

    def _handle_action_pick_install_or_return(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting install or return choice to be made.")
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.INSTALL_DEVICE_BTN,
                CallbackData.RETURN_DEVICE_BTN,
            ]
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                logger.info(
                    f"{self.log_prefix}CallbackData is '{received_callback_data}'."
                )
                if received_callback_data in expected_callback_data:
                    if received_callback_data == CallbackData.INSTALL_DEVICE_BTN:
                        is_defective = False
                    elif received_callback_data == CallbackData.RETURN_DEVICE_BTN:
                        is_defective = True
                    else:
                        error_msg = (
                            "Callback is in expected list, "
                            "but somehow was not identified."
                        )
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                    methods_tg_list.append(self._build_edit_to_callback_button_text())
                    self.next_state = state.model_copy(deep=True)
                    devices_list = self.next_state.devices_list
                    device_index = self.next_state.device_index
                    device_list_length = len(devices_list)
                    if 0 <= device_index < device_list_length:
                        devices_list[device_index].is_defective = is_defective
                    else:
                        error_msg = (
                            f"{self.log_prefix}Error: "
                            f"device_index='{device_index}' "
                            f"and device_list_length='{device_list_length}'. "
                            f"Expected: 0 <= device_index < device_list_length."
                        )
                        logger.error(error_msg)
                        raise IndexError(error_msg)
                    device_type = devices_list[device_index].type
                    if device_type.has_serial_number:
                        logger.info(
                            f"Device type '{device_type}' has serial number parameter."
                        )
                        self.next_state.action = Action.ENTER_SERIAL_NUMBER
                        methods_tg_list.append(
                            self._build_new_text_message(
                                f"{String.ENTER_SERIAL_NUMBER}."
                            )
                        )
                    else:
                        logger.info(
                            f"Device type '{device_type}' doesn't have "
                            "serial number parameter. Serial number "
                            "step will be skipped."
                        )
                        self.next_state.action = Action.PICK_TICKET_ACTION
                        self.next_state.device_index = 0
                        methods_tg_list.append(
                            self._build_pick_ticket_action_message(
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                else:
                    raise ValueError
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Received invalid callback "
                    f"'{raw_data}' for device action selection."
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
            logger.info(
                f"{self.log_prefix}User {self.user_db.full_name} "
                "responded with message while callback data "
                "was awaited."
            )
            methods_tg_list.append(
                self._build_pick_install_or_return_message(
                    f"{String.DEVICE_ACTION_WAS_NOT_PICKED}. "
                    f"{String.PICK_INSTALL_OR_RETURN}."
                )
            )
        return methods_tg_list

    def _handle_action_enter_serial_number(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting device serial number.")
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text.upper()
                if re.fullmatch(r"[\dA-Z]+", message_text):
                    logger.info(
                        f"{self.log_prefix}Got correct device "
                        f"serial number: '{message_text}'."
                    )
                    self.next_state = state.model_copy(deep=True)
                    self.next_state.action = Action.PICK_TICKET_ACTION
                    devices_list = self.next_state.devices_list
                    device_index = self.next_state.device_index
                    device_list_length = len(devices_list)
                    if 0 <= device_index < device_list_length:
                        devices_list[device_index].serial_number = message_text
                    else:
                        error_msg = (
                            f"{self.log_prefix}Error: "
                            f"device_index='{device_index}' "
                            f"and device_list_length='{device_list_length}'. "
                            f"Expected: 0 <= device_index < device_list_length."
                        )
                        logger.error(error_msg)
                        raise IndexError(error_msg)
                    methods_tg_list.append(
                        self._build_pick_ticket_action_message(
                            f"{String.PICK_TICKET_ACTION}."
                        )
                    )
                else:
                    methods_tg_list.append(
                        self._build_new_text_message(
                            f"{String.INCORRECT_SERIAL_NUMBER}. "
                            f"{String.ENTER_SERIAL_NUMBER}."
                        )
                    )
            else:
                methods_tg_list.append(
                    self._build_new_text_message(
                        f"{String.INCORRECT_SERIAL_NUMBER}. "
                        f"{String.ENTER_SERIAL_NUMBER}."
                    )
                )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
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

    async def _handle_action_pick_ticket_action(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting ticket menu choice to be made.")
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.EDIT_TICKET_NUMBER,
                CallbackData.EDIT_CONTRACT_NUMBER,
                CallbackData.QUIT_WITHOUT_SAVING_BTN,
            ]
            device_list_length = len(state.devices_list)
            if device_list_length < settings.devices_per_ticket:
                expected_callback_data.append(CallbackData.ADD_DEVICE_BTN)
            if device_list_length > 0:
                expected_callback_data.append(CallbackData.CLOSE_TICKET_BTN)
            all_devices_list = [
                CallbackData[f"DEVICE_{index}"]
                for index in range(settings.devices_per_ticket)
            ]
            expected_devices_list = all_devices_list[:device_list_length]
            expected_callback_data.extend(expected_devices_list)
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                logger.info(
                    f"{self.log_prefix}CallbackData is '{received_callback_data}'."
                )
                if received_callback_data in expected_callback_data:
                    self.next_state = state.model_copy(deep=True)
                    if received_callback_data == CallbackData.EDIT_TICKET_NUMBER:
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.EDIT_TICKET_NUMBER}."
                            )
                        )
                        self.next_state.action = Action.EDIT_TICKET_NUMBER
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
                        self.next_state.action = Action.EDIT_CONTRACT_NUMBER
                        methods_tg_list.append(
                            self._build_new_text_message(
                                f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                            )
                        )
                    elif received_callback_data in expected_devices_list:
                        pattern = r"(\d+)$"
                        match = re.search(pattern, received_callback_data)
                        if match:
                            device_index_string = match.group(1)
                        else:
                            error_msg = f"{self.log_prefix}StrEnum "
                            f"'{received_callback_data}' doesn't end "
                            "with an integer."
                            logger.error(error_msg)
                            raise ValueError(error_msg)
                        callback_device_index = int(device_index_string)
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.EDIT_DEVICE} {callback_device_index + 1}."
                            )
                        )
                        self.next_state.action = Action.PICK_DEVICE_ACTION
                        self.next_state.device_index = callback_device_index
                        methods_tg_list.append(
                            self._build_pick_device_action_message(
                                f"{String.PICK_DEVICE_ACTION}."
                            )
                        )
                    elif received_callback_data == CallbackData.ADD_DEVICE_BTN:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state.action = Action.PICK_DEVICE_TYPE
                        self.next_state.device_index = device_list_length
                        methods_tg_list.append(
                            await self._build_pick_device_type_message(
                                f"{String.PICK_DEVICE_TYPE}."
                            )
                        )
                    elif received_callback_data == CallbackData.CLOSE_TICKET_BTN:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state.action = Action.CONFIRM_CLOSE_TICKET
                        methods_tg_list.append(
                            self._build_pick_confirm_close_ticket_message(
                                f"{String.CONFIRM_YOU_WANT_TO_CLOSE_TICKET}."
                            )
                        )
                    elif received_callback_data == CallbackData.QUIT_WITHOUT_SAVING_BTN:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state.action = Action.CONFIRM_QUIT_WITHOUT_SAVING
                        methods_tg_list.append(
                            self._build_pick_confirm_quit_without_saving(
                                f"{String.ARE_YOU_SURE_YOU_WANT_TO_QUIT_WITHOUT_SAVING}?"
                            )
                        )
                else:
                    raise ValueError
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Received invalid callback "
                    f"'{raw_data}' for ticket menu selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    self._build_pick_ticket_action_message(
                        f"{String.GOT_UNEXPECTED_DATA}. {String.PICK_TICKET_ACTION}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(
                f"{self.log_prefix}User {self.user_db.full_name} "
                "responded with message while callback data "
                "was awaited."
            )
            methods_tg_list.append(
                self._build_pick_ticket_action_message(
                    f"{String.TICKET_ACTION_WAS_NOT_PICKED}. "
                    f"{String.PICK_TICKET_ACTION}."
                )
            )
        return methods_tg_list

    def _handle_action_edit_ticket_number(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting new ticket number.")
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text
                if re.fullmatch(r"\d+", message_text):
                    logger.info(
                        f"{self.log_prefix}Got correct new "
                        f"ticket number: '{message_text}'."
                    )
                    self.next_state = state.model_copy(deep=True)
                    self.next_state.action = Action.PICK_TICKET_ACTION
                    self.next_state.ticket_number = int(message_text)
                    methods_tg_list.append(
                        self._build_pick_ticket_action_message(
                            f"{String.TICKET_NUMBER_WAS_EDITED}. "
                            f"{String.PICK_TICKET_ACTION}."
                        )
                    )
                else:
                    methods_tg_list.append(
                        self._build_new_text_message(
                            f"{String.INCORRECT_TICKET_NUMBER}. "
                            f"{String.ENTER_NEW_TICKET_NUMBER}."
                        )
                    )
            else:
                methods_tg_list.append(
                    self._build_new_text_message(
                        f"{String.INCORRECT_TICKET_NUMBER}. "
                        f"{String.ENTER_NEW_TICKET_NUMBER}."
                    )
                )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
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

    def _handle_action_edit_contract_number(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting new contract number.")
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text
                if re.fullmatch(r"\d+", message_text):
                    logger.info(
                        f"{self.log_prefix}Got correct new "
                        f"contract number: '{message_text}'."
                    )
                    self.next_state = state.model_copy(deep=True)
                    self.next_state.action = Action.PICK_TICKET_ACTION
                    self.next_state.contract_number = int(message_text)
                    methods_tg_list.append(
                        self._build_pick_ticket_action_message(
                            f"{String.CONTRACT_NUMBER_WAS_EDITED}. "
                            f"{String.PICK_TICKET_ACTION}."
                        )
                    )
                else:
                    methods_tg_list.append(
                        self._build_new_text_message(
                            f"{String.INCORRECT_CONTRACT_NUMBER}. "
                            f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                        )
                    )
            else:
                methods_tg_list.append(
                    self._build_new_text_message(
                        f"{String.INCORRECT_CONTRACT_NUMBER}. "
                        f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                    )
                )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
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

    async def _handle_action_pick_device_action(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting device menu choice to be made.")
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.EDIT_DEVICE_TYPE,
                CallbackData.DELETE_DEVICE_BTN,
            ]
            devices_list = state.devices_list
            device_index = state.device_index
            device_list_length = len(devices_list)
            if 0 <= device_index < device_list_length:
                device_js = devices_list[device_index]
            else:
                error_msg = (
                    f"{self.log_prefix}Error: "
                    f"device_index='{device_index}' "
                    f"and device_list_length='{device_list_length}'. "
                    f"Expected: 0 <= device_index < device_list_length."
                )
                logger.error(error_msg)
                raise IndexError(error_msg)
            if device_js.type.is_returnable:
                if device_js.is_defective is True:
                    expected_callback_data.append(CallbackData.RETURN_DEVICE_BTN)
                elif device_js.is_defective is False:
                    expected_callback_data.append(CallbackData.INSTALL_DEVICE_BTN)
                else:
                    raise ValueError(
                        "Device is returnable but is_defective "
                        f" is '{device_js.is_defective}'."
                    )
            if device_js.type.has_serial_number:
                expected_callback_data.append(CallbackData.EDIT_SERIAL_NUMBER)
                if device_js.serial_number is not None:
                    expected_callback_data.append(CallbackData.EDIT_TICKET)
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                logger.info(
                    f"{self.log_prefix}CallbackData is '{received_callback_data}'."
                )
                if received_callback_data in expected_callback_data:
                    self.next_state = state.model_copy(deep=True)
                    if received_callback_data == CallbackData.EDIT_DEVICE_TYPE:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state.action = Action.EDIT_DEVICE_TYPE
                        methods_tg_list.append(
                            await self._build_pick_device_type_message(
                                f"{String.PICK_DEVICE_TYPE}."
                            )
                        )
                    elif received_callback_data in [
                        CallbackData.RETURN_DEVICE_BTN,
                        CallbackData.INSTALL_DEVICE_BTN,
                    ]:
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.EDIT_INSTALL_OR_RETURN}."
                            )
                        )
                        self.next_state.action = Action.EDIT_INSTALL_OR_RETURN
                        methods_tg_list.append(
                            self._build_pick_install_or_return_message(
                                f"{String.PICK_INSTALL_OR_RETURN}."
                            )
                        )
                    elif received_callback_data == CallbackData.EDIT_SERIAL_NUMBER:
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.EDIT_SERIAL_NUMBER}."
                            )
                        )
                        self.next_state.action = Action.EDIT_SERIAL_NUMBER
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
                        self.next_state.action = Action.PICK_TICKET_ACTION
                        methods_tg_list.append(
                            self._build_pick_ticket_action_message(
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                    elif received_callback_data == CallbackData.DELETE_DEVICE_BTN:
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.RETURNING_TO_TICKET}."
                            )
                        )
                        next_devices_list = self.next_state.devices_list
                        next_device_index = self.next_state.device_index
                        del next_devices_list[next_device_index]
                        self.next_state.device_index = 0
                        methods_tg_list.append(
                            self._build_edit_to_text_message(
                                f"{String.DEVICE_WAS_DELETED_FROM_TICKET}."
                            )
                        )
                        self.next_state.action = Action.PICK_TICKET_ACTION
                        methods_tg_list.append(
                            self._build_pick_ticket_action_message(
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                else:
                    raise ValueError
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Received invalid callback "
                    f"'{raw_data}' for device menu selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    self._build_pick_device_action_message(
                        f"{String.GOT_UNEXPECTED_DATA}. {String.PICK_DEVICE_ACTION}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(
                f"{self.log_prefix}User {self.user_db.full_name} "
                "responded with message while callback data "
                "was awaited."
            )
            methods_tg_list.append(
                self._build_pick_device_action_message(
                    f"{String.DEVICE_ACTION_WAS_NOT_PICKED}. "
                    f"{String.PICK_DEVICE_ACTION}."
                )
            )
        return methods_tg_list

    async def _handle_pick_confirm_close_ticket(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting close ticket confirmation.")
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.CONFIRM_CLOSE_TICKET_BTN,
                CallbackData.CHANGED_MY_MIND_BTN,
            ]
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                logger.info(
                    f"{self.log_prefix}CallbackData is '{received_callback_data}'."
                )
                if received_callback_data in expected_callback_data:
                    if received_callback_data == CallbackData.CONFIRM_CLOSE_TICKET_BTN:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        ticket_closed = self.close_ticket()
                        if ticket_closed:
                            self.next_state = None
                            self.user_db.state_json = None
                            methods_tg_list.append(
                                self._build_stateless_mainmenu_message(
                                    f"{String.YOU_CLOSED_TICKET}. {String.PICK_A_FUNCTION}."
                                )
                            )
                        else:
                            self.next_state = state.model_copy(deep=True)
                            self.next_state.action = Action.PICK_TICKET_ACTION
                            methods_tg_list.append(
                                self._build_pick_ticket_action_message(
                                    f"{String.TICKET_CLOSE_FAILED}. "
                                    f"{String.PICK_TICKET_ACTION}."
                                )
                            )
                    elif received_callback_data == CallbackData.CHANGED_MY_MIND_BTN:
                        self.next_state = state.model_copy(deep=True)
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state.action = Action.PICK_TICKET_ACTION
                        methods_tg_list.append(
                            self._build_pick_ticket_action_message(
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                else:
                    raise ValueError
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Received invalid callback "
                    f"'{raw_data}' for close ticket confirmation "
                    "menu selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    self._build_pick_confirm_close_ticket_message(
                        f"{String.GOT_UNEXPECTED_DATA}. "
                        f"{String.CONFIRM_YOU_WANT_TO_CLOSE_TICKET}"
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(
                f"{self.log_prefix}User {self.user_db.full_name} "
                "responded with message while callback data "
                "was awaited."
            )
            methods_tg_list.append(
                self._build_pick_confirm_close_ticket_message(
                    f"{String.CLOSE_TICKET_ACTION_WAS_NOT_PICKED}. "
                    f"{String.CONFIRM_YOU_WANT_TO_CLOSE_TICKET}"
                )
            )
        return methods_tg_list

    def _handle_pick_confirm_quit_without_saving(
        self, state: StateJS
    ) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting quit without saving confirmation.")
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.CONFIRM_QUIT_BTN,
                CallbackData.CHANGED_MY_MIND_BTN,
            ]
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                logger.info(
                    f"{self.log_prefix}CallbackData is '{received_callback_data}'."
                )
                if received_callback_data in expected_callback_data:
                    if received_callback_data == CallbackData.CONFIRM_QUIT_BTN:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state = None
                        self.user_db.state_json = None
                        methods_tg_list.append(
                            self._build_stateless_mainmenu_message(
                                f"{String.YOU_QUIT_WITHOUT_SAVING}. {String.PICK_A_FUNCTION}."
                            )
                        )
                    elif received_callback_data == CallbackData.CHANGED_MY_MIND_BTN:
                        self.next_state = state.model_copy(deep=True)
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state.action = Action.PICK_TICKET_ACTION
                        methods_tg_list.append(
                            self._build_pick_ticket_action_message(
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                else:
                    raise ValueError
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Received invalid callback "
                    f"'{raw_data}' for quit without saving "
                    "confirmation menu selection."
                )
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
                methods_tg_list.append(
                    self._build_pick_confirm_quit_without_saving(
                        f"{String.GOT_UNEXPECTED_DATA}. "
                        f"{String.ARE_YOU_SURE_YOU_WANT_TO_QUIT_WITHOUT_SAVING}?"
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(
                f"{self.log_prefix}User {self.user_db.full_name} "
                "responded with message while callback data "
                "was awaited."
            )
            methods_tg_list.append(
                self._build_pick_confirm_quit_without_saving(
                    f"{String.QUIT_WITHOUT_SAVING_ACTION_WAS_NOT_PICKED}. "
                    f"{String.ARE_YOU_SURE_YOU_WANT_TO_QUIT_WITHOUT_SAVING}?"
                )
            )
        return methods_tg_list

    def _build_stateless_mainmenu_message(self, text: str) -> SendMessageTG:
        mainmenu_keyboard_array = self._helper_mainmenu_keyboard_array()
        if mainmenu_keyboard_array:
            text = text
            reply_markup = InlineKeyboardMarkupTG(
                inline_keyboard=mainmenu_keyboard_array
            )
        else:
            text = f"{String.NO_FUNCTIONS_ARE_AVAILABLE}."
            reply_markup = None
        method_tg = SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=reply_markup,
        )
        return method_tg

    def _build_edit_to_callback_button_text(
        self, prefix_text: str = ""
    ) -> EditMessageTextTG:
        """Modifies callback message text to the string provided."""
        if not isinstance(self.update_tg, CallbackQueryUpdateTG):
            raise TypeError(
                "This method only works with CallbackQueryUpdateTG update type only."
            )
        if self.update_tg.callback_query.message.reply_markup is None:
            raise ValueError("This method only works with inline keyboard attached.")
        chat_id = self.update_tg.callback_query.message.chat.id
        message_id = self.update_tg.callback_query.message.message_id
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
                        f"{self.log_prefix}Button text '{button_text}' found "
                        f"for callback '{callback_data}'."
                    )
                    break
            else:
                continue
            break
        logger.info(
            f"{self.log_prefix}Editing message #{message_id} to "
            f"button text '{button_text}'."
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
                "This method works with CallbackQueryUpdateTG update type only."
            )
        chat_id = self.update_tg.callback_query.message.chat.id
        message_id = self.update_tg.callback_query.message.message_id
        # old_text = self.update_tg.callback_query.message.text
        logger.info(f"{self.log_prefix}Editing message #{message_id} to '{text}'.")
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

    async def _build_pick_device_type_message(
        self, text: str = f"{String.PICK_DEVICE_TYPE}."
    ) -> SendMessageTG:
        device_types_db = await self.session_db.scalars(
            select(DeviceTypeDB).where(DeviceTypeDB.is_disabled == False)  # noqa: E712
        )
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = []
        for device_type in device_types_db:
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
                error_msg = (
                    f"{self.log_prefix}Configuration Error: Missing "
                    "String or CallbackData enum member for "
                    f"DeviceTypeName '{device_type.name.name}'. "
                    f"Original error: {e}"
                )
                logger.error(error_msg)
                raise ValueError(error_msg) from e
        if not inline_keyboard:
            error_msg = (
                f"{self.log_prefix}Error: No eligible (non-disabled) "
                "device types were found in the database. Cannot build "
                "device type selection keyboard."
            )
            logger.warning(error_msg)
            self.next_state = None
            self.user_db.state_json = None
            method_tg = self._build_stateless_mainmenu_message(
                f"{String.NO_DEVICE_TYPE_AVAILABLE}. {String.PICK_A_FUNCTION}."
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
                            callback_data=CallbackData.INSTALL_DEVICE_BTN,
                        ),
                        InlineKeyboardButtonTG(
                            text=String.RETURN_DEVICE_BTN,
                            callback_data=CallbackData.RETURN_DEVICE_BTN,
                        ),
                    ],
                ]
            ),
        )

    def _build_pick_ticket_action_message(
        self, text: str = f"{String.PICK_TICKET_ACTION}."
    ) -> SendMessageTG:
        if (
            self.next_state
            and self.next_state.ticket_number
            and self.next_state.contract_number
        ):
            ticket_number = self.next_state.ticket_number
            contract_number = self.next_state.contract_number
            devices_list = self.next_state.devices_list
        elif self.state and self.state.ticket_number and self.state.contract_number:
            ticket_number = self.state.ticket_number
            contract_number = self.state.contract_number
            devices_list = self.state.devices_list
        else:
            raise ValueError(
                "The ticket menu only works with ticket number and "
                "contract number already being filled in."
            )
        inline_keyboard_array: list[list[InlineKeyboardButtonTG]] = []
        ticket_number_button: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=f"{String.TICKET_NUMBER_BTN} {ticket_number} {String.EDIT}",
                callback_data=CallbackData.EDIT_TICKET_NUMBER,
            ),
        ]
        contract_number_button: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=f"{String.CONTRACT_NUMBER_BTN} {contract_number} {String.EDIT}",
                callback_data=CallbackData.EDIT_CONTRACT_NUMBER,
            ),
        ]
        device_button_array: list[list[InlineKeyboardButtonTG]] = []
        for index, device in enumerate(devices_list):
            device_number = index + 1
            device_icon = "↪️" if device.is_defective else "✅"
            if not isinstance(device.type.name, DeviceTypeName):
                error_msg = f"{self.log_prefix}CRITICAL: device.type.name is not DeviceTypeName."
                logger.error(error_msg)
                raise AssertionError(error_msg)
            device_type = String[device.type.name.name]
            device_serial_number = device.serial_number
            if device_serial_number is not None:
                device_button_text = (
                    f"{device_number}. "
                    f"{device_icon} {device_type} "
                    f"{device_serial_number} >>"
                )
            else:
                device_button_text = f"{device_number}. {device_icon} {device_type} >>"
            device_button_array.append(
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
                callback_data=CallbackData.ADD_DEVICE_BTN,
            ),
        ]
        close_ticket_button: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=String.CLOSE_TICKET_BTN,
                callback_data=CallbackData.CLOSE_TICKET_BTN,
            ),
        ]
        quit_without_saving_button: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=String.QUIT_WITHOUT_SAVING_BTN,
                callback_data=CallbackData.QUIT_WITHOUT_SAVING_BTN,
            ),
        ]
        inline_keyboard_array.append(ticket_number_button)
        inline_keyboard_array.append(contract_number_button)
        inline_keyboard_array.extend(device_button_array)
        device_list_length = len(devices_list)
        if device_list_length < settings.devices_per_ticket:
            inline_keyboard_array.append(add_device_button)
        if device_list_length > 0:
            inline_keyboard_array.append(close_ticket_button)
        inline_keyboard_array.append(quit_without_saving_button)
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard_array),
        )

    def _build_pick_device_action_message(
        self, text: str = f"{String.PICK_DEVICE_ACTION}."
    ) -> SendMessageTG:
        if (
            self.next_state is not None
            and self.next_state.ticket_number is not None
            and self.next_state.contract_number is not None
            and len(self.next_state.devices_list) > self.next_state.device_index
            and self.next_state.devices_list[self.next_state.device_index].type
            is not None
        ):
            devices_list = self.next_state.devices_list
            device_index = self.next_state.device_index
        elif (
            self.state is not None
            and self.state.ticket_number is not None
            and self.state.contract_number is not None
            and len(self.state.devices_list) > self.state.device_index
            and self.state.devices_list[self.state.device_index].type is not None
        ):
            devices_list = self.state.devices_list
            device_index = self.state.device_index
        else:
            raise ValueError(
                "The device menu only works with state/next_state having at "
                "least one device being filled in."
            )
        inline_keyboard_array: list[list[InlineKeyboardButtonTG]] = []
        device = devices_list[device_index]
        if not isinstance(device.type, DeviceTypeName):
            error_msg = f"{self.log_prefix}CRITICAL: device.type is not DeviceTypeName."
            logger.error(error_msg)
            raise AssertionError(error_msg)
        device_type = String[device.type.name]
        device_serial_number = device.serial_number
        if device.is_defective:
            device_action_text = f"{String.RETURN_DEVICE_BTN} {String.EDIT}"
            device_action_data = CallbackData.RETURN_DEVICE_BTN
        else:
            device_action_text = f"{String.INSTALL_DEVICE_BTN} {String.EDIT}"
            device_action_data = CallbackData.INSTALL_DEVICE_BTN
        device_action_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=device_action_text,
            callback_data=device_action_data,
        )
        device_type_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=f"{device_type} {String.EDIT}",
            callback_data=CallbackData.EDIT_DEVICE_TYPE,
        )
        serial_number_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=f"{device_serial_number} {String.EDIT}",
            callback_data=CallbackData.EDIT_SERIAL_NUMBER,
        )
        return_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=String.RETURN_BTN,
            callback_data=CallbackData.EDIT_TICKET,
        )
        delete_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=String.DELETE_DEVICE_FROM_TICKET,
            callback_data=CallbackData.DELETE_DEVICE_BTN,
        )
        inline_keyboard_array.append([device_action_button])
        inline_keyboard_array.append([device_type_button])
        inline_keyboard_array.append([serial_number_button])
        inline_keyboard_array.append([return_button])
        inline_keyboard_array.append([delete_button])
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard_array),
        )

    def _build_pick_confirm_close_ticket_message(
        self, text: str = f"{String.CONFIRM_YOU_WANT_TO_CLOSE_TICKET}."
    ) -> SendMessageTG:
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(
                inline_keyboard=[
                    [
                        InlineKeyboardButtonTG(
                            text=String.CONFIRM_CLOSE_TICKET_BTN,
                            callback_data=CallbackData.CONFIRM_CLOSE_TICKET_BTN,
                        ),
                        InlineKeyboardButtonTG(
                            text=String.CHANGED_MY_MIND_BTN,
                            callback_data=CallbackData.CHANGED_MY_MIND_BTN,
                        ),
                    ],
                ]
            ),
        )

    async def close_ticket(self) -> bool:
        if self.state is None:
            raise ValueError("'self.state' cannot be None at this point.")
        if not self.state.devices_list:
            error_msg = (
                f"{self.log_prefix}CRITICAL: Attempting to close "
                "a ticket with no devices. You shouldn't see this."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        if not self.state.ticket_number or not self.state.contract_number:
            logger.error(
                f"{self.log_prefix}Cannot close ticket: ticket_number or "
                "contract_number is missing from state."
            )
            return False
        device_types_db = await self.session_db.scalars(select(DeviceTypeDB))
        if not device_types_db:
            logger.error(
                f"{self.log_prefix}Cannot fetch device types from the database."
            )
            return False
        device_type_db_map: dict[int, DeviceTypeDB] = {
            device_type_db.id: device_type_db for device_type_db in device_types_db
        }
        device_js_list: list[DeviceJS] = self.state.devices_list
        for device_js in device_js_list:
            try:
                log_prefix_val = f"{self.log_prefix}Ticket #"
                f"{self.state.ticket_number}: DeviceJS validation: "
                if device_js.is_defective is None:
                    raise ValueError(
                        f"{log_prefix_val}Missing 'is_defective' flag "
                        "used for identifying install from return "
                        "action."
                    )
                if (
                    device_js.type.is_returnable is False
                    and device_js.is_defective is not False
                ):
                    raise ValueError(
                        f"{log_prefix_val}Type "
                        f"'{device_js.type.name.name}' is not "
                        "returnable, but 'is_defective' is "
                        f"{device_js.is_defective} (expected False)."
                    )
                if device_js.type.has_serial_number:
                    if device_js.serial_number is None:
                        raise ValueError(
                            f"{log_prefix_val}Type "
                            f"'{device_js.type.name.name}' requires a "
                            "serial number, but it's missing."
                        )
                else:
                    if device_js.serial_number is not None:
                        raise ValueError(
                            f"{log_prefix_val}Type "
                            f"'{device_js.type.name.name}' does not "
                            "use a serial number, but one is provided "
                            f"('{device_js.serial_number}')."
                        )
                matched_db_type = device_type_db_map.get(device_js.type.id)
                if matched_db_type:
                    if not (
                        device_js.type.name == matched_db_type.name
                        and device_js.type.is_returnable
                        == matched_db_type.is_returnable
                        and device_js.type.has_serial_number
                        == matched_db_type.has_serial_number
                        and device_js.type.is_disabled == matched_db_type.is_disabled
                    ):
                        raise ValueError(
                            f"{log_prefix_val}Mismatch between "
                            f"provided device type "
                            f"'{device_js.type.name.name}' "
                            f"(ID: {device_js.type.id}) and database "
                            "version. "
                            f"JS: {device_js.type.model_dump(exclude_none=True)} vs "
                            f"DB: name={matched_db_type.name}, "
                            f"is_returnable={matched_db_type.is_returnable}, "
                            f"has_serial_number={matched_db_type.has_serial_number}, "
                            f"is_disabled={matched_db_type.is_disabled}"
                        )
                    if matched_db_type.is_disabled:
                        raise ValueError(
                            f"{log_prefix_val}Device type "
                            f"'{matched_db_type.name.name}' "
                            f"(ID: {matched_db_type.id}) is disabled "
                            "in the database."
                        )
                else:
                    raise ValueError(
                        f"{log_prefix_val}Unknown device type ID "
                        f"'{device_js.type.id}' in local data."
                    )
                if device_js.id is not None:
                    device_db_instance = await self.session_db.scalar(
                        select(DeviceDB).where(DeviceDB.id == device_js.id)
                    )
                    if device_db_instance is None:
                        raise ValueError(
                            f"{log_prefix_val}Provided device ID "
                            f"'{device_js.id}' not found in the database."
                        )
                else:
                    device_db_instance = DeviceDB(
                        type_id=device_js.type.id,
                        type=device_type_db_map[device_js.type.id],
                        is_defective=device_js.is_defective,
                        serial_number=device_js.serial_number,
                    )
            except ValueError as e:
                logger.error(str(e))
                return False

                for device_js in device_js_list:
                    if device_js.type is None or device_js.serial_number is None:
                        error_msg = (
                            f"{self.log_prefix}CRITICAL: Rolling back "
                            "database changes since DeviceJS object "
                            f"for ticket #{self.state.ticket_number} "
                            "is flawed. Ticket closing failed."
                        )
                        logger.error(error_msg)
                        await self.session_db.rollback()
                        return False
                    device_type_for_db = device_type_db_dict[device_js.type]
                    new_device_db = DeviceDB(
                        type_id=device_type_for_db.id,
                        type=device_type_for_db,
                        serial_number=device_js.serial_number,
                        is_defective=device_js.is_defective,
                    )
                    self.session_db.add(new_device_db)
                    new_report_db = ReportDB(
                        device_id=new_device_db.id,
                        device=new_device_db,
                        ticket_id=new_ticket_db.id,
                        ticket=new_ticket_db,
                    )
                    self.session_db.add(new_report_db)
                await self.session_db.flush()
                logger.info(
                    f"{self.log_prefix}Successfully closed and saved ticket "
                    f"{new_ticket_db.ticket_number} with {len(device_js_list)} devices."
                )
                return True
            except Exception as e:
                logger.error(
                    f"{self.log_prefix}Failed to close ticket due to database error: {e}",
                    exc_info=True,
                )
        try:
            new_ticket_db = TicketDB(
                ticket_number=self.state.ticket_number,
                contract_number=self.state.contract_number,
                user_id=self.user_db.id,  # Or user=self.user_db if MappedAsDataclass handles it
                user=self.user_db,
                reports=[],
            )
            self.session_db.add(new_ticket_db)
        except Exception as e:
            logger.error(
                f"{self.log_prefix}Failed to close ticket due to database error: {e}",
                exc_info=True,
            )
        await self.session_db.rollback()
        return False

    async def old_close_ticket(self) -> bool:
        if self.state is None:
            raise ValueError("'self.state' cannot be None at this point.")
        if not self.state.devices_list:
            error_msg = (
                f"{self.log_prefix}CRITICAL: Attempting to close "
                "a ticket with no devices. You shouldn't see this."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        if not self.state.ticket_number or not self.state.contract_number:
            logger.error(
                f"{self.log_prefix}Cannot close ticket: ticket_number or "
                "contract_number is missing from state."
            )
            return False
        device_js_list: list[DeviceJS] = self.state.devices_list
        device_type_db_dict: dict[DeviceTypeName, DeviceTypeDB] = {}
        for type_name_enum in DeviceTypeName:
            device_type_db: DeviceTypeDB | None = await self.session_db.scalar(
                select(DeviceTypeDB).where(DeviceTypeDB.name == type_name_enum)
            )
            if device_type_db is not None:
                device_type_db_dict[type_name_enum] = device_type_db
            else:
                error_msg = (
                    f"{self.log_prefix}CRITICAL: Database is missing an entry "
                    f"for essential device type '{type_name_enum.name}'. "
                    "The application cannot proceed with ticket closure."
                )
                logger.error(error_msg)
                raise ValueError(error_msg)
        try:
            new_ticket_db = TicketDB(
                ticket_number=self.state.ticket_number,
                contract_number=self.state.contract_number,
                user_id=self.user_db.id,  # Or user=self.user_db if MappedAsDataclass handles it
                user=self.user_db,
                reports=[],
            )
            self.session_db.add(new_ticket_db)
            for device_js in device_js_list:
                if device_js.type is None or device_js.serial_number is None:
                    error_msg = (
                        f"{self.log_prefix}CRITICAL: DeviceJS object "
                        "is incomplete (missing type or serial_number) "
                        f"for ticket {self.state.ticket_number}."
                    )
                    logger.error(error_msg)
                    await self.session_db.rollback()
                    return False
                device_type_for_db = device_type_db_dict[device_js.type]
                new_device_db = DeviceDB(
                    type_id=device_type_for_db.id,
                    type=device_type_for_db,
                    serial_number=device_js.serial_number,
                    is_defective=device_js.is_defective,
                )
                self.session_db.add(new_device_db)
                new_report_db = ReportDB(
                    device_id=new_device_db.id,
                    device=new_device_db,
                    ticket_id=new_ticket_db.id,
                    ticket=new_ticket_db,
                )
                self.session_db.add(new_report_db)
            await self.session_db.flush()
            logger.info(
                f"{self.log_prefix}Successfully closed and saved ticket "
                f"{new_ticket_db.ticket_number} with {len(device_js_list)} devices."
            )
            return True
        except Exception as e:
            logger.error(
                f"{self.log_prefix}Failed to close ticket due to database error: {e}",
                exc_info=True,
            )
            await self.session_db.rollback()
            return False

    def _build_pick_confirm_quit_without_saving(
        self, text: str = f"{String.ARE_YOU_SURE_YOU_WANT_TO_QUIT_WITHOUT_SAVING}"
    ) -> SendMessageTG:
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(
                inline_keyboard=[
                    [
                        InlineKeyboardButtonTG(
                            text=String.CONFIRM_QUIT_BTN,
                            callback_data=CallbackData.CONFIRM_QUIT_BTN,
                        ),
                        InlineKeyboardButtonTG(
                            text=String.CHANGED_MY_MIND_BTN,
                            callback_data=CallbackData.CHANGED_MY_MIND_BTN,
                        ),
                    ],
                ]
            ),
        )

    def _helper_mainmenu_keyboard_array(self) -> list[list[InlineKeyboardButtonTG]]:
        inline_keyboard_array = []
        if self.user_db.is_engineer:
            inline_keyboard_array.append(
                [
                    InlineKeyboardButtonTG(
                        text=String.CLOSE_TICKET_BTN,
                        callback_data=CallbackData.ENTER_TICKET_NUMBER,
                    )
                ],
            )
            inline_keyboard_array.append(
                [
                    InlineKeyboardButtonTG(
                        text=String.TICKETS_HISTORY_BTN,
                        callback_data=CallbackData.TICKETS_HISTORY_BTN,
                    ),
                    InlineKeyboardButtonTG(
                        text=String.WRITEOFF_DEVICES_BTN,
                        callback_data=CallbackData.WRITEOFF_DEVICES_BTN,
                    ),
                ],
            )
        if self.user_db.is_manager:
            inline_keyboard_array.append(
                [
                    InlineKeyboardButtonTG(
                        text=String.FORM_REPORT_BTN,
                        callback_data=CallbackData.FORM_REPORT_BTN,
                    )
                ],
            )
            if self.user_db.is_hiring:
                inline_keyboard_array.append(
                    [
                        InlineKeyboardButtonTG(
                            text=String.DISABLE_HIRING_BTN,
                            callback_data=CallbackData.DISABLE_HIRING_BTN,
                        )
                    ],
                )
            else:
                inline_keyboard_array.append(
                    [
                        InlineKeyboardButtonTG(
                            text=String.ENABLE_HIRING_BTN,
                            callback_data=CallbackData.ENABLE_HIRING_BTN,
                        )
                    ],
                )
        return inline_keyboard_array


# if __name__ == "__ma1in__":

#     def trash(self):
#         methods_tg_list = []
#         elif self.state.action == Action.EDIT_INSTALL_OR_RETURN:
#             logger.info(
#                 f"{self.log_prefix}Awaiting changing install or return choice to be made."
#             )
#             if self.state.device_index is None:
#                 raise ValueError("device_index cannot be None at this point.")
#             if isinstance(self.update_tg, CallbackQueryUpdateTG):
#                 expected_callback_data = [
#                     CallbackData.INSTALL_DEVICE_BTN,
#                     CallbackData.RETURN_DEVICE_BTN,
#                 ]
#                 data = self.update_tg.callback_query.data
#                 try:
#                     received_callback_data = CallbackData(data)
#                     if received_callback_data in expected_callback_data:
#                         if received_callback_data == CallbackData.INSTALL_DEVICE_BTN:
#                             is_defective = False
#                         elif received_callback_data == CallbackData.RETURN_DEVICE_BTN:
#                             is_defective = True
#                         self.next_state = StateJS(
#                             action=Action.PICK_DEVICE_ACTION,
#                             script=self.state.script,
#                             devices_list=self.state.devices_list,
#                             device_index=self.state.device_index,
#                             ticket_number=self.state.ticket_number,
#                             contract_number=self.state.contract_number,
#                         )
#                         device_index = self.next_state.device_index
#                         list_length = len(self.next_state.devices_list)
#                         if device_index == list_length:
#                             device = DeviceJS(
#                                 is_defective=is_defective, type=None, serial_number=None
#                             )
#                             self.next_state.devices_list.append(device)
#                         elif device_index < list_length:
#                             self.next_state.devices_list[
#                                 device_index
#                             ].is_defective = is_defective
#                         else:
#                             error_msg = (
#                                 f"{self.log_prefix}Error: "
#                                 f"device_index={device_index} > "
#                                 f"list_length={list_length}. "
#                                 f"Expected: device_index <= list_length."
#                             )
#                             logger.error(error_msg)
#                             raise ValueError(error_msg)
#                         methods_tg_list.append(
#                             self._archive_choice_method_tg(
#                                 String[received_callback_data.name]
#                             )
#                         )
#                         methods_tg_list.append(
#                             self.pick_device_action(f"{String.PICK_DEVICE_ACTION}.")
#                         )
#                     else:
#                         raise ValueError
#                 except ValueError:
#                     logger.info(
#                         f"{self.log_prefix}Received invalid callback "
#                         f"data='{data}' for device action selection."
#                     )
#                     methods_tg_list.append(
#                         self._pick_install_or_return(
#                             f"{String.GOT_UNEXPECTED_DATA}. "
#                             f"{String.PICK_INSTALL_OR_RETURN}."
#                         )
#                     )
#             elif isinstance(self.update_tg, MessageUpdateTG):
#                 logger.info(
#                     f"{self.log_prefix}User {self.user_db.full_name} "
#                     "responded with message while callback data "
#                     "was awaited."
#                 )
#                 methods_tg_list.append(
#                     self._pick_install_or_return(
#                         f"{String.DEVICE_ACTION_WAS_NOT_PICKED}. "
#                         f"{String.PICK_INSTALL_OR_RETURN}."
#                     )
#                 )
#         elif self.state.action == Action.EDIT_DEVICE_TYPE:
#             logger.info(
#                 f"{self.log_prefix}Awaiting changing device type choice to be made."
#             )
#             if self.state.device_index is None:
#                 raise ValueError("device_index cannot be None at this point.")
#             if isinstance(self.update_tg, CallbackQueryUpdateTG):
#                 expected_callback_data = [
#                     CallbackData.IP_DEVICE,
#                     CallbackData.TVE_DEVICE,
#                     CallbackData.ROUTER,
#                 ]
#                 data = self.update_tg.callback_query.data
#                 try:
#                     received_callback_data = CallbackData(data)
#                     if received_callback_data in expected_callback_data:
#                         self.next_state = StateJS(
#                             action=Action.PICK_DEVICE_ACTION,
#                             script=self.state.script,
#                             devices_list=self.state.devices_list,
#                             device_index=self.state.device_index,
#                             ticket_number=self.state.ticket_number,
#                             contract_number=self.state.contract_number,
#                         )
#                         device_index = self.next_state.device_index
#                         device_type = DeviceTypeName[received_callback_data.name]
#                         if self.next_state.devices_list[device_index].type is not None:
#                             self.next_state.devices_list[
#                                 device_index
#                             ].type = device_type
#                         else:
#                             existing_type = self.next_state.devices_list[
#                                 device_index
#                             ].type
#                             error_msg = (
#                                 f"{self.log_prefix}Error: Device with "
#                                 f"index={device_index} had type=None "
#                                 "prior to editing."
#                             )
#                             logger.error(error_msg)
#                             raise ValueError(error_msg)
#                         methods_tg_list.append(
#                             self._archive_choice_method_tg(
#                                 String[received_callback_data.name]
#                             )
#                         )
#                         methods_tg_list.append(
#                             self.pick_device_action(f"{String.PICK_DEVICE_ACTION}.")
#                         )
#                     else:
#                         raise ValueError
#                 except ValueError:
#                     logger.info(
#                         f"{self.log_prefix}Received invalid callback "
#                         f"data='{data}' for device type selection."
#                     )
#                     methods_tg_list.append(
#                         self._pick_device_type(
#                             f"{String.GOT_UNEXPECTED_DATA}. "
#                             f"{String.PICK_DEVICE_TYPE} "
#                             f"{String.FROM_OPTIONS_BELOW}."
#                         )
#                     )
#             elif isinstance(self.update_tg, MessageUpdateTG):
#                 logger.info(
#                     f"{self.log_prefix}User {self.user_db.full_name} "
#                     "responded with message while callback data "
#                     "was awaited."
#                 )
#                 methods_tg_list.append(
#                     self._pick_device_type(
#                         f"{String.DEVICE_TYPE_WAS_NOT_PICKED}. "
#                         f"{String.PICK_DEVICE_TYPE} "
#                         f"{String.FROM_OPTIONS_BELOW}."
#                     )
#                 )
#         elif self.state.action == Action.EDIT_SERIAL_NUMBER:
#             logger.info(f"{self.log_prefix}Awaiting new device serial number.")
#             if self.state.device_index is None:
#                 raise ValueError(
#                     "'self.state.device_index' cannot be None at this point."
#                 )
#             if (
#                 isinstance(self.update_tg, MessageUpdateTG)
#                 and self.update_tg.message.text
#             ):
#                 message_text = self.update_tg.message.text.upper()
#                 if re.fullmatch(r"[\dA-Z]+", message_text):
#                     logger.info(
#                         f"{self.log_prefix}Got correct new device "
#                         f"serial number: '{message_text}'."
#                     )
#                     self.next_state = StateJS(
#                         action=Action.PICK_DEVICE_ACTION,
#                         script=self.state.script,
#                         devices_list=self.state.devices_list,
#                         device_index=self.state.device_index,
#                         ticket_number=self.state.ticket_number,
#                         contract_number=self.state.contract_number,
#                     )
#                     device_index = self.state.device_index
#                     if (
#                         self.next_state.devices_list[device_index].serial_number
#                         is not None
#                     ):
#                         self.next_state.devices_list[
#                             device_index
#                         ].serial_number = message_text
#                     else:
#                         error_msg = (
#                             f"{self.log_prefix}Internal logic error: "
#                             "Device has no serial_number to edit."
#                         )
#                         logger.error(error_msg)
#                         raise ValueError(error_msg)
#                     methods_tg_list.append(
#                         self._send_text_message_tg(
#                             f"{String.SERIAL_NUMBER_WAS_CHANGED}."
#                         )
#                     )
#                     methods_tg_list.append(
#                         self.pick_device_action(f"{String.PICK_DEVICE_ACTION}.")
#                     )
#                 else:
#                     methods_tg_list.append(
#                         self._send_text_message_tg(
#                             f"{String.INCORRECT_SERIAL_NUMBER}. "
#                             f"{String.ENTER_NEW_SERIAL_NUMBER}."
#                         )
#                     )
#             elif isinstance(self.update_tg, CallbackQueryUpdateTG):
#                 methods_tg_list.append(
#                     self._send_text_message_tg(
#                         f"{String.GOT_DATA_NOT_SERIAL_NUMBER}. "
#                         f"{String.ENTER_NEW_SERIAL_NUMBER}."
#                     )
#                 )
#         return methods_tg_list
