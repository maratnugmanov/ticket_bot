from __future__ import annotations
from typing import Callable, TypedDict
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
from src.core.models import DeviceJS, StateJS
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
from src.db.models import RoleDB, UserDB


class MethodsPack(TypedDict):
    edit_old_message: EditMessageTextTG | None
    send_new_message: SendMessageTG | None
    state: StateJS | None


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
        self.session_db: SessionDepDB = session_db
        self.user_db: UserDB = user_db
        self.state: StateJS | None = (
            StateJS.model_validate_json(user_db.state_json)
            if user_db.state_json
            else None
        )
        self.next_state: StateJS | None = None
        self.response_methods_list: list[MethodTG] = []
        logger.debug(
            f"Conversation with {self.user_db.full_name}, "
            f"Update #{self.update_tg.update_id} initialized."
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
            logger.debug(
                "Ignoring update: Could not extract Telegram User from "
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
            logger.debug(f"Guest {user_tg.full_name} is not registered.")
            hiring = await session_db.scalar(
                select(exists().where(UserDB.is_hiring == True))  # noqa: E712
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
            guest_role = await session_db.scalar(
                select(RoleDB).where(RoleDB.name == RoleName.GUEST)
            )
            if guest_role is None:
                error_message = (
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
            session_db.add(user_db)
            await session_db.flush()
            logger.debug(
                f"User DB {user_db.full_name} (ID: {user_db.id}) was "
                f"created with role '{RoleName.GUEST.name}' in the DB. "
                "It won't get any visible feedback to prevent "
                "unnecessary interactions with strangers from "
                "happening. Conversation returns None."
            )
            return None
        if len(user_db.roles) == 1:
            if guest_role is None:
                guest_role = await session_db.scalar(
                    select(RoleDB).where(RoleDB.name == RoleName.GUEST)
                )
                if guest_role is None:
                    error_message = (
                        f"CRITICAL: Default role '{RoleName.GUEST}' not "
                        "found in the DB. Cannot create new User DB."
                    )
                    logger.error(error_message)
                    raise ValueError(error_message)
            if user_db.roles[0].id == guest_role.id:
                logger.debug(
                    f"User DB {user_db.full_name} has only "
                    f"'{RoleName.GUEST}' role and won't get any reply."
                )
                return None
        logger.debug(f"Validated User DB {user_db.full_name} as employee.")
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
            logger.debug(f"Processing private message update from {user_tg.full_name}.")
        elif (
            isinstance(update_tg, CallbackQueryUpdateTG)
            and not update_tg.callback_query.from_.is_bot
            and update_tg.callback_query.message
            and update_tg.callback_query.message.from_.is_bot
            and update_tg.callback_query.message.from_.id == settings.bot_id
            and update_tg.callback_query.message.chat.type == "private"
        ):
            user_tg = update_tg.callback_query.from_
            logger.debug(f"Processing callback query update from {user_tg.full_name}.")
        return user_tg

    async def process(self) -> bool:
        success = False
        if self.state is None:
            logger.debug(f"Starting new conversation with {self.user_db.full_name}.")
            success = await self.make_delivery(self.get_stateless_conversation)
        else:
            logger.debug(
                f"Continuing existing conversation with {self.user_db.full_name}."
            )
            if self.state.script == Script.INITIAL_DATA:
                logger.debug(
                    f"Initial device conversation with {self.user_db.full_name}."
                )
                success = await self.make_delivery(self.get_device_conversation)
        return success

    async def make_delivery(
        self,
        method_generator: Callable[[], list[MethodTG]],
        not_exist_fix: bool = True,
    ) -> bool:
        response_tg: SuccessTG | ErrorTG | None
        method_tg_list = method_generator()
        last_method_tg_index = len(method_tg_list) - 1
        success = False
        for index, method_tg in enumerate(method_tg_list):
            response_tg = await self.post_method_tg(method_tg)
            if index == last_method_tg_index:
                if isinstance(response_tg, SuccessTG):
                    if isinstance(self.next_state, StateJS):
                        next_state_json = self.next_state.model_dump_json(
                            exclude_none=True
                        )
                        self.user_db.state_json = next_state_json
                    success = True
                elif (
                    not_exist_fix is True
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
                    response_tg = await self.post_method_tg(method_tg)
                    if isinstance(response_tg, SuccessTG):
                        if isinstance(self.next_state, StateJS):
                            next_state_json = self.next_state.model_dump_json(
                                exclude_none=True
                            )
                            self.user_db.state_json = next_state_json
                        success = True
        return success

    async def post_method_tg(self, method_tg: MethodTG) -> SuccessTG | ErrorTG | None:
        async with httpx.AsyncClient() as client:
            logger.debug(
                f"Attempting sending a response for Update #"
                f"{self.update_tg.update_id} from user "
                f"{self.user_db.full_name}. "
                f"Method '{method_tg._url}'"
            )
            try:
                response: httpx.Response = await client.post(
                    url=settings.get_tg_endpoint(method_tg._url),
                    json=method_tg.model_dump(exclude_none=True),
                )
                response.raise_for_status()
                logger.debug(
                    f"Method '{method_tg._url}' was delivered to "
                    f"Telegram API (HTTP status {response.status_code})."
                )

                try:
                    response_data = response.json()
                    success_tg = SuccessTG.model_validate(response_data)
                    logger.debug(
                        f"Method '{method_tg._url}' was accepted by "
                        f"Telegram API. Response JSON: {response_data}"
                    )
                    return success_tg
                except ValidationError as e:
                    logger.warning(
                        f"Unable to validate response {response_data} "
                        "as a successful response for Method "
                        f"'{method_tg._url}': {e}"
                    )
                    return None
            except httpx.TimeoutException as e:
                # If ANY type of timeout occurs, this block is executed
                logger.error(f"Request timed out for Method '{method_tg._url}': {e}")
                # Handle the timeout (e.g., retry, log, return an error indicator)
                return None  # Or raise a custom exception
            except httpx.RequestError as e:
                # Catch other request errors (like network issues, DNS failures etc.)
                logger.error(
                    f"An error occurred while delivering Method '{method_tg._url}': {e}"
                )
                return None
            except httpx.HTTPStatusError as e:
                # Catch HTTP status errors (4xx, 5xx responses) - these are NOT timeouts
                logger.error(f"HTTP status error for Method '{method_tg._url}': {e}")
                try:
                    error_data = e.response.json()
                    error_tg = ErrorTG.model_validate(error_data)
                    logger.warning(
                        f"Telegram API Error Details for Method '{method_tg._url}': "
                        f"error_code={error_tg.error_code}, "
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
                        logger.error(f"Raw error response body text: {e.response.text}")
                    # Correct: Return None to indicate that an HTTP status error occurred,
                    # but the error details couldn't be parsed/validated into an ErrorTG model.
                    return None
            except Exception as e:
                logger.error(
                    f"An unexpected error occurred during API call for "
                    f"Method '{method_tg._url}': {e}",
                    exc_info=True,
                )
                return None

    def archive_choice_method_tg(self, text: str) -> MethodTG:
        assert isinstance(self.update_tg, CallbackQueryUpdateTG), (
            "Choice archiving method only works with CallbackQueryUpdateTG type"
        )
        chat_id = self.update_tg.callback_query.message.chat.id
        message_id = self.update_tg.callback_query.message.message_id
        # old_text = self.update_tg.callback_query.message.text
        logger.debug(f"Archiving message #{message_id} by editing it to '{text}'.")
        method_tg = EditMessageTextTG(
            chat_id=chat_id,
            message_id=message_id,
            # text=f"<s>{old_text}</s>\n\n{String.YOU_HAVE_CHOSEN}: {string}.",
            text=text,
            # parse_mode="HTML",
        )
        return method_tg

    def get_stateless_conversation(self) -> list[MethodTG]:
        logger.debug(
            f"Initiating Stateless Conversation with {self.user_db.full_name}."
        )
        assert self.state is None, "This method is designed for stateless conversation"
        methods_tg_list: list[MethodTG] = []
        update_id = self.update_tg.update_id
        if isinstance(self.update_tg, MessageUpdateTG):
            message_id = self.update_tg.message.message_id
            logger.debug(
                f"Update #{update_id} is a message #{message_id} from "
                f"{self.user_db.full_name}."
            )
            logger.debug(f"Preparing Main Menu for {self.user_db.full_name}.")
            methods_tg_list.append(
                self.stateless_mainmenu_method_tg(f"{String.PICK_A_FUNCTION}.")
            )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
            data = self.update_tg.callback_query.data
            message_id = self.update_tg.callback_query.message.message_id
            chat_id = self.update_tg.callback_query.message.chat.id
            logger.debug(
                f"Update #{update_id} is a data='{data}' from {self.user_db.full_name}."
            )
            if data == CallbackData.ENTER_TICKET_NUMBER:
                logger.debug(
                    f"data='{data}' is recognized as a ticket number input. "
                    f"Preparing the answer for {self.user_db.full_name}."
                )
                self.next_state = StateJS(
                    action=Action.ENTER_TICKET_NUMBER,
                    script=Script.INITIAL_DATA,
                    device_index=0,
                )
                methods_tg_list.append(
                    self.archive_choice_method_tg(String.CLOSE_TICKET_BTN)
                )
                methods_tg_list.append(
                    self.send_text_message_tg(f"{String.ENTER_TICKET_NUMBER}.")
                )
            elif data == CallbackData.ENABLE_HIRING:
                logger.debug(
                    f"data='{data}' is recognized as enable hiring. "
                    f"Preparing the answer for {self.user_db.full_name}."
                )
                if not self.user_db.is_hiring:
                    self.user_db.is_hiring = True
                    methods_tg_list.append(
                        EditMessageTextTG(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=f"{String.HIRING_ENABLED}",
                            reply_markup=InlineKeyboardMarkupTG(
                                inline_keyboard=self.get_mainmenu_keyboard_array()
                            ),
                        )
                    )
                else:
                    methods_tg_list.append(
                        EditMessageTextTG(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=(f"{String.HIRING_ALREADY_ENABLED}"),
                            reply_markup=InlineKeyboardMarkupTG(
                                inline_keyboard=self.get_mainmenu_keyboard_array()
                            ),
                        )
                    )
            elif data == CallbackData.DISABLE_HIRING:
                logger.debug(
                    f"data='{data}' is recognized as disable hiring. "
                    f"Preparing the answer for {self.user_db.full_name}."
                )
                if self.user_db.is_hiring:
                    self.user_db.is_hiring = False
                    methods_tg_list.append(
                        EditMessageTextTG(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=f"{String.HIRING_DISABLED}",
                            reply_markup=InlineKeyboardMarkupTG(
                                inline_keyboard=self.get_mainmenu_keyboard_array()
                            ),
                        )
                    )
                else:
                    methods_tg_list.append(
                        EditMessageTextTG(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=f"{String.HIRING_ALREADY_DISABLED}",
                            reply_markup=InlineKeyboardMarkupTG(
                                inline_keyboard=self.get_mainmenu_keyboard_array()
                            ),
                        )
                    )
            else:
                logger.debug(
                    f"data='{data}' is not recognized. "
                    f"Preparing Main Menu for {self.user_db.full_name}."
                )
                methods_tg_list.append(
                    self.stateless_mainmenu_method_tg(
                        f"{String.GOT_UNEXPECTED_DATA}. "
                        f"{String.PICK_A_FUNCTION} {String.FROM_OPTIONS_BELOW}."
                    )
                )
        return methods_tg_list

    def stateless_mainmenu_method_tg(self, text: str) -> MethodTG:
        mainmenu_keyboard_array = self.get_mainmenu_keyboard_array()
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

    def get_mainmenu_keyboard_array(self) -> list[list[InlineKeyboardButtonTG]]:
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

    def get_device_conversation(self) -> list[MethodTG]:
        logger.debug(
            f"Initiating initial data conversation with {self.user_db.full_name}."
        )
        assert self.state, "This method is designed for existing conversation"
        methods_tg_list: list[MethodTG] = []
        if self.state.action == Action.ENTER_TICKET_NUMBER:
            if (
                isinstance(self.update_tg, MessageUpdateTG)
                and self.update_tg.message.text
            ):
                message_text = self.update_tg.message.text
                if re.fullmatch(r"\d+", message_text):
                    self.next_state = StateJS(
                        action=Action.ENTER_CONTRACT_NUMBER,
                        script=self.state.script,
                        device_index=self.state.device_index,
                        ticket_number=message_text,
                    )
                    methods_tg_list.append(
                        self.send_text_message_tg(f"{String.ENTER_CONTRACT_NUMBER}.")
                    )
                else:
                    methods_tg_list.append(
                        self.send_text_message_tg(
                            f"{String.INCORRECT_TICKET_NUMBER}. "
                            f"{String.ENTER_TICKET_NUMBER}."
                        )
                    )
            elif isinstance(self.update_tg, CallbackQueryUpdateTG):
                methods_tg_list.append(
                    self.send_text_message_tg(
                        f"{String.GOT_DATA_NOT_TICKET_NUMBER}. "
                        f"{String.ENTER_TICKET_NUMBER}."
                    )
                )
        elif self.state.action == Action.ENTER_CONTRACT_NUMBER:
            if (
                isinstance(self.update_tg, MessageUpdateTG)
                and self.update_tg.message.text
            ):
                message_text = self.update_tg.message.text
                if re.fullmatch(r"\d+", message_text):
                    self.next_state = StateJS(
                        action=Action.PICK_DEVICE_TYPE,
                        script=self.state.script,
                        ticket_number=self.state.ticket_number,
                        contract_number=message_text,
                    )
                    methods_tg_list.append(
                        self.pick_device_type(f"{String.PICK_DEVICE_TYPE}.")
                    )
                else:
                    methods_tg_list.append(
                        self.send_text_message_tg(
                            f"{String.INCORRECT_CONTRACT_NUMBER}. "
                            f"{String.ENTER_CONTRACT_NUMBER}."
                        )
                    )
            elif isinstance(self.update_tg, CallbackQueryUpdateTG):
                methods_tg_list.append(
                    self.send_text_message_tg(
                        f"{String.GOT_DATA_NOT_CONTRACT_NUMBER}. "
                        f"{String.ENTER_CONTRACT_NUMBER}."
                    )
                )
        elif self.state.action == Action.PICK_DEVICE_TYPE:
            if isinstance(self.update_tg, CallbackQueryUpdateTG):
                expected_callback_data = [
                    CallbackData.IP,
                    CallbackData.TVE,
                    CallbackData.ROUTER,
                ]
                data = self.update_tg.callback_query.data
                try:
                    received_callback_data = CallbackData(data)
                    if received_callback_data in expected_callback_data:
                        self.next_state = StateJS(
                            action=Action.ENTER_SERIAL_NUMBER,
                            script=self.state.script,
                            ticket_number=self.state.ticket_number,
                            contract_number=self.state.contract_number,
                            device_index=self.state.device_index
                            if self.state.device_index is not None
                            else 0,
                            device_type=DeviceTypeName[received_callback_data.name],
                        )
                        methods_tg_list.append(
                            self.archive_choice_method_tg(
                                String[received_callback_data.name]
                            )
                        )
                        methods_tg_list.append(
                            self.send_text_message_tg(f"{String.ENTER_SERIAL_NUMBER}.")
                        )
                    else:
                        raise ValueError
                except ValueError:
                    logger.debug(
                        f"Received invalid callback data '{data}' "
                        "for device type selection."
                    )
                    methods_tg_list.append(
                        self.pick_device_type(
                            f"{String.GOT_UNEXPECTED_DATA}. "
                            f"{String.PICK_DEVICE_TYPE} "
                            f"{String.FROM_OPTIONS_BELOW}."
                        )
                    )
            elif isinstance(self.update_tg, MessageUpdateTG):
                logger.debug(
                    f"User {self.user_db.full_name} responded with "
                    "message while callback data was awaited."
                )
                methods_tg_list.append(
                    self.pick_device_type(
                        f"{String.DEVICE_TYPE_WAS_NOT_PICKED}. "
                        f"{String.PICK_DEVICE_TYPE} {String.FROM_OPTIONS_BELOW}."
                    )
                )
        elif self.state.action == Action.ENTER_SERIAL_NUMBER:
            if (
                isinstance(self.update_tg, MessageUpdateTG)
                and self.update_tg.message.text
            ):
                message_text = self.update_tg.message.text
                if re.fullmatch(r"[\dA-Za-z]+", message_text):
                    self.next_state = StateJS(
                        action=Action.PICK_INSTALL_OR_RETURN,
                        script=self.state.script,
                        ticket_number=self.state.ticket_number,
                        contract_number=self.state.contract_number,
                        device_index=self.state.device_index
                        if self.state.device_index is not None
                        else 0,
                        device_type=self.state.device_type,
                        device_serial_number=message_text,
                    )
                    methods_tg_list.append(
                        self.pick_install_or_return(f"{String.PICK_INSTALL_OR_RETURN}.")
                    )
                else:
                    methods_tg_list.append(
                        self.send_text_message_tg(
                            f"{String.INCORRECT_SERIAL_NUM}. "
                            f"{String.ENTER_SERIAL_NUMBER}."
                        )
                    )
            elif isinstance(self.update_tg, CallbackQueryUpdateTG):
                methods_tg_list.append(
                    self.send_text_message_tg(
                        f"{String.GOT_DATA_NOT_SERIAL_NUMBER}. "
                        f"{String.ENTER_SERIAL_NUMBER}."
                    )
                )
        elif self.state.action == Action.PICK_INSTALL_OR_RETURN:
            if isinstance(self.update_tg, CallbackQueryUpdateTG):
                expected_callback_data = [
                    CallbackData.INSTALL_DEVICE_BTN,
                    CallbackData.REMOVE_DEVICE_BTN,
                    CallbackData.EDIT_DEVICE_SN_BTN,
                ]
                data = self.update_tg.callback_query.data
                try:
                    received_callback_data = CallbackData(data)
                    if received_callback_data in expected_callback_data:
                        if received_callback_data == CallbackData.INSTALL_DEVICE_BTN:
                            self.next_state = StateJS(
                                action=Action.TICKET_MENU,
                                script=self.state.script,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                                device_index=self.state.device_index
                                if self.state.device_index is not None
                                else 0,
                                devices_list=self.state.devices_list
                                if self.state.devices_list is not None
                                else [],
                            )
                            self.next_state.devices_list.append(
                                DeviceJS(
                                    type=self.state.device_type,
                                    serial_number=self.state.device_serial_number,
                                    is_defective=False,
                                )
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    String[received_callback_data.name]
                                )
                            )
                            methods_tg_list.append(
                                self.pick_ticket_action(f"{String.PICK_TICKET_ACTION}.")
                            )
                    else:
                        raise ValueError
                except ValueError:
                    logger.debug(
                        f"Received invalid callback data '{data}' "
                        "for device action selection."
                    )
                    methods_tg_list.append(
                        self.pick_install_or_return(
                            f"{String.GOT_UNEXPECTED_DATA}. {String.PICK_INSTALL_OR_RETURN}."
                        )
                    )
            elif isinstance(self.update_tg, MessageUpdateTG):
                logger.debug(
                    f"User {self.user_db.full_name} responded with "
                    "message while callback data was awaited."
                )
                methods_tg_list.append(
                    self.pick_install_or_return(
                        f"{String.DEVICE_ACTION_WAS_NOT_PICKED}. "
                        f"{String.PICK_INSTALL_OR_RETURN}."
                    )
                )
        elif self.state.action == Action.TICKET_MENU:
            if isinstance(self.update_tg, CallbackQueryUpdateTG):
                expected_callback_data = [
                    CallbackData.EDIT_TICKET_NUMBER,
                    CallbackData.EDIT_CONTRACT_NUMBER,
                    CallbackData.ADD_DEVICE_BTN,
                    CallbackData.CLOSE_TICKET_BTN,
                    CallbackData.QUIT_WITHOUT_SAVING_BTN,
                ]
                all_devices_list = [
                    CallbackData.DEVICE_0,
                    CallbackData.DEVICE_1,
                    CallbackData.DEVICE_2,
                    CallbackData.DEVICE_3,
                    CallbackData.DEVICE_4,
                    CallbackData.DEVICE_5,
                ]
                if self.state.devices_list:
                    expected_callback_data.extend(
                        all_devices_list[: len(self.state.devices_list)]
                    )
                data = self.update_tg.callback_query.data
                try:
                    received_callback_data = CallbackData(data)
                    if received_callback_data in expected_callback_data:
                        if received_callback_data == CallbackData.EDIT_TICKET_NUMBER:
                            self.next_state = StateJS(
                                action=Action.EDIT_TICKET_NUMBER,
                                script=self.state.script,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                                devices_list=self.state.devices_list,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.EDIT_TICKET_NUMBER}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_ticket_action(
                                    f"{String.ENTER_NEW_TICKET_NUMBER}."
                                )
                            )
                        elif (
                            received_callback_data == CallbackData.EDIT_CONTRACT_NUMBER
                        ):
                            self.next_state = StateJS(
                                action=Action.EDIT_CONTRACT_NUMBER,
                                script=self.state.script,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                                devices_list=self.state.devices_list,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.EDIT_CONTRACT_NUMBER}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_ticket_action(
                                    f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                                )
                            )
                        elif received_callback_data in all_devices_list:
                            callback_device_index = None
                            device_index_string = received_callback_data[-1]
                            try:
                                callback_device_index = int(device_index_string)
                            except ValueError:
                                logger.debug(
                                    f"Last symbol of '{data}' data is "
                                    "not an integer string. int() failed."
                                )
                            if callback_device_index is not None:
                                self.next_state = StateJS(
                                    action=Action.DEVICE_MENU,
                                    script=self.state.script,
                                    ticket_number=self.state.ticket_number,
                                    contract_number=self.state.contract_number,
                                    device_index=callback_device_index,
                                    devices_list=self.state.devices_list,
                                )
                                methods_tg_list.append(
                                    self.archive_choice_method_tg(
                                        f"{String.EDIT_CONTRACT_NUMBER}."
                                    )
                                )
                                methods_tg_list.append(
                                    self.pick_ticket_action(
                                        f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                                    )
                                )
                        elif received_callback_data == CallbackData.ADD_DEVICE_BTN:
                            self.next_state = StateJS(
                                action=Action.ENTER_SERIAL_NUMBER,
                                script=self.state.script,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                                device_index=len(self.state.devices_list),
                                devices_list=self.state.devices_list,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.EDIT_CONTRACT_NUMBER}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_ticket_action(
                                    f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                                )
                            )
                    else:
                        raise ValueError
                except ValueError:
                    logger.debug(
                        f"Received invalid callback data '{data}' "
                        "for ticket menu action selection. "
                    )
                    methods_tg_list.append(
                        self.pick_ticket_action(
                            f"{String.GOT_UNEXPECTED_DATA}. {String.PICK_TICKET_ACTION}."
                        )
                    )
            elif isinstance(self.update_tg, MessageUpdateTG):
                logger.debug(
                    f"User {self.user_db.full_name} responded with "
                    "message while callback data was awaited."
                )
                methods_tg_list.append(
                    self.pick_ticket_action(
                        f"{String.TICKET_ACTION_WAS_NOT_PICKED}. "
                        f"{String.PICK_TICKET_ACTION}."
                    )
                )
        return methods_tg_list

    def get_initial_device_conversation(self) -> list[MethodTG]:
        logger.debug(
            f"Initiating initial data conversation with {self.user_db.full_name}."
        )
        assert self.state and self.state.script == Script.INITIAL_DATA, (
            "This method is designed for Script.INITIAL_DATA conversation"
        )
        methods_tg_list: list[MethodTG] = []
        if self.state.action == Action.ENTER_TICKET_NUMBER:
            if (
                isinstance(self.update_tg, MessageUpdateTG)
                and self.update_tg.message.text
            ):
                message_text = self.update_tg.message.text
                if re.fullmatch(r"\d+", message_text):
                    self.next_state = StateJS(
                        action=Action.ENTER_CONTRACT_NUMBER,
                        script=Script.INITIAL_DATA,
                        ticket_number=message_text,
                    )
                    methods_tg_list.append(
                        self.send_text_message_tg(f"{String.ENTER_CONTRACT_NUMBER}.")
                    )
                else:
                    methods_tg_list.append(
                        self.send_text_message_tg(
                            f"{String.INCORRECT_TICKET_NUMBER}. "
                            f"{String.ENTER_TICKET_NUMBER}."
                        )
                    )
            elif isinstance(self.update_tg, CallbackQueryUpdateTG):
                methods_tg_list.append(
                    self.send_text_message_tg(
                        f"{String.GOT_DATA_NOT_TICKET_NUMBER}. "
                        f"{String.ENTER_TICKET_NUMBER}."
                    )
                )
        elif self.state.action == Action.ENTER_CONTRACT_NUMBER:
            if (
                isinstance(self.update_tg, MessageUpdateTG)
                and self.update_tg.message.text
            ):
                message_text = self.update_tg.message.text
                if re.fullmatch(r"\d+", message_text):
                    self.next_state = StateJS(
                        action=Action.PICK_DEVICE_TYPE,
                        script=Script.INITIAL_DATA,
                        ticket_number=self.state.ticket_number,
                        contract_number=message_text,
                    )
                    methods_tg_list.append(
                        self.pick_device_type(f"{String.PICK_DEVICE_TYPE}.")
                    )
                else:
                    methods_tg_list.append(
                        self.send_text_message_tg(
                            f"{String.INCORRECT_CONTRACT_NUMBER}. "
                            f"{String.ENTER_CONTRACT_NUMBER}."
                        )
                    )
            elif isinstance(self.update_tg, CallbackQueryUpdateTG):
                methods_tg_list.append(
                    self.send_text_message_tg(
                        f"{String.GOT_DATA_NOT_CONTRACT_NUMBER}. "
                        f"{String.ENTER_CONTRACT_NUMBER}."
                    )
                )
        elif self.state.action == Action.PICK_DEVICE_TYPE:
            if isinstance(self.update_tg, CallbackQueryUpdateTG):
                expected_callback_data = [
                    CallbackData.IP,
                    CallbackData.TVE,
                    CallbackData.ROUTER,
                ]
                data = self.update_tg.callback_query.data
                try:
                    received_callback_data = CallbackData(data)
                    if received_callback_data in expected_callback_data:
                        self.next_state = StateJS(
                            action=Action.ENTER_SERIAL_NUMBER,
                            script=Script.INITIAL_DATA,
                            ticket_number=self.state.ticket_number,
                            contract_number=self.state.contract_number,
                            device_type=DeviceTypeName[received_callback_data.name],
                        )
                        methods_tg_list.append(
                            self.archive_choice_method_tg(
                                String[received_callback_data.name]
                            )
                        )
                        methods_tg_list.append(
                            self.send_text_message_tg(f"{String.ENTER_SERIAL_NUMBER}.")
                        )
                    else:
                        raise ValueError
                except ValueError:
                    logger.debug(
                        f"Received invalid callback data '{data}' "
                        "for device type selection."
                    )
                    methods_tg_list.append(
                        self.pick_device_type(
                            f"{String.GOT_UNEXPECTED_DATA}. "
                            f"{String.PICK_DEVICE_TYPE} "
                            f"{String.FROM_OPTIONS_BELOW}."
                        )
                    )
            elif isinstance(self.update_tg, MessageUpdateTG):
                logger.debug(
                    f"User {self.user_db.full_name} responded with "
                    "message while callback data was awaited."
                )
                methods_tg_list.append(
                    self.pick_device_type(
                        f"{String.DEVICE_TYPE_WAS_NOT_PICKED}. "
                        f"{String.PICK_DEVICE_TYPE} {String.FROM_OPTIONS_BELOW}."
                    )
                )
        elif self.state.action == Action.ENTER_SERIAL_NUMBER:
            if (
                isinstance(self.update_tg, MessageUpdateTG)
                and self.update_tg.message.text
            ):
                message_text = self.update_tg.message.text
                if re.fullmatch(r"[\dA-Za-z]+", message_text):
                    self.next_state = StateJS(
                        action=Action.PICK_INSTALL_OR_RETURN,
                        script=Script.INITIAL_DATA,
                        ticket_number=self.state.ticket_number,
                        contract_number=self.state.contract_number,
                        device_type=self.state.device_type,
                        device_serial_number=message_text,
                    )
                    methods_tg_list.append(
                        self.pick_install_or_return(f"{String.PICK_INSTALL_OR_RETURN}.")
                    )
                else:
                    methods_tg_list.append(
                        self.send_text_message_tg(
                            f"{String.INCORRECT_SERIAL_NUM}. "
                            f"{String.ENTER_SERIAL_NUMBER}."
                        )
                    )
            elif isinstance(self.update_tg, CallbackQueryUpdateTG):
                methods_tg_list.append(
                    self.send_text_message_tg(
                        f"{String.GOT_DATA_NOT_SERIAL_NUMBER}. "
                        f"{String.ENTER_SERIAL_NUMBER}."
                    )
                )
        elif self.state.action == Action.PICK_INSTALL_OR_RETURN:
            if isinstance(self.update_tg, CallbackQueryUpdateTG):
                expected_callback_data = [
                    CallbackData.INSTALL_DEVICE_BTN,
                    CallbackData.REMOVE_DEVICE_BTN,
                    CallbackData.EDIT_DEVICE_SN_BTN,
                ]
                data = self.update_tg.callback_query.data
                try:
                    received_callback_data = CallbackData(data)
                    if received_callback_data in expected_callback_data:
                        if received_callback_data == CallbackData.INSTALL_DEVICE_BTN:
                            self.next_state = StateJS(
                                action=Action.TICKET_MENU,
                                script=Script.INITIAL_DATA,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                                devices_list=[
                                    DeviceJS(
                                        type=self.state.device_type,
                                        serial_number=self.state.device_serial_number,
                                        is_defective=False,
                                    )
                                ],
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    String[received_callback_data.name]
                                )
                            )
                            methods_tg_list.append(
                                self.pick_ticket_action(f"{String.PICK_TICKET_ACTION}.")
                            )
                    else:
                        raise ValueError
                except ValueError:
                    logger.debug(
                        f"Received invalid callback data '{data}' "
                        "for device action selection."
                    )
                    methods_tg_list.append(
                        self.pick_install_or_return(
                            f"{String.GOT_UNEXPECTED_DATA}. {String.PICK_INSTALL_OR_RETURN}."
                        )
                    )
            elif isinstance(self.update_tg, MessageUpdateTG):
                logger.debug(
                    f"User {self.user_db.full_name} responded with "
                    "message while callback data was awaited."
                )
                methods_tg_list.append(
                    self.pick_install_or_return(
                        f"{String.DEVICE_ACTION_WAS_NOT_PICKED}. "
                        f"{String.PICK_INSTALL_OR_RETURN}."
                    )
                )
        elif self.state.action == Action.TICKET_MENU:
            if isinstance(self.update_tg, CallbackQueryUpdateTG):
                expected_callback_data = [
                    CallbackData.EDIT_TICKET_NUMBER,
                    CallbackData.EDIT_CONTRACT_NUMBER,
                    CallbackData.ADD_DEVICE_BTN,
                    CallbackData.CLOSE_TICKET_BTN,
                    CallbackData.QUIT_WITHOUT_SAVING_BTN,
                ]
                all_devices_list = [
                    CallbackData.DEVICE_0,
                    CallbackData.DEVICE_1,
                    CallbackData.DEVICE_2,
                    CallbackData.DEVICE_3,
                    CallbackData.DEVICE_4,
                    CallbackData.DEVICE_5,
                ]
                if self.state.devices_list:
                    expected_callback_data.extend(
                        all_devices_list[: len(self.state.devices_list)]
                    )
                data = self.update_tg.callback_query.data
                try:
                    received_callback_data = CallbackData(data)
                    if received_callback_data in expected_callback_data:
                        if received_callback_data == CallbackData.EDIT_TICKET_NUMBER:
                            self.next_state = StateJS(
                                action=Action.EDIT_TICKET_NUMBER,
                                script=Script.INITIAL_DATA,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                                devices_list=self.state.devices_list,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.EDIT_TICKET_NUMBER}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_ticket_action(
                                    f"{String.ENTER_NEW_TICKET_NUMBER}."
                                )
                            )
                        elif (
                            received_callback_data == CallbackData.EDIT_CONTRACT_NUMBER
                        ):
                            self.next_state = StateJS(
                                action=Action.EDIT_CONTRACT_NUMBER,
                                script=Script.INITIAL_DATA,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                                devices_list=self.state.devices_list,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.EDIT_CONTRACT_NUMBER}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_ticket_action(
                                    f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                                )
                            )
                        elif received_callback_data in all_devices_list:
                            callback_device_index = None
                            device_index_string = received_callback_data[-1]
                            try:
                                callback_device_index = int(device_index_string)
                            except ValueError:
                                logger.debug(
                                    f"Last symbol of '{data}' data is "
                                    "not an integer string. int() failed."
                                )
                            if callback_device_index is not None:
                                self.next_state = StateJS(
                                    action=Action.DEVICE_MENU,
                                    script=Script.INITIAL_DATA,
                                    ticket_number=self.state.ticket_number,
                                    contract_number=self.state.contract_number,
                                    device_index=callback_device_index,
                                    devices_list=self.state.devices_list,
                                )
                                methods_tg_list.append(
                                    self.archive_choice_method_tg(
                                        f"{String.EDIT_CONTRACT_NUMBER}."
                                    )
                                )
                                methods_tg_list.append(
                                    self.pick_ticket_action(
                                        f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                                    )
                                )
                        elif received_callback_data == CallbackData.ADD_DEVICE_BTN:
                            self.next_state = StateJS(
                                action=Action.ENTER_SERIAL_NUMBER,
                                script=Script.INITIAL_DATA,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                                device_index=len(self.state.devices_list),
                                devices_list=self.state.devices_list,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.EDIT_CONTRACT_NUMBER}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_ticket_action(
                                    f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                                )
                            )
                    else:
                        raise ValueError
                except ValueError:
                    logger.debug(
                        f"Received invalid callback data '{data}' "
                        "for ticket menu action selection. "
                    )
                    methods_tg_list.append(
                        self.pick_ticket_action(
                            f"{String.GOT_UNEXPECTED_DATA}. {String.PICK_TICKET_ACTION}."
                        )
                    )
            elif isinstance(self.update_tg, MessageUpdateTG):
                logger.debug(
                    f"User {self.user_db.full_name} responded with "
                    "message while callback data was awaited."
                )
                methods_tg_list.append(
                    self.pick_ticket_action(
                        f"{String.TICKET_ACTION_WAS_NOT_PICKED}. "
                        f"{String.PICK_TICKET_ACTION}."
                    )
                )
        return methods_tg_list

    def send_text_message_tg(self, text: str):
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
        )

    def pick_device_type(self, text: str = f"{String.PICK_DEVICE_TYPE}."):
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(
                inline_keyboard=[
                    [
                        InlineKeyboardButtonTG(
                            text=String.IP,
                            callback_data=CallbackData.IP,
                        ),
                        InlineKeyboardButtonTG(
                            text=String.TVE,
                            callback_data=CallbackData.TVE,
                        ),
                        InlineKeyboardButtonTG(
                            text=String.ROUTER,
                            callback_data=CallbackData.ROUTER,
                        ),
                    ]
                ]
            ),
        )

    def pick_install_or_return(self, text: str = f"{String.PICK_INSTALL_OR_RETURN}."):
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
                            text=String.REMOVE_DEVICE_BTN,
                            callback_data=CallbackData.REMOVE_DEVICE_BTN,
                        ),
                    ],
                    [
                        InlineKeyboardButtonTG(
                            text=String.EDIT_DEVICE_SN_BTN,
                            callback_data=CallbackData.EDIT_DEVICE_SN_BTN,
                        ),
                    ],
                ]
            ),
        )

    def pick_ticket_action(self, text: str = f"{String.PICK_TICKET_ACTION}."):
        assert (
            self.next_state
            and self.next_state.ticket_number
            and self.next_state.contract_number
            and self.next_state.devices_list
        ), (
            "The ticket menu only works with next_state having at least one device being filled in."
        )
        inline_keyboard_array: list[list[InlineKeyboardButtonTG]] = []
        ticket_number_button: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=f"{String.TICKET_NUMBER_BTN} {self.next_state.ticket_number}",
                callback_data=CallbackData.EDIT_TICKET_NUMBER,
            ),
        ]
        contract_number_button: list[InlineKeyboardButtonTG] = [
            InlineKeyboardButtonTG(
                text=f"{String.CONTRACT_NUMBER_BTN} {self.next_state.contract_number}",
                callback_data=CallbackData.EDIT_CONTRACT_NUMBER,
            ),
        ]
        device_button_array: list[list[InlineKeyboardButtonTG]] = []
        emoji_dict = {
            1: "1⃣",
            2: "2⃣",
            3: "3⃣",
            4: "4⃣",
            5: "5⃣",
            6: "6⃣",
            7: "7⃣",
            8: "8⃣",
            9: "9⃣",
            10: "🔟",
            "#": "#⃣",
            "*": "*⃣",
        }
        for index, device in enumerate(self.next_state.devices_list):
            device_number = index + 1
            device_icon = "↪️" if device.is_defective else "✅"
            device_type = String[device.type.name]
            device_serial_number = device.serial_number
            device_button_array.append(
                [
                    InlineKeyboardButtonTG(
                        text=(
                            f"{emoji_dict[device_number]}{device_icon}"
                            f" {device_type.upper()} {String.S_N} "
                            f"{device_serial_number}"
                        ),
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
        if len(self.next_state.devices_list) < 6:
            inline_keyboard_array.append(add_device_button)
        if len(self.next_state.devices_list) > 0:
            inline_keyboard_array.append(close_ticket_button)
        inline_keyboard_array.append(quit_without_saving_button)
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard_array),
        )
