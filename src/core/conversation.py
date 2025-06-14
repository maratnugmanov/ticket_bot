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
        # logger.debug(f"{self.log_prefix}User conversation state: {self.state}")
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
            Action.EDIT_DEVICE_TYPE: self._handle_action_edit_device_type,
            Action.EDIT_INSTALL_OR_RETURN: self._handle_action_edit_install_or_return,
            Action.EDIT_SERIAL_NUMBER: self._handle_action_edit_serial_number,
            Action.CONFIRM_CLOSE_TICKET: self._handle_pick_confirm_close_ticket,
            Action.CONFIRM_QUIT_WITHOUT_SAVING: self._handle_pick_confirm_quit_ticket_without_saving,
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
                f"{update_tg._log}CRITICAL: Could not extract Telegram "
                "user from supported update types "
                "(private message/callback)."
            )
            return None
        user_db: UserDB | None = await session.scalar(
            select(UserDB)
            .where(UserDB.telegram_uid == user_tg.id)
            .options(
                selectinload(UserDB.roles),
                selectinload(UserDB.current_ticket)
                .selectinload(TicketDB.contract)
                .selectinload(ContractDB.tickets),
                selectinload(UserDB.current_ticket)
                .selectinload(TicketDB.devices)
                .selectinload(DeviceDB.type),
            )
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
                    f"{update_tg._log}CRITICAL: Default role "
                    f"'{RoleName.GUEST}' not found in the "
                    "database. Cannot create new user instance."
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
                        f"{update_tg._log}CRITICAL: Default role "
                        f"'{RoleName.GUEST}' not found in the "
                        "database. Cannot create new user instance."
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
                        "Telegram API Error Details for "
                        f"method '{method_tg._url}': "
                        f"error_code='{error_tg.error_code}', "
                        f"description='{error_tg.description}'"
                    )
                    return error_tg  # Return the response even on error status
                except (ValidationError, Exception) as error_parsing_error:
                    logger.error(
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
            logger.info(f"{self.log_prefix}Bot is idle.")
        else:
            if self.state.script == Script.INITIAL_DATA:
                success = await self._make_delivery(self._state_action_conversation)
                if success:
                    if self.next_state is not None:
                        logger.info(
                            f"{self.log_prefix}Advancing to Action '{self.next_state.action.name}'."
                        )
                    elif self.state is not None:
                        logger.info(
                            f"{self.log_prefix}Still on Action '{self.state.action.name}'."
                        )
        return success

    def _stateless_conversation(self) -> list[MethodTG]:
        logger.info(
            f"{self.log_prefix}Starting new conversation with {self.user_db.full_name}."
        )
        if self.state is not None:
            error_msg = "'self.state' should be None at this point."
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
                self._build_stateless_mainmenu_message(f"{String.PICK_A_FUNCTION}.")
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
                    logger.info(
                        f"{self.log_prefix}Calling sync handler for "
                        f"{received_callback_data.__class__.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    methods_tg_list.extend(callback_handler(chat_id, message_id))
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
            error_msg = "'self.state' cannot be None at this point."
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
                methods_tg_list.extend(await action_handler(self.state))
            else:
                logger.info(
                    f"{self.log_prefix}Calling sync handler for "
                    f"{self.state.action.__class__.__name__} "
                    f"'{self.state.action.value}'."
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
        logger.info(f"{self.log_prefix}Initiating ticket creation.")
        methods_tg_list: list[MethodTG] = []
        self.next_state = StateJS(
            action=Action.ENTER_TICKET_NUMBER,
            script=Script.INITIAL_DATA,
        )
        methods_tg_list.append(
            self._build_edit_to_callback_button_text(),
        )
        methods_tg_list.append(
            self._build_new_text_message(f"{String.ENTER_TICKET_NUMBER}."),
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
            inline_keyboard=self._helper_mainmenu_keyboard_array()
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
            inline_keyboard=self._helper_mainmenu_keyboard_array()
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
            self._build_stateless_mainmenu_message(
                f"{String.GOT_UNEXPECTED_DATA}. "
                f"{String.PICK_A_FUNCTION} {String.FROM_OPTIONS_BELOW}."
            )
        )
        return methods_tg_list

    async def _handle_action_enter_ticket_number(
        self, state: StateJS
    ) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting ticket number.")
        current_ticket = self.user_db.current_ticket
        if current_ticket:
            error_msg = (
                f"{self.log_prefix}"
                f"{self.user_db.full_name} "
                "is already working on a ticket "
                f"number={current_ticket.number} "
                f"id={current_ticket.id}. "
                "Cannot create a new ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text
                if re.fullmatch(r"\d+", message_text) and message_text != "0":
                    logger.info(
                        f"{self.log_prefix}Got correct ticket number: '{message_text}'."
                    )
                    ticket_number = int(message_text)
                    self.next_state = state.model_copy(deep=True)
                    self.next_state.action = Action.ENTER_CONTRACT_NUMBER
                    new_ticket = TicketDB(
                        number=ticket_number,
                        user_id=self.user_db.id,
                        locked_by_user_id=self.user_db.id,
                    )
                    self.user_db.current_ticket = new_ticket
                    await self.session.flush()
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

    async def _handle_action_enter_contract_number(
        self, state: StateJS
    ) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting contract number.")
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text
                if re.fullmatch(r"\d+", message_text) and message_text != "0":
                    logger.info(
                        f"{self.log_prefix}Got correct "
                        f"contract number: '{message_text}'."
                    )
                    contract_number = int(message_text)
                    self.next_state = state.model_copy(deep=True)
                    self.next_state.action = Action.PICK_DEVICE_TYPE
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
                        current_ticket.contract = contract_exist
                    else:
                        logger.info(
                            f"{self.log_prefix}Contract "
                            f"number={contract_number} was not found "
                            "in the database and will be added."
                        )
                        new_contract = ContractDB(number=contract_number)
                        current_ticket.contract = new_contract
                    await self.session.flush()
                    methods_tg_list.append(
                        await self._build_pick_device_type_message(
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

    async def _handle_action_pick_device_type(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting device type choice to be made.")
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                logger.info(
                    f"{self.log_prefix}Got {CallbackData.__name__} "
                    f"'{received_callback_data.value}'."
                )
                if received_callback_data.name not in DeviceTypeName.__members__:
                    logger.info(
                        f"{self.log_prefix}{CallbackData.__name__} "
                        f"'{received_callback_data.value}' "
                        "doesn't match any "
                        f"{DeviceTypeName.__name__}."
                    )
                    raise ValueError
                device_type_name = DeviceTypeName[received_callback_data.name]
                logger.info(
                    f"{self.log_prefix}{CallbackData.__name__} "
                    f"'{received_callback_data.value}' "
                    f"matches {DeviceTypeName.__name__} "
                    f"'{device_type_name.name}'."
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
                        await self._build_pick_device_type_message(
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
                        await self._build_pick_device_type_message(
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
                    self.next_state = state.model_copy(deep=True)
                    devices_list = current_ticket.devices
                    device_index = self.next_state.device_index
                    device_list_length = len(devices_list)
                    if device_index == device_list_length:
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
                    elif 0 <= device_index < device_list_length:
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
                    else:
                        error_msg = (
                            f"{self.log_prefix}Error: "
                            f"device_index={device_index} and "
                            f"device_list_length={device_list_length}. "
                            "Expected: "
                            "0 <= device_index <= device_list_length."
                        )
                        logger.error(error_msg)
                        raise IndexError(error_msg)
                    await self.session.flush()
                    if device_type.is_disposable:
                        logger.info(
                            f"{DeviceTypeDB.__name__} "
                            f"'{device_type.name.name}' "
                            "is disposable. Install or return step "
                            "will be skipped."
                        )
                        device.removal = False
                        if device_type.has_serial_number:
                            logger.info(
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' "
                                "has serial number parameter."
                            )
                            self.next_state.action = Action.ENTER_SERIAL_NUMBER
                            methods_tg_list.append(
                                self._build_new_text_message(
                                    f"{String.ENTER_SERIAL_NUMBER}."
                                )
                            )
                        else:
                            logger.info(
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' "
                                "doesn't have serial number parameter. "
                                "Serial number step will be skipped."
                            )
                            self.next_state.action = Action.PICK_TICKET_ACTION
                            methods_tg_list.append(
                                self._build_pick_ticket_action_message(
                                    f"{String.PICK_TICKET_ACTION}."
                                )
                            )
                    else:
                        logger.info(
                            f"{DeviceTypeDB.__name__} "
                            f"'{device_type.name.name}' "
                            "is not disposable."
                        )
                        self.next_state.action = Action.PICK_INSTALL_OR_RETURN
                        methods_tg_list.append(
                            self._build_pick_install_or_return_message(
                                f"{String.PICK_INSTALL_OR_RETURN}."
                            )
                        )
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Got invalid callback data "
                    f"'{raw_data}' for current device type selection."
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
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                await self._build_pick_device_type_message(
                    f"{String.DEVICE_TYPE_WAS_NOT_PICKED}. "
                    f"{String.PICK_DEVICE_TYPE} "
                    f"{String.FROM_OPTIONS_BELOW}."
                )
            )
        return methods_tg_list

    async def _handle_action_pick_install_or_return(
        self, state: StateJS
    ) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting install or return choice to be made.")
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.INSTALL_DEVICE_BTN,
                CallbackData.RETURN_DEVICE_BTN,
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
                    if received_callback_data == CallbackData.INSTALL_DEVICE_BTN:
                        logger.info(
                            f"{self.log_prefix}{CallbackData.__name__} "
                            f"'{received_callback_data.value}' "
                            "matches 'install' option "
                            "(removal=False)."
                        )
                        removal = False
                    elif received_callback_data == CallbackData.RETURN_DEVICE_BTN:
                        logger.info(
                            f"{self.log_prefix}{CallbackData.__name__} "
                            f"'{received_callback_data.value}' "
                            "matches 'return' option "
                            "(removal=True)."
                        )
                        removal = True
                    else:
                        error_msg = (
                            f"{CallbackData.__name__} "
                            f"{received_callback_data.value}"
                            "is in expected callback list, "
                            "but somehow doesn't match anything."
                        )
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                    methods_tg_list.append(self._build_edit_to_callback_button_text())
                    self.next_state = state.model_copy(deep=True)
                    devices_list = current_ticket.devices
                    device_index = self.next_state.device_index
                    device_list_length = len(devices_list)
                    if 0 <= device_index < device_list_length:
                        logger.info(
                            f"{self.log_prefix}Working with "
                            f"{DeviceDB.__name__} "
                            f"at devices[{device_index}]. Setting "
                            f"'removal' flag to '{removal}'."
                        )
                        device = devices_list[device_index]
                        device.removal = removal
                        await self.session.flush()
                    else:
                        error_msg = (
                            f"{self.log_prefix}Error: "
                            f"device_index={device_index} and "
                            f"device_list_length={device_list_length}. "
                            "Expected: "
                            "0 <= device_index < device_list_length."
                        )
                        logger.error(error_msg)
                        raise IndexError(error_msg)
                    device_type = device.type
                    if device_type.has_serial_number:
                        logger.info(
                            f"{DeviceTypeDB.__name__} "
                            f"'{device_type.name.name}' "
                            "has serial number parameter."
                        )
                        self.next_state.action = Action.ENTER_SERIAL_NUMBER
                        methods_tg_list.append(
                            self._build_new_text_message(
                                f"{String.ENTER_SERIAL_NUMBER}."
                            )
                        )
                    else:
                        logger.info(
                            f"{DeviceTypeDB.__name__} "
                            f"'{device_type.name.name}' "
                            "doesn't have serial number parameter. "
                            "Serial number step will be skipped."
                        )
                        self.next_state.action = Action.PICK_TICKET_ACTION
                        self.next_state.device_index = 0
                        methods_tg_list.append(
                            self._build_pick_ticket_action_message(
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

    def _handle_action_enter_serial_number(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting device serial number.")
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text.upper()
                if re.fullmatch(r"[\dA-Z]+", message_text) and message_text != "0":
                    logger.info(
                        f"{self.log_prefix}Got correct device "
                        "serial number (forced uppercase): "
                        f"'{message_text}'."
                    )
                    self.next_state = state.model_copy(deep=True)
                    self.next_state.action = Action.PICK_TICKET_ACTION
                    devices_list = current_ticket.devices
                    device_index = self.next_state.device_index
                    device_list_length = len(devices_list)
                    if 0 <= device_index < device_list_length:
                        logger.info(
                            f"{self.log_prefix}Working with "
                            f"{DeviceDB.__name__} "
                            f"at devices[{device_index}]. Setting "
                            f"serial number to '{message_text}'."
                        )
                        device = devices_list[device_index]
                        device.serial_number = message_text
                    else:
                        error_msg = (
                            f"{self.log_prefix}Error: "
                            f"device_index={device_index} and "
                            f"device_list_length={device_list_length}. "
                            "Expected: "
                            "0 <= device_index < device_list_length."
                        )
                        logger.error(error_msg)
                        raise IndexError(error_msg)
                    methods_tg_list.append(
                        self._build_pick_ticket_action_message(
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

    async def _handle_action_pick_ticket_action(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting ticket menu choice to be made.")
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.EDIT_TICKET_NUMBER,
                CallbackData.EDIT_CONTRACT_NUMBER,
                CallbackData.QUIT_WITHOUT_SAVING_BTN,
            ]
            device_list_length = len(current_ticket.devices)
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
                if received_callback_data in expected_callback_data:
                    logger.info(
                        f"{self.log_prefix}Got expected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
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
                            error_msg = (
                                f"{self.log_prefix}"
                                f"{CallbackData.__name__} "
                                f"'{received_callback_data.value}' "
                                "doesn't end with an integer."
                            )
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
                    self._build_pick_ticket_action_message(
                        f"{String.GOT_UNEXPECTED_DATA}. {String.PICK_TICKET_ACTION}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                self._build_pick_ticket_action_message(
                    f"{String.TICKET_ACTION_WAS_NOT_PICKED}. "
                    f"{String.PICK_TICKET_ACTION}."
                )
            )
        return methods_tg_list

    def _handle_action_edit_ticket_number(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting new ticket number.")
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text
                if re.fullmatch(r"\d+", message_text) and message_text != "0":
                    logger.info(
                        f"{self.log_prefix}Got correct new "
                        f"ticket number: '{message_text}'."
                    )
                    new_ticket_number = int(message_text)
                    self.next_state = state.model_copy(deep=True)
                    self.next_state.action = Action.PICK_TICKET_ACTION
                    if current_ticket.number != new_ticket_number:
                        logger.info(
                            f"{self.log_prefix}New ticket "
                            f"number={new_ticket_number} "
                            "is different from old ticket "
                            f"number={current_ticket.number}. "
                            "Applying change."
                        )
                        current_ticket.number = new_ticket_number
                        methods_tg_list.append(
                            self._build_pick_ticket_action_message(
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
                        methods_tg_list.append(
                            self._build_pick_ticket_action_message(
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

    async def _handle_action_edit_contract_number(
        self, state: StateJS
    ) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting new contract number.")
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        if current_ticket.contract is None:
            error_msg = (
                f"{self.log_prefix}Ticket "
                f"number={current_ticket.number} "
                f"id={current_ticket.id} is missing "
                "a contract to work with."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text
                if re.fullmatch(r"\d+", message_text) and message_text != "0":
                    logger.info(
                        f"{self.log_prefix}Got correct new "
                        f"contract number: '{message_text}'."
                    )
                    new_contract_number = int(message_text)
                    self.next_state = state.model_copy(deep=True)
                    self.next_state.action = Action.PICK_TICKET_ACTION
                    if current_ticket.contract.number != new_contract_number:
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
                        methods_tg_list.append(
                            self._build_pick_ticket_action_message(
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
                        methods_tg_list.append(
                            self._build_pick_ticket_action_message(
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

    async def _handle_action_pick_device_action(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting device menu choice to be made.")
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.EDIT_DEVICE_TYPE,
                CallbackData.DELETE_DEVICE_BTN,
            ]
            devices_list = current_ticket.devices
            device_index = state.device_index
            device_list_length = len(devices_list)
            if 0 <= device_index < device_list_length:
                logger.info(
                    f"{self.log_prefix}Working with "
                    f"{DeviceDB.__name__} "
                    f"at devices[{device_index}]."
                )
                device = devices_list[device_index]
            else:
                error_msg = (
                    f"{self.log_prefix}Error: "
                    f"device_index={device_index} and "
                    f"device_list_length={device_list_length}. "
                    "Expected: 0 <= device_index < device_list_length."
                )
                logger.error(error_msg)
                raise IndexError(error_msg)
            if not device.type.is_disposable:
                if device.removal is True:
                    expected_callback_data.append(CallbackData.RETURN_DEVICE_BTN)
                elif device.removal is False:
                    expected_callback_data.append(CallbackData.INSTALL_DEVICE_BTN)
                else:
                    raise ValueError(
                        f"{DeviceDB.__name__} is not disposable "
                        f"but 'removal' is '{device.removal}'."
                    )
            if device.type.has_serial_number:
                expected_callback_data.append(CallbackData.EDIT_SERIAL_NUMBER)
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
                    self.next_state = state.model_copy(deep=True)
                    if received_callback_data == CallbackData.EDIT_DEVICE_TYPE:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        self.next_state.action = Action.EDIT_DEVICE_TYPE
                        methods_tg_list.append(
                            await self._build_pick_device_type_message(
                                f"{String.PICK_NEW_DEVICE_TYPE}."
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
                        await self.session.delete(device)
                        await self.session.flush()
                        await self.session.refresh(current_ticket, ["devices"])
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
                    self._build_pick_device_action_message(
                        f"{String.GOT_UNEXPECTED_DATA}. {String.PICK_DEVICE_ACTION}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                self._build_pick_device_action_message(
                    f"{String.DEVICE_ACTION_WAS_NOT_PICKED}. "
                    f"{String.PICK_DEVICE_ACTION}."
                )
            )
        return methods_tg_list

    async def _handle_action_edit_device_type(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting new device type choice to be made.")
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            raw_data = self.update_tg.callback_query.data
            try:
                received_callback_data = CallbackData(raw_data)
                logger.info(
                    f"{self.log_prefix}Got {CallbackData.__name__} "
                    f"'{received_callback_data.value}'."
                )
                if received_callback_data.name not in DeviceTypeName.__members__:
                    logger.info(
                        f"{self.log_prefix}{CallbackData.__name__} "
                        f"'{received_callback_data.value}' "
                        "doesn't match any "
                        f"{DeviceTypeName.__name__}."
                    )
                    raise ValueError
                device_type_name = DeviceTypeName[received_callback_data.name]
                logger.info(
                    f"{self.log_prefix}{CallbackData.__name__} "
                    f"'{received_callback_data.value}' "
                    f"matches {DeviceTypeName.__name__} "
                    f"'{device_type_name.name}'."
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
                        await self._build_pick_device_type_message(
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
                        await self._build_pick_device_type_message(
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
                    self.next_state = state.model_copy(deep=True)
                    self.next_state.action = Action.PICK_DEVICE_ACTION
                    devices_list = current_ticket.devices
                    device_index = self.next_state.device_index
                    device_list_length = len(devices_list)
                    if 0 <= device_index < device_list_length:
                        logger.info(
                            f"{self.log_prefix}Working with "
                            f"{DeviceDB.__name__} at "
                            f"devices[{device_index}]."
                        )
                        device = devices_list[device_index]
                    else:
                        error_msg = (
                            f"{self.log_prefix}Error: "
                            f"device_index={device_index} and "
                            f"device_list_length={device_list_length}. "
                            "Expected: "
                            "0 <= device_index < device_list_length."
                        )
                        logger.error(error_msg)
                        raise IndexError(error_msg)
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
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' is "
                                "disposable. Install action set."
                            )
                            device.removal = False
                        else:
                            logger.info(
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' "
                                "is not disposable. "
                                "Keeping install or return as is."
                            )
                        if device_type.has_serial_number:
                            logger.info(
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' "
                                "has serial number parameter. "
                                "Keeping serial number intact."
                            )
                        else:
                            logger.info(
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' "
                                "doesn't have serial number parameter. "
                                "Setting serial number to None."
                            )
                            device.serial_number = None
                        methods_tg_list.append(
                            self._build_pick_device_action_message(
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
                        methods_tg_list.append(
                            self._build_pick_device_action_message(
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
                    await self._build_pick_device_type_message(
                        f"{String.GOT_UNEXPECTED_DATA}. "
                        f"{String.PICK_DEVICE_TYPE} "
                        f"{String.FROM_OPTIONS_BELOW}."
                    )
                )
        elif isinstance(self.update_tg, MessageUpdateTG):
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                await self._build_pick_device_type_message(
                    f"{String.DEVICE_TYPE_WAS_NOT_PICKED}. "
                    f"{String.PICK_DEVICE_TYPE} "
                    f"{String.FROM_OPTIONS_BELOW}."
                )
            )
        return methods_tg_list

    async def _handle_action_edit_install_or_return(
        self, state: StateJS
    ) -> list[MethodTG]:
        logger.info(
            f"{self.log_prefix}Awaiting new install or return choice to be made."
        )
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.INSTALL_DEVICE_BTN,
                CallbackData.RETURN_DEVICE_BTN,
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
                    if received_callback_data == CallbackData.INSTALL_DEVICE_BTN:
                        logger.info(
                            f"{self.log_prefix}{CallbackData.__name__} "
                            f"'{received_callback_data.value}' "
                            "matches 'install' option "
                            "(removal=False)."
                        )
                        removal = False
                    elif received_callback_data == CallbackData.RETURN_DEVICE_BTN:
                        logger.info(
                            f"{self.log_prefix}{CallbackData.__name__} "
                            f"'{received_callback_data.value}' "
                            "matches 'return' option "
                            "(removal=True)."
                        )
                        removal = True
                    else:
                        error_msg = (
                            f"{CallbackData.__name__} "
                            f"{received_callback_data.value}"
                            "is in expected callback list, "
                            "but somehow doesn't match anything."
                        )
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                    methods_tg_list.append(self._build_edit_to_callback_button_text())
                    self.next_state = state.model_copy(deep=True)
                    self.next_state.action = Action.PICK_DEVICE_ACTION
                    devices_list = current_ticket.devices
                    device_index = self.next_state.device_index
                    device_list_length = len(devices_list)
                    if 0 <= device_index < device_list_length:
                        logger.info(
                            f"{self.log_prefix}Working with "
                            f"{DeviceDB.__name__} "
                            f"at devices[{device_index}]."
                        )
                        device = devices_list[device_index]
                    else:
                        error_msg = (
                            f"{self.log_prefix}Error: "
                            f"device_index={device_index} and "
                            f"device_list_length={device_list_length}. "
                            "Expected: "
                            "0 <= device_index < device_list_length."
                        )
                        logger.error(error_msg)
                        raise IndexError(error_msg)
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
                                f"{DeviceTypeDB.__name__} "
                                f"'{device_type.name.name}' "
                                "is disposable, install or return "
                                "selection not available."
                            )
                            logger.error(error_msg)
                            raise ValueError(error_msg)
                        methods_tg_list.append(
                            self._build_pick_device_action_message(
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
                        methods_tg_list.append(
                            self._build_pick_device_action_message(
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

    def _handle_action_edit_serial_number(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting new device serial number.")
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, MessageUpdateTG):
            if self.update_tg.message.text is not None:
                message_text = self.update_tg.message.text.upper()
                if re.fullmatch(r"[\dA-Z]+", message_text) and message_text != "0":
                    logger.info(
                        f"{self.log_prefix}Got correct new device "
                        "serial number (forced uppercase): "
                        f"'{message_text}'."
                    )
                    self.next_state = state.model_copy(deep=True)
                    self.next_state.action = Action.PICK_TICKET_ACTION
                    devices_list = current_ticket.devices
                    device_index = self.next_state.device_index
                    device_list_length = len(devices_list)
                    if 0 <= device_index < device_list_length:
                        logger.info(
                            f"{self.log_prefix}Working with "
                            f"{DeviceDB.__name__} "
                            f"at devices[{device_index}]."
                        )
                        device = devices_list[device_index]
                    else:
                        error_msg = (
                            f"{self.log_prefix}Error: "
                            f"device_index={device_index} and "
                            f"device_list_length={device_list_length}. "
                            "Expected: "
                            "0 <= device_index < device_list_length."
                        )
                        logger.error(error_msg)
                        raise IndexError(error_msg)
                    if device.serial_number != message_text:
                        logger.info(
                            f"{self.log_prefix}Device new "
                            f"serial_number={message_text} "
                            "is different from device old "
                            f"serial_number={device.serial_number}. "
                            "Applying change."
                        )
                        device.serial_number = message_text
                        methods_tg_list.append(
                            self._build_pick_device_action_message(
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
                        methods_tg_list.append(
                            self._build_pick_device_action_message(
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
                            f"{String.ENTER_SERIAL_NUMBER}."
                        )
                    )
            else:
                logger.info(f"{self.log_prefix}Didn't get new device serial number.")
                methods_tg_list.append(
                    self._build_new_text_message(
                        f"{String.INCORRECT_SERIAL_NUMBER}. "
                        f"{String.ENTER_SERIAL_NUMBER}."
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
                    f"{String.ENTER_SERIAL_NUMBER}."
                )
            )
        return methods_tg_list

    async def _handle_pick_confirm_close_ticket(self, state: StateJS) -> list[MethodTG]:
        logger.info(f"{self.log_prefix}Awaiting close ticket confirmation.")
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.CONFIRM_CLOSE_TICKET_BTN,
                CallbackData.CHANGED_MY_MIND_BTN,
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
                    if received_callback_data == CallbackData.CONFIRM_CLOSE_TICKET_BTN:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        ticket_number = current_ticket.number
                        total_devices = len(current_ticket.devices)
                        if total_devices == 1:
                            device_string = String.X_DEVICE
                        else:
                            device_string = String.X_DEVICES
                        ticket_closed = await self.close_ticket()
                        if ticket_closed:
                            self.next_state = None
                            self.user_db.state_json = None
                            methods_tg_list.append(
                                self._build_stateless_mainmenu_message(
                                    f"{String.YOU_CLOSED_TICKET} "
                                    f"{String.NUMBER_SYMBOL}"
                                    f"{ticket_number} "
                                    f"{String.WITH_X} "
                                    f"{total_devices} "
                                    f"{device_string}. "
                                    f"{String.PICK_A_FUNCTION}."
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
                    logger.info(
                        f"{self.log_prefix}Got unexpected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    raise ValueError
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Got invalid callback data "
                    f"'{raw_data}' for current ticket close "
                    "confirmation menu selection."
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
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
            methods_tg_list.append(
                self._build_pick_confirm_close_ticket_message(
                    f"{String.CLOSE_TICKET_ACTION_WAS_NOT_PICKED}. "
                    f"{String.CONFIRM_YOU_WANT_TO_CLOSE_TICKET}"
                )
            )
        return methods_tg_list

    async def _handle_pick_confirm_quit_ticket_without_saving(
        self, state: StateJS
    ) -> list[MethodTG]:
        logger.info(
            f"{self.log_prefix}Awaiting quit ticket without saving confirmation."
        )
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            expected_callback_data = [
                CallbackData.CONFIRM_QUIT_BTN,
                CallbackData.CHANGED_MY_MIND_BTN,
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
                    if received_callback_data == CallbackData.CONFIRM_QUIT_BTN:
                        methods_tg_list.append(
                            self._build_edit_to_callback_button_text()
                        )
                        await self.drop_current_ticket()
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
                    logger.info(
                        f"{self.log_prefix}Got unexpected "
                        f"{CallbackData.__name__} "
                        f"'{received_callback_data.value}'."
                    )
                    raise ValueError
            except ValueError:
                logger.info(
                    f"{self.log_prefix}Got invalid callback data "
                    f"'{raw_data}' for quit ticket without saving "
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
            logger.info(f"{self.log_prefix}Got message instead of callback data.")
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
                "This method only works with "
                f"{CallbackQueryUpdateTG.__name__} update type only."
            )
        if self.update_tg.callback_query.message.reply_markup is None:
            error_msg = "This method only works with inline keyboard attached."
            logger.error(error_msg)
            raise ValueError(error_msg)
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
                        f"for callback data '{callback_data}'."
                    )
                    break
            else:
                continue
            break
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

    async def _build_pick_device_type_message(
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
                error_msg = (
                    f"{self.log_prefix}Configuration error: Missing "
                    f"{String.__name__} or {CallbackData.__name__} "
                    f"enum member for {DeviceTypeName.__name__} "
                    f"'{device_type.name.name}'. Original error: {e}"
                )
                logger.error(error_msg)
                raise ValueError(error_msg) from e
        if not inline_keyboard:
            warning_msg = (
                f"{self.log_prefix}Warning: Not a single eligible "
                f"(active) {DeviceTypeDB.__name__} was found in the "
                f"database. Cannot build {DeviceTypeDB.__name__} "
                "selection keyboard."
            )
            logger.warning(warning_msg)
            await self.drop_current_ticket()
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
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        if not current_ticket.contract:
            error_msg = (
                "The ticket menu only works with ticket number and "
                "contract number already being filled in."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        ticket_number = current_ticket.number
        contract_number = current_ticket.contract.number
        devices_list = current_ticket.devices
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
            device_icon = "↪️" if device.removal else "✅"
            if not isinstance(device.type.name, DeviceTypeName):
                error_msg = (
                    f"{self.log_prefix}CRITICAL: device.type.name "
                    f"is not {DeviceTypeName.__name__}."
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
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        if current_ticket.contract is None:
            error_msg = (
                f"{self.log_prefix}Ticket "
                f"number={current_ticket.number} "
                f"id={current_ticket.id} is missing "
                "a contract to work with."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        devices_list = current_ticket.devices
        device_list_length = len(devices_list)
        if (
            self.next_state is not None
            and device_list_length > self.next_state.device_index
        ):
            device_index = self.next_state.device_index
        elif self.state is not None and device_list_length > self.state.device_index:
            device_index = self.state.device_index
        else:
            error_msg = (
                "The device menu only works with next_state/state "
                "having at least one device being filled in."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        inline_keyboard_array: list[list[InlineKeyboardButtonTG]] = []
        device = devices_list[device_index]
        device_type_name = String[device.type.name.name]
        device_serial_number_text = (
            device.serial_number
            if device.serial_number is not None
            else String.ENTER_SERIAL_NUMBER
        )
        if device.removal is None:
            error_msg = (
                f"{self.log_prefix}{DeviceDB.__name__} "
                f"type='{device.type.name.name}' "
                f"id={device.id} is missing "
                "'removal' bool status."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        device_type_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=f"{device_type_name} {String.EDIT}",
            callback_data=CallbackData.EDIT_DEVICE_TYPE,
        )
        if device.removal:
            device_action_text = f"{String.RETURN_DEVICE_BTN} {String.EDIT}"
            device_action_data = CallbackData.RETURN_DEVICE_BTN
        else:
            device_action_text = f"{String.INSTALL_DEVICE_BTN} {String.EDIT}"
            device_action_data = CallbackData.INSTALL_DEVICE_BTN
        device_action_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=device_action_text,
            callback_data=device_action_data,
        )
        serial_number_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=f"{device_serial_number_text} {String.EDIT}",
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
        inline_keyboard_array.append([device_type_button])
        if not device.type.is_disposable:
            inline_keyboard_array.append([device_action_button])
        if device.type.has_serial_number:
            inline_keyboard_array.append([serial_number_button])
        if (
            device.type.has_serial_number
            and device.serial_number is not None
            or not device.type.has_serial_number
        ):
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
            error_msg = "'self.state' cannot be None at this point."
            logger.error(error_msg)
            raise ValueError(error_msg)
        current_ticket = self.user_db.current_ticket
        if current_ticket is None:
            error_msg = (
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any ticket."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        if current_ticket.contract is None:
            error_msg = (
                f"{self.log_prefix}Cannot close ticket "
                f"number={current_ticket.number} "
                f"id={current_ticket.id}: contract is missing."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        devices_list = current_ticket.devices
        if not devices_list:
            error_msg = (
                f"{self.log_prefix}CRITICAL: Attempting to close "
                "a ticket with no devices. You shouldn't see this."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        for device in devices_list:
            try:
                log_prefix_val = f"{self.log_prefix}Ticket "
                f"number={current_ticket.number} "
                f"id={current_ticket.id}: Device validation: "
                if device.type.is_active is False:
                    raise ValueError(
                        f"{log_prefix_val}{DeviceTypeDB.__name__} "
                        f"'{device.type.name.name}' is inactive "
                        "but was assigned to device."
                    )
                if device.removal is None:
                    raise ValueError(
                        f"{log_prefix_val}Missing 'removal' flag "
                        "used for identifying install from return "
                        "action."
                    )
                if device.type.is_disposable and device.removal:
                    raise ValueError(
                        f"{log_prefix_val}{DeviceTypeDB.__name__} "
                        f"'{device.type.name.name}' is "
                        "disposable, but device flag 'removal' is "
                        f"{device.removal} (expected False)."
                    )
                if device.type.has_serial_number:
                    if device.serial_number is None:
                        raise ValueError(
                            f"{log_prefix_val}{DeviceTypeDB.__name__} "
                            f"'{device.type.name.name}' requires a "
                            "serial number, but device is missing it."
                        )
                else:
                    if device.serial_number is not None:
                        raise ValueError(
                            f"{log_prefix_val}{DeviceTypeDB.__name__} "
                            f"'{device.type.name.name}' does not "
                            "use a serial number, but one is provided "
                            f"('{device.serial_number}')."
                        )
            except ValueError as e:
                logger.error(str(e))
                return False
        for device in devices_list:
            if device.is_draft is True:
                device.is_draft = False
        ticket_id = current_ticket.id
        ticket_number = current_ticket.number
        if current_ticket.is_draft is True:
            current_ticket.is_draft = False
        self.user_db.current_ticket = None
        logger.info(
            f"{self.log_prefix}Successfully closed and saved "
            f"ticket number={ticket_number} id={ticket_id} "
            f"with {len(devices_list)} devices."
        )
        return True

    async def drop_current_ticket(self) -> bool:
        if self.state is None:
            error_msg = "'self.state' cannot be None at this point."
            logger.error(error_msg)
            raise ValueError(error_msg)
        if self.user_db.current_ticket is None:
            error_msg = "'user_db.current_ticket' cannot be None at this point."
            logger.error(error_msg)
            raise ValueError(error_msg)
        current_ticket = self.user_db.current_ticket
        current_ticket_id = self.user_db.current_ticket.id
        current_ticket_number = self.user_db.current_ticket.number
        current_contract = current_ticket.contract
        for device in current_ticket.devices.copy():
            if device.is_draft:
                logger.info(
                    f"{self.log_prefix}Marking draft device "
                    f"type='{device.type.name.name}' "
                    f"id={device.id} for deletion. "
                    "Associated with ticket "
                    f"number={current_ticket_number} "
                    f"id={current_ticket_id}."
                )
                await self.session.delete(device)
        if current_ticket.is_draft:
            logger.info(
                f"{self.log_prefix}Marking draft ticket "
                f"number={current_ticket_number} "
                f"id={current_ticket_id} for deletion."
            )
            await self.session.delete(current_ticket)
        else:
            logger.info(
                f"{self.log_prefix}Unlocking ticket "
                f"number={current_ticket_number} "
                f"id={current_ticket_id}. "
                "Reverting draft additions/changes."
            )
            current_ticket.locked_by_user_id = None
        if current_contract is not None:
            current_contract_id = current_contract.id
            current_contract_number = current_contract.number
            await self.session.flush()
            await self.session.refresh(current_contract, ["tickets"])
            if not current_contract.tickets:
                logger.info(
                    f"{self.log_prefix}Contract "
                    f"number={current_contract_number} "
                    f"id={current_contract_id} was associated "
                    "only with the current ticket "
                    f"number={current_ticket_number} "
                    f"id={current_ticket_id} being deleted. "
                    "Marking contract for deletion."
                )
                await self.session.delete(current_contract)
            else:
                logger.info(
                    f"{self.log_prefix}Contract "
                    f"number={current_contract_number} "
                    f"id={current_contract_id} is still "
                    "associated with other ticket IDs: "
                    f"{[ticket.id for ticket in current_contract.tickets]}. "
                    "It will NOT be deleted."
                )
        self.user_db.current_ticket = None
        return True

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
