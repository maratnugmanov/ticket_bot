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

    async def post_method_tg(self, method_tg: MethodTG) -> SuccessTG | ErrorTG | None:
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

    async def make_delivery(
        self,
        method_generator: Callable[[], list[MethodTG]],
        ensure_delivery: bool = True,
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
                    response_tg = await self.post_method_tg(method_tg)
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
            success = await self.make_delivery(self.get_stateless_conversation)
        else:
            if self.state.script == Script.INITIAL_DATA:
                success = await self.make_delivery(self.get_device_conversation)
        return success

    def archive_choice_method_tg(self, text: str) -> MethodTG:
        if not isinstance(self.update_tg, CallbackQueryUpdateTG):
            raise TypeError(
                "Choice archiving method only works with CallbackQueryUpdateTG type"
            )
        chat_id = self.update_tg.callback_query.message.chat.id
        message_id = self.update_tg.callback_query.message.message_id
        # old_text = self.update_tg.callback_query.message.text
        logger.info(
            f"{self.log_prefix}Archiving choice being made by editing message #{message_id} to '{text}'."
        )
        method_tg = EditMessageTextTG(
            chat_id=chat_id,
            message_id=message_id,
            # text=f"<s>{old_text}</s>\n\n{String.YOU_HAVE_CHOSEN}: {string}.",
            text=text,
            # parse_mode="HTML",
        )
        return method_tg

    def get_stateless_conversation(self) -> list[MethodTG]:
        logger.info(
            f"{self.log_prefix}Starting new conversation with {self.user_db.full_name}."
        )
        if self.state is not None:
            raise ValueError("'self.state' should be None at this point.")
        methods_tg_list: list[MethodTG] = []
        if isinstance(self.update_tg, MessageUpdateTG):
            message_id = self.update_tg.message.message_id
            logger.info(
                f"{self.log_prefix}A message #{message_id} from "
                f"{self.user_db.full_name}."
            )
            logger.info(
                f"{self.log_prefix}Preparing main menu for {self.user_db.full_name}."
            )
            methods_tg_list.append(
                self.stateless_mainmenu_method_tg(f"{String.PICK_A_FUNCTION}.")
            )
        elif isinstance(self.update_tg, CallbackQueryUpdateTG):
            data = self.update_tg.callback_query.data
            message_id = self.update_tg.callback_query.message.message_id
            chat_id = self.update_tg.callback_query.message.chat.id
            logger.info(
                f"{self.log_prefix}data='{data}' from {self.user_db.full_name}."
            )
            if data == CallbackData.ENTER_TICKET_NUMBER:
                logger.info(
                    f"{self.log_prefix}data='{data}' is recognized "
                    "as a ticket number input. Preparing the answer "
                    f"for {self.user_db.full_name}."
                )
                self.next_state = StateJS(
                    action=Action.ENTER_TICKET_NUMBER,
                    script=Script.INITIAL_DATA,
                )
                methods_tg_list.append(
                    self.archive_choice_method_tg(String.CLOSE_TICKET_BTN)
                )
                methods_tg_list.append(
                    self.send_text_message_tg(f"{String.ENTER_TICKET_NUMBER}.")
                )
            elif data == CallbackData.ENABLE_HIRING_BTN:
                logger.info(
                    f"{self.log_prefix}data='{data}' is recognized "
                    "as enable hiring. Preparing the answer "
                    f"for {self.user_db.full_name}."
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
            elif data == CallbackData.DISABLE_HIRING_BTN:
                logger.info(
                    f"{self.log_prefix}data='{data}' is recognized "
                    "as disable hiring. Preparing the answer "
                    f"for {self.user_db.full_name}."
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
                logger.info(
                    f"{self.log_prefix}data='{data}' is not "
                    "recognized. Preparing Main Menu "
                    f"for {self.user_db.full_name}."
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
        logger.info(
            f"{self.log_prefix}Continuing conversation with {self.user_db.full_name}."
        )
        if self.state is None:
            raise ValueError("'self.state' cannot be None at this point.")
        methods_tg_list: list[MethodTG] = []
        if self.state.action == Action.ENTER_TICKET_NUMBER:
            logger.info(f"{self.log_prefix}Awaiting ticket number.")
            if isinstance(self.update_tg, MessageUpdateTG):
                if self.update_tg.message.text is not None:
                    message_text = self.update_tg.message.text
                    if re.fullmatch(r"\d+", message_text):
                        logger.info(
                            f"{self.log_prefix}Got correct "
                            f"ticket number: '{message_text}'."
                        )
                        self.next_state = StateJS(
                            action=Action.ENTER_CONTRACT_NUMBER,
                            script=self.state.script,
                            devices_list=self.state.devices_list,
                            device_index=0,
                            ticket_number=message_text,
                        )
                        methods_tg_list.append(
                            self.send_text_message_tg(
                                f"{String.ENTER_CONTRACT_NUMBER}."
                            )
                        )
                    else:
                        methods_tg_list.append(
                            self.send_text_message_tg(
                                f"{String.INCORRECT_TICKET_NUMBER}. "
                                f"{String.ENTER_TICKET_NUMBER}."
                            )
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
            logger.info(f"{self.log_prefix}Awaiting contract number.")
            if self.state.device_index is None:
                raise ValueError(
                    "'self.state.device_index' cannot be None at this point."
                )
            if isinstance(self.update_tg, MessageUpdateTG):
                if self.update_tg.message.text is not None:
                    message_text = self.update_tg.message.text
                    if re.fullmatch(r"\d+", message_text):
                        logger.info(
                            f"{self.log_prefix}Got correct "
                            f"contract number: '{message_text}'."
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_INSTALL_OR_RETURN,
                            script=self.state.script,
                            devices_list=self.state.devices_list,
                            device_index=self.state.device_index,
                            ticket_number=self.state.ticket_number,
                            contract_number=message_text,
                        )
                        methods_tg_list.append(
                            self.pick_install_or_return(
                                f"{String.PICK_INSTALL_OR_RETURN}."
                            )
                        )
                    else:
                        methods_tg_list.append(
                            self.send_text_message_tg(
                                f"{String.INCORRECT_CONTRACT_NUMBER}. "
                                f"{String.ENTER_CONTRACT_NUMBER}."
                            )
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
        elif self.state.action == Action.PICK_INSTALL_OR_RETURN:
            logger.info(
                f"{self.log_prefix}Awaiting install or return choice to be made."
            )
            if self.state.device_index is None:
                raise ValueError("device_index cannot be None at this point.")
            if isinstance(self.update_tg, CallbackQueryUpdateTG):
                expected_callback_data = [
                    CallbackData.INSTALL_DEVICE_BTN,
                    CallbackData.RETURN_DEVICE_BTN,
                ]
                data = self.update_tg.callback_query.data
                try:
                    received_callback_data = CallbackData(data)
                    if received_callback_data in expected_callback_data:
                        if received_callback_data == CallbackData.INSTALL_DEVICE_BTN:
                            is_defective = False
                        elif received_callback_data == CallbackData.RETURN_DEVICE_BTN:
                            is_defective = True
                        self.next_state = StateJS(
                            action=Action.PICK_DEVICE_TYPE,
                            script=self.state.script,
                            devices_list=self.state.devices_list,
                            device_index=self.state.device_index,
                            ticket_number=self.state.ticket_number,
                            contract_number=self.state.contract_number,
                        )
                        device_index = self.next_state.device_index
                        list_length = len(self.next_state.devices_list)
                        if device_index == list_length:
                            device = DeviceJS(
                                is_defective=is_defective,
                                type=None,
                                serial_number=None,
                                id=None,
                            )
                            self.next_state.devices_list.append(device)
                        elif device_index < list_length:
                            self.next_state.devices_list[
                                device_index
                            ].is_defective = is_defective
                        else:
                            error_msg = (
                                f"{self.log_prefix}Error: "
                                f"device_index={device_index} "
                                f"> list_length={list_length}. "
                                f"Expected: device_index <= list_length."
                            )
                            logger.error(error_msg)
                            raise ValueError(error_msg)
                        methods_tg_list.append(
                            self.archive_choice_method_tg(
                                String[received_callback_data.name]
                            )
                        )
                        methods_tg_list.append(
                            self.pick_device_type(f"{String.PICK_DEVICE_TYPE}.")
                        )
                    else:
                        raise ValueError
                except ValueError:
                    logger.info(
                        f"{self.log_prefix}Received invalid callback "
                        f"data='{data}' for device action selection."
                    )
                    methods_tg_list.append(
                        self.pick_install_or_return(
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
                    self.pick_install_or_return(
                        f"{String.DEVICE_ACTION_WAS_NOT_PICKED}. "
                        f"{String.PICK_INSTALL_OR_RETURN}."
                    )
                )
        elif self.state.action == Action.PICK_DEVICE_TYPE:
            logger.info(f"{self.log_prefix}Awaiting device type choice to be made.")
            if self.state.device_index is None:
                raise ValueError("device_index cannot be None at this point.")
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
                            devices_list=self.state.devices_list,
                            device_index=self.state.device_index,
                            ticket_number=self.state.ticket_number,
                            contract_number=self.state.contract_number,
                        )
                        device_index = self.next_state.device_index
                        device_type = DeviceTypeName[received_callback_data.name]
                        if self.next_state.devices_list[device_index].type is None:
                            self.next_state.devices_list[
                                device_index
                            ].type = device_type
                        else:
                            existing_type = self.next_state.devices_list[
                                device_index
                            ].type
                            error_msg = (
                                f"{self.log_prefix}Error: Device with "
                                f"index={device_index} already "
                                f"has type={existing_type}."
                            )
                            logger.error(error_msg)
                            raise ValueError(error_msg)
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
                    logger.info(
                        f"{self.log_prefix}Received invalid callback "
                        f"data='{data}' for device type selection."
                    )
                    methods_tg_list.append(
                        self.pick_device_type(
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
                    self.pick_device_type(
                        f"{String.DEVICE_TYPE_WAS_NOT_PICKED}. "
                        f"{String.PICK_DEVICE_TYPE} "
                        f"{String.FROM_OPTIONS_BELOW}."
                    )
                )
        elif self.state.action == Action.ENTER_SERIAL_NUMBER:
            logger.info(f"{self.log_prefix}Awaiting device serial number.")
            if self.state.device_index is None:
                raise ValueError("device_index cannot be None at this point.")
            if isinstance(self.update_tg, MessageUpdateTG):
                if self.update_tg.message.text is not None:
                    message_text = self.update_tg.message.text.upper()
                    if re.fullmatch(r"[\dA-Z]+", message_text):
                        logger.info(
                            f"{self.log_prefix}Got correct device "
                            f"serial number: '{message_text}'."
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            script=self.state.script,
                            devices_list=self.state.devices_list,
                            device_index=0,
                            ticket_number=self.state.ticket_number,
                            contract_number=self.state.contract_number,
                        )
                        device_index = self.state.device_index
                        if (
                            self.next_state.devices_list[device_index].serial_number
                            is None
                        ):
                            self.next_state.devices_list[
                                device_index
                            ].serial_number = message_text
                        else:
                            existing_serial_number = self.next_state.devices_list[
                                device_index
                            ].serial_number
                            error_msg = (
                                f"{self.log_prefix}Error: Device with "
                                f"index={device_index} already has "
                                f"serial_number={existing_serial_number}."
                            )
                            logger.error(error_msg)
                            raise ValueError(error_msg)
                        methods_tg_list.append(
                            self.pick_ticket_action(f"{String.PICK_TICKET_ACTION}.")
                        )
                    else:
                        methods_tg_list.append(
                            self.send_text_message_tg(
                                f"{String.INCORRECT_SERIAL_NUMBER}. "
                                f"{String.ENTER_SERIAL_NUMBER}."
                            )
                        )
                else:
                    methods_tg_list.append(
                        self.send_text_message_tg(
                            f"{String.INCORRECT_SERIAL_NUMBER}. "
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
        elif self.state.action == Action.PICK_TICKET_ACTION:
            logger.info(f"{self.log_prefix}Awaiting ticket menu choice to be made.")
            if isinstance(self.update_tg, CallbackQueryUpdateTG):
                expected_callback_data = [
                    CallbackData.EDIT_TICKET_NUMBER,
                    CallbackData.EDIT_CONTRACT_NUMBER,
                    CallbackData.QUIT_WITHOUT_SAVING_BTN,
                ]
                if len(self.state.devices_list) < 6:
                    expected_callback_data.append(CallbackData.ADD_DEVICE_BTN)
                if len(self.state.devices_list) > 0:
                    expected_callback_data.append(CallbackData.CLOSE_TICKET_BTN)
                all_devices_list = [
                    CallbackData.DEVICE_0,
                    CallbackData.DEVICE_1,
                    CallbackData.DEVICE_2,
                    CallbackData.DEVICE_3,
                    CallbackData.DEVICE_4,
                    CallbackData.DEVICE_5,
                ]
                expected_devices_list = all_devices_list[: len(self.state.devices_list)]
                if self.state.devices_list:
                    expected_callback_data.extend(expected_devices_list)
                data = self.update_tg.callback_query.data
                try:
                    received_callback_data = CallbackData(data)
                    if received_callback_data in expected_callback_data:
                        if received_callback_data == CallbackData.EDIT_TICKET_NUMBER:
                            self.next_state = StateJS(
                                action=Action.EDIT_TICKET_NUMBER,
                                script=self.state.script,
                                devices_list=self.state.devices_list,
                                device_index=self.state.device_index,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.EDIT_TICKET_NUMBER}."
                                )
                            )
                            methods_tg_list.append(
                                self.send_text_message_tg(
                                    f"{String.ENTER_NEW_TICKET_NUMBER}."
                                )
                            )
                        elif (
                            received_callback_data == CallbackData.EDIT_CONTRACT_NUMBER
                        ):
                            self.next_state = StateJS(
                                action=Action.EDIT_CONTRACT_NUMBER,
                                script=self.state.script,
                                devices_list=self.state.devices_list,
                                device_index=self.state.device_index,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.EDIT_CONTRACT_NUMBER}."
                                )
                            )
                            methods_tg_list.append(
                                self.send_text_message_tg(
                                    f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                                )
                            )
                        elif received_callback_data in all_devices_list:
                            device_index_string = received_callback_data[-1]
                            try:
                                callback_device_index = int(device_index_string)
                                self.next_state = StateJS(
                                    action=Action.PICK_DEVICE_ACTION,
                                    script=self.state.script,
                                    devices_list=self.state.devices_list,
                                    device_index=callback_device_index,
                                    ticket_number=self.state.ticket_number,
                                    contract_number=self.state.contract_number,
                                )
                                methods_tg_list.append(
                                    self.archive_choice_method_tg(
                                        f"{String.EDIT_DEVICE} "
                                        f"{callback_device_index + 1}."
                                    )
                                )
                                methods_tg_list.append(
                                    self.pick_device_action(
                                        f"{String.PICK_DEVICE_ACTION}."
                                    )
                                )
                            except ValueError:
                                logger.error(
                                    f"{self.log_prefix}Last symbol "
                                    f"of data='{data}' is not an "
                                    "integer string. int(data) failed."
                                )
                        elif received_callback_data == CallbackData.ADD_DEVICE_BTN:
                            self.next_state = StateJS(
                                action=Action.PICK_INSTALL_OR_RETURN,
                                script=self.state.script,
                                devices_list=self.state.devices_list,
                                device_index=len(self.state.devices_list),
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.ADD_DEVICE_BTN}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_install_or_return(
                                    f"{String.PICK_INSTALL_OR_RETURN}."
                                )
                            )
                        elif received_callback_data == CallbackData.CLOSE_TICKET_BTN:
                            self.next_state = StateJS(
                                action=Action.CONFIRM_CLOSE_TICKET,
                                script=self.state.script,
                                devices_list=self.state.devices_list,
                                device_index=self.state.device_index,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.CLOSE_TICKET_BTN}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_confirm_close_ticket(
                                    f"{String.CONFIRM_YOU_WANT_TO_CLOSE_TICKET}."
                                )
                            )
                        elif (
                            received_callback_data
                            == CallbackData.QUIT_WITHOUT_SAVING_BTN
                        ):
                            self.next_state = StateJS(
                                action=Action.CONFIRM_QUIT_WITHOUT_SAVING,
                                script=self.state.script,
                                devices_list=self.state.devices_list,
                                device_index=self.state.device_index,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.QUIT_WITHOUT_SAVING_BTN}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_confirm_quit(
                                    f"{String.ARE_YOU_SURE_YOU_WANT_TO_QUIT_WITHOUT_SAVING}"
                                )
                            )
                    else:
                        raise ValueError
                except ValueError:
                    logger.info(
                        f"{self.log_prefix}Received invalid callback "
                        f"data='{data}' for ticket menu selection."
                    )
                    methods_tg_list.append(
                        self.pick_ticket_action(
                            f"{String.GOT_UNEXPECTED_DATA}. "
                            f"{String.PICK_TICKET_ACTION}."
                        )
                    )
            elif isinstance(self.update_tg, MessageUpdateTG):
                logger.info(
                    f"{self.log_prefix}User {self.user_db.full_name} "
                    "responded with message while callback data "
                    "was awaited."
                )
                methods_tg_list.append(
                    self.pick_ticket_action(
                        f"{String.TICKET_ACTION_WAS_NOT_PICKED}. "
                        f"{String.PICK_TICKET_ACTION}."
                    )
                )
        elif self.state.action == Action.EDIT_TICKET_NUMBER:
            logger.info(f"{self.log_prefix}Awaiting new ticket number.")
            if isinstance(self.update_tg, MessageUpdateTG):
                if self.update_tg.message.text is not None:
                    message_text = self.update_tg.message.text
                    if re.fullmatch(r"\d+", message_text):
                        logger.info(
                            f"{self.log_prefix}Got correct "
                            f"new ticket number: '{message_text}'."
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            script=self.state.script,
                            devices_list=self.state.devices_list,
                            device_index=self.state.device_index,
                            ticket_number=message_text,
                            contract_number=self.state.contract_number,
                        )
                        methods_tg_list.append(
                            self.pick_ticket_action(
                                f"{String.TICKET_NUMBER_WAS_EDITED}. "
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                    else:
                        methods_tg_list.append(
                            self.send_text_message_tg(
                                f"{String.INCORRECT_TICKET_NUMBER}. "
                                f"{String.ENTER_NEW_TICKET_NUMBER}."
                            )
                        )
                else:
                    methods_tg_list.append(
                        self.send_text_message_tg(
                            f"{String.INCORRECT_TICKET_NUMBER}. "
                            f"{String.ENTER_NEW_TICKET_NUMBER}."
                        )
                    )
            elif isinstance(self.update_tg, CallbackQueryUpdateTG):
                methods_tg_list.append(
                    self.send_text_message_tg(
                        f"{String.GOT_DATA_NOT_TICKET_NUMBER}. "
                        f"{String.ENTER_NEW_TICKET_NUMBER}."
                    )
                )
        elif self.state.action == Action.EDIT_CONTRACT_NUMBER:
            logger.info(f"{self.log_prefix}Awaiting new contract number.")
            if isinstance(self.update_tg, MessageUpdateTG):
                if self.update_tg.message.text is not None:
                    message_text = self.update_tg.message.text
                    if re.fullmatch(r"\d+", message_text):
                        logger.info(
                            f"{self.log_prefix}Got correct new "
                            f"contract number: '{message_text}'."
                        )
                        self.next_state = StateJS(
                            action=Action.PICK_TICKET_ACTION,
                            script=self.state.script,
                            devices_list=self.state.devices_list,
                            device_index=self.state.device_index,
                            ticket_number=self.state.ticket_number,
                            contract_number=message_text,
                        )
                        methods_tg_list.append(
                            self.pick_ticket_action(
                                f"{String.CONTRACT_NUMBER_WAS_EDITED}. "
                                f"{String.PICK_TICKET_ACTION}."
                            )
                        )
                    else:
                        methods_tg_list.append(
                            self.send_text_message_tg(
                                f"{String.INCORRECT_CONTRACT_NUMBER}. "
                                f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                            )
                        )
                else:
                    methods_tg_list.append(
                        self.send_text_message_tg(
                            f"{String.INCORRECT_CONTRACT_NUMBER}. "
                            f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                        )
                    )
            elif isinstance(self.update_tg, CallbackQueryUpdateTG):
                methods_tg_list.append(
                    self.send_text_message_tg(
                        f"{String.GOT_DATA_NOT_CONTRACT_NUMBER}. "
                        f"{String.ENTER_NEW_CONTRACT_NUMBER}."
                    )
                )
        elif self.state.action == Action.CONFIRM_CLOSE_TICKET:
            logger.info(f"{self.log_prefix}Awaiting close ticket confirmation.")
            if self.state.device_index is None:
                raise ValueError(
                    "'self.state.device_index' cannot be None at this point."
                )
            if isinstance(self.update_tg, CallbackQueryUpdateTG):
                expected_callback_data = [
                    CallbackData.CONFIRM_CLOSE_TICKET_BTN,
                    CallbackData.CHANGED_MY_MIND_BTN,
                ]
                data = self.update_tg.callback_query.data
                try:
                    received_callback_data = CallbackData(data)
                    if received_callback_data in expected_callback_data:
                        if (
                            received_callback_data
                            == CallbackData.CONFIRM_CLOSE_TICKET_BTN
                        ):
                            self.next_state = None
                            self.user_db.state_json = None
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.CONFIRM_CLOSE_TICKET_BTN}."
                                )
                            )
                            methods_tg_list.append(
                                self.stateless_mainmenu_method_tg(
                                    f"{String.YOU_CLOSED_TICKET}. {String.PICK_A_FUNCTION}."
                                )
                            )
                        elif received_callback_data == CallbackData.CHANGED_MY_MIND_BTN:
                            self.next_state = StateJS(
                                action=Action.PICK_TICKET_ACTION,
                                script=self.state.script,
                                devices_list=self.state.devices_list,
                                device_index=self.state.device_index,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.CHANGED_MY_MIND_BTN}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_ticket_action(f"{String.PICK_TICKET_ACTION}.")
                            )
                    else:
                        raise ValueError
                except ValueError:
                    logger.info(
                        f"{self.log_prefix}Received invalid callback "
                        f"data='{data}' for close ticket "
                        "confirmation menu selection."
                    )
                    methods_tg_list.append(
                        self.pick_confirm_close_ticket(
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
                    self.pick_confirm_close_ticket(
                        f"{String.CLOSE_TICKET_ACTION_WAS_NOT_PICKED}. "
                        f"{String.CONFIRM_YOU_WANT_TO_CLOSE_TICKET}"
                    )
                )
        elif self.state.action == Action.CONFIRM_QUIT_WITHOUT_SAVING:
            logger.info(f"{self.log_prefix}Awaiting quit without saving confirmation.")
            if self.state.device_index is None:
                raise ValueError(
                    "'self.state.device_index' cannot be None at this point."
                )
            if isinstance(self.update_tg, CallbackQueryUpdateTG):
                expected_callback_data = [
                    CallbackData.CONFIRM_QUIT_BTN,
                    CallbackData.CHANGED_MY_MIND_BTN,
                ]
                data = self.update_tg.callback_query.data
                try:
                    received_callback_data = CallbackData(data)
                    if received_callback_data in expected_callback_data:
                        if received_callback_data == CallbackData.CONFIRM_QUIT_BTN:
                            self.next_state = None
                            self.user_db.state_json = None
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.CONFIRM_QUIT_BTN}."
                                )
                            )
                            methods_tg_list.append(
                                self.stateless_mainmenu_method_tg(
                                    f"{String.YOU_QUIT_WITHOUT_SAVING}. {String.PICK_A_FUNCTION}."
                                )
                            )
                        elif received_callback_data == CallbackData.CHANGED_MY_MIND_BTN:
                            self.next_state = StateJS(
                                action=Action.PICK_TICKET_ACTION,
                                script=self.state.script,
                                devices_list=self.state.devices_list,
                                device_index=self.state.device_index,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.CHANGED_MY_MIND_BTN}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_ticket_action(f"{String.PICK_TICKET_ACTION}.")
                            )
                    else:
                        raise ValueError
                except ValueError:
                    logger.info(
                        f"{self.log_prefix}Received invalid callback "
                        f"data='{data}' for quit without saving "
                        "confirmation menu selection."
                    )
                    methods_tg_list.append(
                        self.pick_confirm_quit(
                            f"{String.GOT_UNEXPECTED_DATA}. "
                            f"{String.ARE_YOU_SURE_YOU_WANT_TO_QUIT_WITHOUT_SAVING}"
                        )
                    )
            elif isinstance(self.update_tg, MessageUpdateTG):
                logger.info(
                    f"{self.log_prefix}User {self.user_db.full_name} "
                    "responded with message while callback data "
                    "was awaited."
                )
                methods_tg_list.append(
                    self.pick_confirm_quit(
                        f"{String.QUIT_WITHOUT_SAVING_ACTION_WAS_NOT_PICKED}. "
                        f"{String.ARE_YOU_SURE_YOU_WANT_TO_QUIT_WITHOUT_SAVING}"
                    )
                )
        elif self.state.action == Action.PICK_DEVICE_ACTION:
            logger.info(f"{self.log_prefix}Awaiting device menu choice to be made.")
            if self.state.device_index is None:
                raise ValueError(
                    "'self.state.device_index' cannot be None at this point."
                )
            if isinstance(self.update_tg, CallbackQueryUpdateTG):
                expected_callback_data = [
                    CallbackData.EDIT_DEVICE_TYPE,
                    CallbackData.EDIT_SERIAL_NUMBER,
                    CallbackData.EDIT_TICKET,
                    CallbackData.DELETE_DEVICE_BTN,
                ]
                device_index = self.state.device_index
                if self.state.devices_list[device_index].is_defective is True:
                    expected_callback_data.append(CallbackData.RETURN_DEVICE_BTN)
                elif self.state.devices_list[device_index].is_defective is False:
                    expected_callback_data.append(CallbackData.INSTALL_DEVICE_BTN)
                else:
                    raise ValueError("device_index is not True or False.")
                data = self.update_tg.callback_query.data
                try:
                    received_callback_data = CallbackData(data)
                    if received_callback_data in expected_callback_data:
                        if received_callback_data == CallbackData.RETURN_DEVICE_BTN:
                            self.next_state = StateJS(
                                action=Action.EDIT_INSTALL_OR_RETURN,
                                script=self.state.script,
                                devices_list=self.state.devices_list,
                                device_index=device_index,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.EDIT_INSTALL_OR_RETURN}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_install_or_return(
                                    f"{String.PICK_INSTALL_OR_RETURN}."
                                )
                            )
                        elif received_callback_data == CallbackData.INSTALL_DEVICE_BTN:
                            self.next_state = StateJS(
                                action=Action.EDIT_INSTALL_OR_RETURN,
                                script=self.state.script,
                                devices_list=self.state.devices_list,
                                device_index=self.state.device_index,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.EDIT_INSTALL_OR_RETURN}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_install_or_return(
                                    f"{String.PICK_INSTALL_OR_RETURN}."
                                )
                            )
                        elif received_callback_data == CallbackData.EDIT_SERIAL_NUMBER:
                            self.next_state = StateJS(
                                action=Action.EDIT_SERIAL_NUMBER,
                                script=self.state.script,
                                devices_list=self.state.devices_list,
                                device_index=self.state.device_index,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.EDIT_SERIAL_NUMBER}."
                                )
                            )
                            methods_tg_list.append(
                                self.send_text_message_tg(
                                    f"{String.ENTER_NEW_SERIAL_NUMBER}."
                                )
                            )
                        elif received_callback_data == CallbackData.EDIT_DEVICE_TYPE:
                            self.next_state = StateJS(
                                action=Action.EDIT_DEVICE_TYPE,
                                script=self.state.script,
                                device_index=self.state.device_index,
                                devices_list=self.state.devices_list,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.EDIT_DEVICE_TYPE}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_device_type(f"{String.PICK_DEVICE_TYPE}.")
                            )
                        elif received_callback_data == CallbackData.EDIT_TICKET:
                            self.next_state = StateJS(
                                action=Action.PICK_TICKET_ACTION,
                                script=self.state.script,
                                devices_list=self.state.devices_list,
                                device_index=self.state.device_index,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                            )
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.RETURNING_TO_TICKET}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_ticket_action(f"{String.PICK_TICKET_ACTION}.")
                            )
                        elif received_callback_data == CallbackData.DELETE_DEVICE_BTN:
                            self.next_state = StateJS(
                                action=Action.PICK_TICKET_ACTION,
                                script=self.state.script,
                                devices_list=self.state.devices_list,
                                device_index=0,
                                ticket_number=self.state.ticket_number,
                                contract_number=self.state.contract_number,
                            )
                            devices_list = self.state.devices_list.copy()
                            device_index = self.state.device_index
                            if 0 <= device_index < len(devices_list):
                                del devices_list[device_index]
                            else:
                                raise IndexError(
                                    f"List index out of range: {device_index}"
                                )
                            self.next_state.devices_list = devices_list
                            methods_tg_list.append(
                                self.archive_choice_method_tg(
                                    f"{String.DEVICE_WAS_DELETED_FROM_TICKET}."
                                )
                            )
                            methods_tg_list.append(
                                self.pick_ticket_action(f"{String.PICK_TICKET_ACTION}.")
                            )
                    else:
                        raise ValueError
                except ValueError:
                    logger.info(
                        f"{self.log_prefix}Received invalid callback "
                        f"data='{data}' for device menu action "
                        "selection."
                    )
                    methods_tg_list.append(
                        self.pick_device_action(
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
                    self.pick_device_action(
                        f"{String.DEVICE_ACTION_WAS_NOT_PICKED}. "
                        f"{String.PICK_DEVICE_ACTION}."
                    )
                )
        elif self.state.action == Action.EDIT_INSTALL_OR_RETURN:
            logger.info(
                f"{self.log_prefix}Awaiting changing install or return choice to be made."
            )
            if self.state.device_index is None:
                raise ValueError("device_index cannot be None at this point.")
            if isinstance(self.update_tg, CallbackQueryUpdateTG):
                expected_callback_data = [
                    CallbackData.INSTALL_DEVICE_BTN,
                    CallbackData.RETURN_DEVICE_BTN,
                ]
                data = self.update_tg.callback_query.data
                try:
                    received_callback_data = CallbackData(data)
                    if received_callback_data in expected_callback_data:
                        if received_callback_data == CallbackData.INSTALL_DEVICE_BTN:
                            is_defective = False
                        elif received_callback_data == CallbackData.RETURN_DEVICE_BTN:
                            is_defective = True
                        self.next_state = StateJS(
                            action=Action.PICK_DEVICE_ACTION,
                            script=self.state.script,
                            devices_list=self.state.devices_list,
                            device_index=self.state.device_index,
                            ticket_number=self.state.ticket_number,
                            contract_number=self.state.contract_number,
                        )
                        device_index = self.next_state.device_index
                        list_length = len(self.next_state.devices_list)
                        if device_index == list_length:
                            device = DeviceJS(
                                is_defective=is_defective, type=None, serial_number=None
                            )
                            self.next_state.devices_list.append(device)
                        elif device_index < list_length:
                            self.next_state.devices_list[
                                device_index
                            ].is_defective = is_defective
                        else:
                            error_msg = (
                                f"{self.log_prefix}Error: "
                                f"device_index={device_index} > "
                                f"list_length={list_length}. "
                                f"Expected: device_index <= list_length."
                            )
                            logger.error(error_msg)
                            raise ValueError(error_msg)
                        methods_tg_list.append(
                            self.archive_choice_method_tg(
                                String[received_callback_data.name]
                            )
                        )
                        methods_tg_list.append(
                            self.pick_device_action(f"{String.PICK_DEVICE_ACTION}.")
                        )
                    else:
                        raise ValueError
                except ValueError:
                    logger.info(
                        f"{self.log_prefix}Received invalid callback "
                        f"data='{data}' for device action selection."
                    )
                    methods_tg_list.append(
                        self.pick_install_or_return(
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
                    self.pick_install_or_return(
                        f"{String.DEVICE_ACTION_WAS_NOT_PICKED}. "
                        f"{String.PICK_INSTALL_OR_RETURN}."
                    )
                )
        elif self.state.action == Action.EDIT_DEVICE_TYPE:
            logger.info(
                f"{self.log_prefix}Awaiting changing device type choice to be made."
            )
            if self.state.device_index is None:
                raise ValueError("device_index cannot be None at this point.")
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
                            action=Action.PICK_DEVICE_ACTION,
                            script=self.state.script,
                            devices_list=self.state.devices_list,
                            device_index=self.state.device_index,
                            ticket_number=self.state.ticket_number,
                            contract_number=self.state.contract_number,
                        )
                        device_index = self.next_state.device_index
                        device_type = DeviceTypeName[received_callback_data.name]
                        if self.next_state.devices_list[device_index].type is not None:
                            self.next_state.devices_list[
                                device_index
                            ].type = device_type
                        else:
                            existing_type = self.next_state.devices_list[
                                device_index
                            ].type
                            error_msg = (
                                f"{self.log_prefix}Error: Device with "
                                f"index={device_index} had type=None "
                                "prior to editing."
                            )
                            logger.error(error_msg)
                            raise ValueError(error_msg)
                        methods_tg_list.append(
                            self.archive_choice_method_tg(
                                String[received_callback_data.name]
                            )
                        )
                        methods_tg_list.append(
                            self.pick_device_action(f"{String.PICK_DEVICE_ACTION}.")
                        )
                    else:
                        raise ValueError
                except ValueError:
                    logger.info(
                        f"{self.log_prefix}Received invalid callback "
                        f"data='{data}' for device type selection."
                    )
                    methods_tg_list.append(
                        self.pick_device_type(
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
                    self.pick_device_type(
                        f"{String.DEVICE_TYPE_WAS_NOT_PICKED}. "
                        f"{String.PICK_DEVICE_TYPE} "
                        f"{String.FROM_OPTIONS_BELOW}."
                    )
                )
        elif self.state.action == Action.EDIT_SERIAL_NUMBER:
            logger.info(f"{self.log_prefix}Awaiting new device serial number.")
            if self.state.device_index is None:
                raise ValueError(
                    "'self.state.device_index' cannot be None at this point."
                )
            if (
                isinstance(self.update_tg, MessageUpdateTG)
                and self.update_tg.message.text
            ):
                message_text = self.update_tg.message.text.upper()
                if re.fullmatch(r"[\dA-Z]+", message_text):
                    logger.info(
                        f"{self.log_prefix}Got correct new device "
                        f"serial number: '{message_text}'."
                    )
                    self.next_state = StateJS(
                        action=Action.PICK_DEVICE_ACTION,
                        script=self.state.script,
                        devices_list=self.state.devices_list,
                        device_index=self.state.device_index,
                        ticket_number=self.state.ticket_number,
                        contract_number=self.state.contract_number,
                    )
                    device_index = self.state.device_index
                    if (
                        self.next_state.devices_list[device_index].serial_number
                        is not None
                    ):
                        self.next_state.devices_list[
                            device_index
                        ].serial_number = message_text
                    else:
                        error_msg = (
                            f"{self.log_prefix}Internal logic error: "
                            "Device has no serial_number to edit."
                        )
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                    methods_tg_list.append(
                        self.send_text_message_tg(
                            f"{String.SERIAL_NUMBER_WAS_CHANGED}."
                        )
                    )
                    methods_tg_list.append(
                        self.pick_device_action(f"{String.PICK_DEVICE_ACTION}.")
                    )
                else:
                    methods_tg_list.append(
                        self.send_text_message_tg(
                            f"{String.INCORRECT_SERIAL_NUMBER}. "
                            f"{String.ENTER_NEW_SERIAL_NUMBER}."
                        )
                    )
            elif isinstance(self.update_tg, CallbackQueryUpdateTG):
                methods_tg_list.append(
                    self.send_text_message_tg(
                        f"{String.GOT_DATA_NOT_SERIAL_NUMBER}. "
                        f"{String.ENTER_NEW_SERIAL_NUMBER}."
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
                            text=String.RETURN_DEVICE_BTN,
                            callback_data=CallbackData.RETURN_DEVICE_BTN,
                        ),
                    ],
                ]
            ),
        )

    def pick_ticket_action(self, text: str = f"{String.PICK_TICKET_ACTION}."):
        if (
            self.next_state
            and self.next_state.ticket_number
            and self.next_state.contract_number
            # and self.next_state.devices_list
        ):
            ticket_number = self.next_state.ticket_number
            contract_number = self.next_state.contract_number
            devices_list = self.next_state.devices_list
        elif (
            self.state and self.state.ticket_number and self.state.contract_number
            # and self.state.devices_list
        ):
            ticket_number = self.state.ticket_number
            contract_number = self.state.contract_number
            devices_list = self.state.devices_list
        else:
            raise ValueError(
                "The ticket menu only works with ticket number and "
                "contract number already being filled in."
                # "The ticket menu only works with state/next_state having at "
                # "least one device being filled in."
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
            if not isinstance(device.type, DeviceTypeName):
                error_msg = (
                    f"{self.log_prefix}CRITICAL: device.type is not DeviceTypeName."
                )
                logger.error(error_msg)
                raise AssertionError(error_msg)
            device_type = String[device.type.name]
            device_serial_number = device.serial_number
            device_button_array.append(
                [
                    InlineKeyboardButtonTG(
                        text=(
                            f"{device_number}. "
                            f"{device_icon} {device_type} "
                            f"{device_serial_number} >>"
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
        if len(devices_list) < 6:
            inline_keyboard_array.append(add_device_button)
        if len(devices_list) > 0:
            inline_keyboard_array.append(close_ticket_button)
        inline_keyboard_array.append(quit_without_saving_button)
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard_array),
        )

    def pick_device_action(self, text: str = f"{String.PICK_DEVICE_ACTION}."):
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

    def pick_confirm_close_ticket(
        self, text: str = f"{String.CONFIRM_YOU_WANT_TO_CLOSE_TICKET}."
    ):
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

    def pick_confirm_quit(
        self, text: str = f"{String.ARE_YOU_SURE_YOU_WANT_TO_QUIT_WITHOUT_SAVING}"
    ):
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
