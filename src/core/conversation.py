from __future__ import annotations
from typing import Callable, TypedDict
import re
import httpx
from pydantic import ValidationError
from sqlalchemy import select, exists
from sqlalchemy.orm import selectinload
from src.core.config import settings
from src.core.logger import logger
from src.core.enums import RoleName, DeviceTypeName, Strings, Action, Script
from src.core.models import StateJS
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
        self.next_state: StateJS | None
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
        if self.state is None or self.state.message_id and not self.state.action:
            logger.debug(
                f"There is no Conversation State with {self.user_db.full_name}."
            )
            success = await self.ensure_delivery(self.get_stateless_conversation)
        elif self.state.script == Script.INITIAL_DATA:
            logger.debug(f"Initial device conversation with {self.user_db.full_name}.")
            self.response_methods_list.extend(self.initial_device_conversation_list())
        return success

    async def ensure_delivery(
        self,
        method_generator: Callable[[], tuple[tuple[MethodTG, ...], StateJS | None]],
    ) -> bool:
        retry_response_tg: SuccessTG | ErrorTG | None
        method_tg_tuple, state_obj = method_generator()
        last_method_tg_index = len(method_tg_tuple) - 1
        success = False
        for index, method_tg in enumerate(method_tg_tuple):
            retry_response_tg = await self.post_method_tg(method_tg)
            if index == last_method_tg_index:
                if isinstance(retry_response_tg, SuccessTG):
                    if state_obj:
                        if isinstance(retry_response_tg.result, MessageTG):
                            state_obj.message_id = retry_response_tg.result.message_id
                        state_json = state_obj.model_dump_json(exclude_none=True)
                        self.user_db.state_json = state_json
                    success = True
                elif (
                    isinstance(method_tg, EditMessageTextTG)
                    and isinstance(retry_response_tg, ErrorTG)
                    and retry_response_tg.error_code == 400
                    and retry_response_tg.description
                    in (
                        "Bad Request: message not found",
                        "Bad Request: message to edit not found",
                    )
                ):
                    retry_method_tg = SendMessageTG(
                        chat_id=method_tg.chat_id,
                        text=method_tg.text,
                        parse_mode=method_tg.parse_mode,
                        reply_markup=method_tg.reply_markup,
                    )
                    retry_response_tg = await self.post_method_tg(retry_method_tg)
                    if isinstance(retry_response_tg, SuccessTG):
                        if state_obj:
                            if isinstance(retry_response_tg.result, MessageTG):
                                state_obj.message_id = (
                                    retry_response_tg.result.message_id
                                )
                            state_json = state_obj.model_dump_json(exclude_none=True)
                            self.user_db.state_json = state_json
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

    def get_stateless_conversation(self) -> tuple[tuple[MethodTG], StateJS | None]:
        logger.debug(
            f"Initiating stateless conversation with {self.user_db.full_name}."
        )
        state_obj = None
        if isinstance(self.update_tg, MessageUpdateTG):
            logger.debug(f"Responding with Main Menu to {self.user_db.full_name}.")
            method_tg = self.stateless_mainmenu()
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
            data = self.update_tg.callback_query.data
            chat_id = self.update_tg.callback_query.message.chat.id
            message_id = self.update_tg.callback_query.message.message_id
            if data == Action.TICKET_NUMBER_INPUT:
                state_obj = StateJS(
                    message_id=message_id,
                    action=Action.TICKET_NUMBER_INPUT,
                    script=Script.INITIAL_DATA,
                )
                method_tg = self.enter_ticket_number(Strings.ENTER_TICKET_NUMBER)
            elif data == Action.ENABLE_HIRING:
                if not self.user_db.is_hiring:
                    self.user_db.is_hiring = True
                    method_tg = EditMessageTextTG(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f"{self.user_db.first_name}, {Strings.HIRING_ENABLED}",
                        reply_markup=InlineKeyboardMarkupTG(
                            inline_keyboard=self.get_mainmenu_keyboard_array()
                        ),
                    )
                else:
                    method_tg = EditMessageTextTG(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f"{self.user_db.first_name}, {Strings.HIRING_ALREADY_ENABLED}",
                        reply_markup=InlineKeyboardMarkupTG(
                            inline_keyboard=self.get_mainmenu_keyboard_array()
                        ),
                    )
            elif data == Action.DISABLE_HIRING:
                if self.user_db.is_hiring:
                    self.user_db.is_hiring = False
                    method_tg = EditMessageTextTG(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f"{self.user_db.first_name}, {Strings.HIRING_DISABLED}",
                        reply_markup=InlineKeyboardMarkupTG(
                            inline_keyboard=self.get_mainmenu_keyboard_array()
                        ),
                    )
                else:
                    method_tg = EditMessageTextTG(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=f"{self.user_db.first_name}, {Strings.HIRING_ALREADY_DISABLED}",
                        reply_markup=InlineKeyboardMarkupTG(
                            inline_keyboard=self.get_mainmenu_keyboard_array()
                        ),
                    )
        return (method_tg,), state_obj

    def stateless_mainmenu(self) -> MethodTG:
        method_tg = SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=f"{Strings.HELLO}, {self.user_db.first_name}, "
            f"{Strings.THESE_FUNCTIONS_ARE_AVAILABLE}",
            reply_markup=InlineKeyboardMarkupTG(
                inline_keyboard=self.get_mainmenu_keyboard_array()
            ),
        )
        return method_tg

    def get_mainmenu_keyboard_array(self) -> list[list[InlineKeyboardButtonTG]]:
        inline_keyboard_array = []
        if self.user_db.is_engineer:
            inline_keyboard_array.append(
                [
                    InlineKeyboardButtonTG(
                        text=Strings.CLOSE_TICKET_BTN,
                        callback_data=Action.TICKET_NUMBER_INPUT,
                    )
                ],
            )
            inline_keyboard_array.append(
                [
                    InlineKeyboardButtonTG(
                        text=Strings.TICKETS_HISTORY_BTN,
                        callback_data=Action.TICKETS_HISTORY_MENU_BUTTONS,
                    ),
                    InlineKeyboardButtonTG(
                        text=Strings.WRITEOFF_DEVICES_BTN,
                        callback_data=Action.WRITEOFF_DEVICES_LIST,
                    ),
                ],
            )
        if self.user_db.is_manager:
            inline_keyboard_array.append(
                [
                    InlineKeyboardButtonTG(
                        text=Strings.FORM_REPORT_BTN,
                        callback_data=Action.FORM_REPORT,
                    )
                ],
            )
            if self.user_db.is_hiring:
                inline_keyboard_array.append(
                    [
                        InlineKeyboardButtonTG(
                            text=Strings.DISABLE_HIRING_BTN,
                            callback_data=Action.DISABLE_HIRING,
                        )
                    ],
                )
            else:
                inline_keyboard_array.append(
                    [
                        InlineKeyboardButtonTG(
                            text=Strings.ENABLE_HIRING_BTN,
                            callback_data=Action.ENABLE_HIRING,
                        )
                    ],
                )
        return inline_keyboard_array

    def initial_device_conversation_list(self) -> list[MethodTG]:
        assert self.state is not None, (
            "State cannot be None in initial_device_conversation"
        )
        method_tg_list: list[MethodTG] = []
        if self.state.action == Action.TICKET_NUMBER_INPUT:
            if self.update_tg.message and self.update_tg.message.text:
                message_id = self.update_tg.message.message_id
                message_text = self.update_tg.message.text
                if re.fullmatch(r"\d+", message_text):
                    self.user_db.state_json = StateJS(
                        message_id=message_id,
                        action=Action.DEVICE_TYPE_BUTTONS,
                        script=Script.INITIAL_DATA,
                        ticket_number=message_text,
                    ).model_dump_json(exclude_none=True)
                    method_tg_list.append(
                        self.pick_device_type(f"{Strings.PICK_DEVICE_TYPE}.")
                    )
                else:
                    method_tg_list.append(
                        self.enter_ticket_number(
                            f"{Strings.INCORRECT_TICKET_NUMBER} "
                            f"{Strings.ENTER_TICKET_NUMBER}"
                        )
                    )
            elif self.update_tg.callback_query:
                pass
        elif self.state.action == Action.DEVICE_TYPE_BUTTONS:
            if self.update_tg.callback_query:
                data = self.update_tg.callback_query.data
                chat_id = self.update_tg.callback_query.message.chat.id
                message_id = self.update_tg.callback_query.message.message_id
                try:
                    device_type_enum = DeviceTypeName(data)
                    if device_type_enum.name in Strings.__members__:
                        self.next_state_json = StateJS(
                            message_id=message_id,
                            action=Action.DEVICE_SERIAL_NUMBER,
                            script=Script.INITIAL_DATA,
                            ticket_number=self.state.ticket_number,
                            device_type=device_type_enum,
                        ).model_dump_json(exclude_none=True)
                        method_tg_list.append(
                            EditMessageTextTG(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=f"{Strings.DEVICE_TYPE_PICKED}: {Strings[device_type_enum.name]}. {Strings.ENTER_SERIAL_NUMBER}.",
                            )
                        )
                except ValueError:
                    logger.debug(
                        f"Received invalid callback data '{data}' "
                        "for device type selection."
                        "Cannot convert to DeviceTypeName."
                    )
                    method_tg_list.append(
                        self.pick_device_type(
                            f"{Strings.UNEXPECTED_CALLBACK}. {Strings.PICK_DEVICE_TYPE} {Strings.FROM_THESE_VARIANTS}."
                        )
                    )
            elif self.update_tg.message:
                logger.debug(
                    f"User {self.user_db.full_name} responded with "
                    "message while callback data was awaited."
                )
                method_tg_list.append(
                    self.pick_device_type(
                        f"{Strings.DEVICE_TYPE_WAS_NOT_PICKED}. {Strings.PICK_DEVICE_TYPE} {Strings.FROM_THESE_VARIANTS}."
                    )
                )
        return method_tg_list

    def enter_ticket_number(self, text: str = Strings.ENTER_TICKET_NUMBER):
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
        )

    def pick_device_type(self, text: str = Strings.PICK_DEVICE_TYPE):
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(
                inline_keyboard=[
                    [
                        InlineKeyboardButtonTG(
                            text=Strings.IP,
                            callback_data=DeviceTypeName.IP,
                        ),
                        InlineKeyboardButtonTG(
                            text=Strings.TVE,
                            callback_data=DeviceTypeName.TVE,
                        ),
                        InlineKeyboardButtonTG(
                            text=Strings.ROUTER,
                            callback_data=DeviceTypeName.ROUTER,
                        ),
                    ]
                ]
            ),
        )

    def serial_number_input(self):
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=Strings.ENTER_SERIAL_NUMBER,
        )

    def install_return_input(self):
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=Strings.PICK_INSTALL_OR_RETURN,
            reply_markup=InlineKeyboardMarkupTG(
                inline_keyboard=[
                    [
                        InlineKeyboardButtonTG(
                            text=Strings.INSTALL_DEVICE_BTN,
                            callback_data="install_btn",
                        ),
                        InlineKeyboardButtonTG(
                            text=Strings.REMOVE_DEVICE_BTN,
                            callback_data="remove_btn",
                        ),
                    ],
                    [
                        InlineKeyboardButtonTG(
                            text=Strings.EDIT_DEVICE_SN_BTN,
                            callback_data="edit_sn_btn",
                        ),
                    ],
                ]
            ),
        )

    def ticket_devices_list(self):
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=Strings.TICKET_DEVICES_LIST,
            reply_markup=InlineKeyboardMarkupTG(
                inline_keyboard=[
                    [
                        InlineKeyboardButtonTG(
                            text=f"{Strings.TICKET_NUMBER_BTN} 123456789",
                            callback_data="install_btn1",
                        ),
                    ],
                    [
                        InlineKeyboardButtonTG(
                            text="1. ✅ IP / SS1458745697",
                            callback_data="install_btn2",
                        ),
                    ],
                    [
                        InlineKeyboardButtonTG(
                            text="2. ↪️ TVE / SS1458745697",
                            callback_data="install_bt3n",
                        ),
                    ],
                    [
                        InlineKeyboardButtonTG(
                            text=Strings.ADD_DEVICE_BTN,
                            callback_data="install_b4tn",
                        ),
                    ],
                    [
                        InlineKeyboardButtonTG(
                            text=Strings.CLOSE_TICKET_BTN,
                            callback_data="install5_btn",
                        ),
                    ],
                    [
                        InlineKeyboardButtonTG(
                            text=Strings.QUIT_WITHOUT_SAVING_BTN,
                            callback_data="install6_btn",
                        ),
                    ],
                ]
            ),
        )

    def echo(self):
        return SendMessageTG(
            chat_id=self.update_tg.message.chat.id,
            text=self.update_tg.message.text,
        )
