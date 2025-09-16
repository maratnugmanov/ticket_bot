from __future__ import annotations
import inspect
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine
from zoneinfo import ZoneInfo
import httpx
from pydantic import ValidationError
from sqlalchemy import select, exists, func
from sqlalchemy.orm import selectinload
from src.core.config import settings
from src.core.logger import logger
from src.core.router import router
from src.core.callbacks import cb
from src.core.decorators import require_ticket_context, require_writeoff_context
from src.core.ticket_service import TicketService
from src.core.enums import (
    RoleName,
    DeviceTypeName,
    DeviceStatus,
    ValidationMode,
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

    @property
    def _relevant_state(self) -> StateJS | None:
        """
        Returns the most relevant state object,
        prioritizing next_state over state.
        """
        return self.next_state or self.state

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
        method_tg_list: list[MethodTG],
        ensure_delivery: bool = True,
    ) -> bool:
        def _persist_next_state():
            """Saves the next state to the user's database object if it's a valid state."""
            if self.next_state is None:
                self.user_db.state_json = None
            elif isinstance(self.next_state, StateJS):
                self.user_db.state_json = self.next_state.model_dump_json(
                    exclude_none=True
                )

        if not method_tg_list:
            _persist_next_state()
            return True
        response_tg: SuccessTG | ErrorTG | None
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
        initial_state_json = self.user_db.state_json
        command_string: str | None = None
        if isinstance(self.update_tg, CallbackQueryUpdateTG):
            command_string = self.update_tg.callback_query.data
            logger.info(f"{self.log_prefix}Got callback data '{command_string}'.")
        elif isinstance(self.update_tg, MessageUpdateTG):
            if self.state and self.state.pending_command_prefix:
                if self.update_tg.message.text is not None:
                    original_text = self.update_tg.message.text
                    text = original_text.strip()
                    if text != original_text:
                        logger.info(
                            f"{self.log_prefix}Got message with text "
                            f"'{original_text}' (processed as '{text}')."
                        )
                    else:
                        logger.info(f"{self.log_prefix}Got message with text '{text}'.")
                else:
                    logger.info(f"{self.log_prefix}Got message with no text.")
                    text = ""
                command_string = f"{self.state.pending_command_prefix}:{text}"
            elif self.update_tg.message.text == "/start":
                command_string = cb.menu.main()
        methods_tg_list: list[MethodTG] = []
        if command_string:
            methods_tg_list = await router.process(command_string, self)
        if not methods_tg_list:
            # This block handles cases where no route was found,
            # or it was an unhandled text message.
            if isinstance(self.update_tg, CallbackQueryUpdateTG):
                methods_tg_list.append(
                    self._build_edit_to_text_message(f"{String.GOT_UNEXPECTED_DATA}.")
                )
            methods_tg_list.append(
                self._build_main_menu(
                    f"{String.GOT_UNEXPECTED_DATA}. {String.PICK_A_FUNCTION}."
                )
            )
            self.next_state = None
        success = await self._make_delivery(methods_tg_list)
        if success:
            final_state_json = self.user_db.state_json
            if initial_state_json == final_state_json:
                logger.info(f"{self.log_prefix}Conversation state unchanged.")
            else:
                logger.info(
                    f"{self.log_prefix}Conversation state changed from "
                    f"{initial_state_json} to {final_state_json}."
                )
        return success

    async def _get_ticket_if_eligible(
        self, ticket_id: int, loader_options: list | None = None
    ) -> TicketDB | String:
        ticket = await self.session.get(
            TicketDB,
            ticket_id,
            options=loader_options,
        )
        if not ticket:
            result: TicketDB | String = String.TICKET_WAS_NOT_FOUND
        elif not (ticket.user_id == self.user_db.id or self.user_db.is_manager):
            result = String.FOREIGN_TICKET
        else:
            result = ticket
        return result

    def _build_new_text_message(self, text: str) -> SendMessageTG:
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
        )

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

    def _build_edit_to_callback_button_text(
        self, prefix_text: str = "", suffix_text: str = ""
    ) -> EditMessageTextTG:
        """Modifies callback message text to the string provided."""

        def _get_callback_button_text() -> str:
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
        button_text = _get_callback_button_text()
        logger.info(
            f"{self.log_prefix}Editing message id={message_id} text "
            f"to button text '{button_text}'."
        )
        method_tg = EditMessageTextTG(
            chat_id=chat_id,
            message_id=message_id,
            text=f"{prefix_text} {button_text} {suffix_text}".strip(),
        )
        return method_tg

    def _build_main_menu(
        self, text: str = f"{String.PICK_A_FUNCTION}."
    ) -> SendMessageTG:
        def _build_main_menu_keyboard_rows() -> list[list[InlineKeyboardButtonTG]]:
            inline_keyboard_rows = []
            if self.user_db.is_engineer:
                inline_keyboard_rows.append(
                    [
                        InlineKeyboardButtonTG(
                            text=String.ADD_TICKET_BTN,
                            callback_data=cb.ticket.create_start(),
                        )
                    ],
                )
                inline_keyboard_rows.append(
                    [
                        InlineKeyboardButtonTG(
                            text=String.TICKETS_BTN,
                            callback_data=cb.ticket.list_page(0),
                        ),
                        InlineKeyboardButtonTG(
                            text=String.WRITEOFF_DEVICES_BTN,
                            callback_data=cb.writeoff.list_page(0),
                        ),
                    ],
                )
            if self.user_db.is_manager:
                inline_keyboard_rows.append(
                    [
                        InlineKeyboardButtonTG(
                            text=String.FORM_REPORT_BTN,
                            callback_data=cb.report.create_start(),
                        )
                    ],
                )
                if self.user_db.is_hiring:
                    inline_keyboard_rows.append(
                        [
                            InlineKeyboardButtonTG(
                                text=String.DISABLE_HIRING_BTN,
                                callback_data=cb.user.disable_hiring(),
                            )
                        ],
                    )
                else:
                    inline_keyboard_rows.append(
                        [
                            InlineKeyboardButtonTG(
                                text=String.ENABLE_HIRING_BTN,
                                callback_data=cb.user.enable_hiring(),
                            )
                        ],
                    )
            return inline_keyboard_rows

        main_menu_keyboard_rows = _build_main_menu_keyboard_rows()
        if main_menu_keyboard_rows:
            text = text
            reply_markup = InlineKeyboardMarkupTG(
                inline_keyboard=main_menu_keyboard_rows
            )
        else:
            text = f"{String.NO_FUNCTIONS_ARE_AVAILABLE}."
            reply_markup = None
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=reply_markup,
        )

    def _drop_state_goto_main_menu(self, text: str | None = None) -> SendMessageTG:
        logger.info(f"{self.log_prefix}Going back to main menu.")
        self.next_state = None
        if text:
            return self._build_main_menu(text)
        return self._build_main_menu()

    def _get_ticket_overview(self, ticket: TicketDB) -> str:
        user_timezone = ZoneInfo(self.user_db.timezone)
        months = {
            1: String.JAN,
            2: String.FEB,
            3: String.MAR,
            4: String.APR,
            5: String.MAY,
            6: String.JUN,
            7: String.JUL,
            8: String.AUG,
            9: String.SEP,
            10: String.OCT,
            11: String.NOV,
            12: String.DEC,
        }
        closed_status = (
            String.CLOSED_TICKET_ICON if ticket.is_closed else String.ATTENTION_ICON
        )
        ticket_created_at_local_timestamp = ticket.created_at.astimezone(user_timezone)
        day_number = ticket_created_at_local_timestamp.day
        month_number = ticket_created_at_local_timestamp.month
        hh_mm = ticket_created_at_local_timestamp.strftime("%H:%M")
        return (
            f"{closed_status} "  # nbsp
            f"{String.NUMBER_SYMBOL} "  # nbsp
            f"{ticket.number} {String.FROM_X.value} "
            f"{day_number} {months[month_number]} "  # nbsp
            f"{hh_mm} >>"  # nbsp
        )

    def _get_device_type_statuses(
        self, device_type: DeviceTypeDB
    ) -> dict[DeviceStatus, tuple[String, String]]:
        """Returns a dictionary of status enum as a key
        and its icon and its localized name as a value tuple."""
        device_type_all_flags = {
            DeviceStatus.RENTED: (
                device_type.rentable,
                String.RENTED_DEVICE_ICON,
                String.RENT_DEVICE_BTN,
            ),
            DeviceStatus.SOLD: (
                device_type.sellable,
                String.SOLD_DEVICE_ICON,
                String.SELL_DEVICE_BTN,
            ),
            DeviceStatus.RETURNED: (
                device_type.returnable,
                String.RETURNED_DEVICE_ICON,
                String.RETURN_DEVICE_BTN,
            ),
        }
        return {
            status: (status_icon, status_name)
            for status, (
                is_possible,
                status_icon,
                status_name,
            ) in device_type_all_flags.items()
            if is_possible
        }

    def _device_status_icon_if_valid_for_ticket_closing(
        self, device: DeviceDB
    ) -> String | None:
        device_type_statuses = self._get_device_type_statuses(device.type)
        if (
            device.status is not None
            and device.status in device_type_statuses
            and bool(device.serial_number) == device.type.has_serial_number
        ):
            return device_type_statuses[device.status][0]
        else:
            return None

    def _ticket_valid_for_closing(self, ticket: TicketDB) -> bool:
        if not (
            ticket.number
            and ticket.contract
            and ticket.contract.number
            and ticket.devices
        ):
            return False
        for device in ticket.devices:
            if not self._device_status_icon_if_valid_for_ticket_closing(device):
                return False
        return True

    async def _build_tickets_list(
        self, page: int = 0, text: str = f"{String.AVAILABLE_TICKETS_ACTIONS}."
    ) -> SendMessageTG:
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
        last_page = total_pages - 1
        if page < 0:
            logger.warning(
                f"{self.log_prefix}Current tickets list page "
                f"is negative (page={page}). Setting it to 0. "
            )
            page = 0
        elif page > last_page:
            page = last_page
        logger.info(f"{self.log_prefix}User is on page {page + 1} of {total_pages}.")
        add_ticket_button = InlineKeyboardButtonTG(
            text=String.ADD_TICKET_BTN,
            callback_data=cb.ticket.create_start(),
        )
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = [[add_ticket_button]]
        recent_ticket_index_offset = page * tickets_per_page
        current_page_tickets_result = await self.session.scalars(
            select(TicketDB)
            .where(
                TicketDB.user_id == self.user_db.id,
                TicketDB.created_at >= cutoff_date,
            )
            .order_by(TicketDB.created_at.desc())
            .offset(recent_ticket_index_offset)
            .limit(tickets_per_page)
        )
        current_page_tickets = list(current_page_tickets_result)
        for ticket in current_page_tickets:
            ticket_button = [
                InlineKeyboardButtonTG(
                    text=self._get_ticket_overview(ticket),
                    callback_data=cb.ticket.view(ticket.id),
                ),
            ]
            inline_keyboard.append(ticket_button)
        prev_next_buttons_row: list[InlineKeyboardButtonTG] = []
        if total_recent_tickets > tickets_per_page:
            prev_button = InlineKeyboardButtonTG(
                text=f"{String.PREV_ONES}",
                callback_data=cb.ticket.list_page(page + 1),
            )
            next_button = InlineKeyboardButtonTG(
                text=f"{String.NEXT_ONES}",
                callback_data=cb.ticket.list_page(page - 1),
            )
            if page < last_page:
                prev_next_buttons_row.append(prev_button)
            if page > 0:
                prev_next_buttons_row.append(next_button)
        if prev_next_buttons_row:
            inline_keyboard.append(prev_next_buttons_row)
        return_button = InlineKeyboardButtonTG(
            text=String.MAIN_MENU,
            callback_data=cb.menu.main(),
        )
        inline_keyboard.append([return_button])
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard),
        )

    def _build_ticket_view(
        self, ticket: TicketDB, text: str = f"{String.AVAILABLE_TICKET_ACTIONS}."
    ) -> SendMessageTG:
        ticket_number_button = InlineKeyboardButtonTG(
            text=f"{String.TICKET} {String.NUMBER_SYMBOL} {ticket.number} {String.EDIT}",
            callback_data=cb.ticket.edit_number(ticket.id),
        )
        contract_text = (
            f"{String.CONTRACT} {String.NUMBER_SYMBOL} {ticket.contract.number}"  # nbsp
            if ticket.contract
            else f"{String.ATTENTION_ICON} {String.ENTER_CONTRACT_NUMBER}"  # nbsp
        )
        contract_number_button = InlineKeyboardButtonTG(
            text=f"{contract_text} {String.EDIT}",
            callback_data=cb.ticket.edit_contract(ticket.id),
        )
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = [
            [ticket_number_button],
            [contract_number_button],
        ]
        for index, device in enumerate(ticket.devices):
            device_number = index + 1
            device_icon = (
                self._device_status_icon_if_valid_for_ticket_closing(device)
                or String.ATTENTION_ICON
            )
            device_type_name = String[device.type.name.name]
            if device.serial_number is not None:
                device_button_text = (
                    f"{device_number}. "  # nbsp
                    f"{device_icon} {device_type_name} "  # nbsp
                    f"{device.serial_number} "
                    ">>"
                )
            else:
                device_button_text = (
                    f"{device_number}. "  # nbsp
                    f"{device_icon} {device_type_name} "  # nbsp
                    ">>"
                )
            inline_keyboard.append(
                [
                    InlineKeyboardButtonTG(
                        text=device_button_text,
                        callback_data=cb.device.view(device.id),
                    )
                ]
            )
        add_device_button = InlineKeyboardButtonTG(
            text=f"{String.PLUS_ICON} {String.ADD_DEVICE}",  # nbsp
            callback_data=cb.ticket.add_device(ticket.id),
        )
        reopen_ticket_button = InlineKeyboardButtonTG(
            text=f"{String.ATTENTION_ICON} {String.REOPEN_TICKET}",  # nbsp
            callback_data=cb.ticket.reopen(ticket.id),
        )
        close_ticket_button = InlineKeyboardButtonTG(
            text=f"{String.ATTENTION_ICON} {String.CLOSE_TICKET}",  # nbsp
            callback_data=cb.ticket.close(ticket.id),
        )
        delete_ticket_button = InlineKeyboardButtonTG(
            text=f"{String.TRASHCAN_ICON} {String.DELETE_TICKET_PERMANENTLY}",  # nbsp
            callback_data=cb.ticket.delete_start(ticket.id),
        )
        view_tickets_button = InlineKeyboardButtonTG(
            text=String.ALL_TICKETS, callback_data=cb.ticket.list_page(0)
        )
        main_menu_button = InlineKeyboardButtonTG(
            text=String.MAIN_MENU, callback_data=cb.menu.main()
        )
        total_devices = len(ticket.devices)
        if total_devices < settings.devices_per_ticket:
            inline_keyboard.append([add_device_button])
        if ticket.is_closed:
            inline_keyboard.append([reopen_ticket_button])
        else:
            if self._ticket_valid_for_closing(ticket):
                inline_keyboard.append([close_ticket_button])
        inline_keyboard.append([delete_ticket_button])
        inline_keyboard.append([view_tickets_button, main_menu_button])
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard),
        )

    def _build_set_device_type_menu(
        self,
        ticket: TicketDB,
        device_types: list[DeviceTypeDB],
        text: str = f"{String.PICK_DEVICE_TYPE}.",
        device: DeviceDB | None = None,
    ) -> SendMessageTG:
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = []
        for device_type in device_types:
            button_text = String[device_type.name.name]
            callback_data = (
                cb.device.set_type(device.id, device_type.id)
                if device
                else cb.ticket.create_device(ticket.id, device_type.id)
            )
            inline_keyboard.append(
                [
                    InlineKeyboardButtonTG(
                        text=button_text,
                        callback_data=callback_data,
                    )
                ]
            )
        if not inline_keyboard:
            logger.error(
                f"{self.log_prefix}Configuration error: "
                "Not a single eligible (active) "
                f"{DeviceTypeDB.__name__} was found in the database. "
                f"Cannot build {DeviceTypeDB.__name__} "
                "selection keyboard. Investigate the logic."
            )
            text = (
                f"{String.CONFIGURATION_ERROR_DETECTED}. "
                f"{String.NO_ACTIVE_DEVICE_TYPE_AVAILABLE}. "
                f"{String.CONTACT_THE_ADMINISTRATOR}"
            )
            if device:
                method_tg = self._build_device_view(
                    ticket, device, f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}."
                )
            else:
                method_tg = self._drop_state_goto_main_menu(
                    f"{text}. {String.PICK_A_FUNCTION}."
                )
        else:
            method_tg = SendMessageTG(
                chat_id=self.user_db.telegram_uid,
                text=text,
                reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard),
            )
        return method_tg

    def _build_set_device_action_menu(
        self,
        device_id: int,
        device_type: DeviceTypeDB,
        text: str = f"{String.PICK_DEVICE_ACTION}.",
    ) -> SendMessageTG:
        rent_button = InlineKeyboardButtonTG(
            text=(
                f"{String.RENTED_DEVICE_ICON} "  # nbsp
                f"{String.RENT_DEVICE_BTN}"
            ),
            callback_data=cb.device.set_action_rented(device_id),
        )
        sell_button = InlineKeyboardButtonTG(
            text=(
                f"{String.SOLD_DEVICE_ICON} "  # nbsp
                f"{String.SELL_DEVICE_BTN}"
            ),
            callback_data=cb.device.set_action_sold(device_id),
        )
        return_button = InlineKeyboardButtonTG(
            text=(
                f"{String.RETURNED_DEVICE_ICON} "  # nbsp
                f"{String.RETURN_DEVICE_BTN}"
            ),
            callback_data=cb.device.set_action_returned(device_id),
        )
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = []
        if device_type.rentable:
            inline_keyboard.append([rent_button])
        if device_type.sellable:
            inline_keyboard.append([sell_button])
        if device_type.returnable:
            inline_keyboard.append([return_button])
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard),
        )

    def _build_device_view(
        self,
        ticket: TicketDB,
        device: DeviceDB,
        text: str = f"{String.AVAILABLE_DEVICE_ACTIONS}.",
    ) -> SendMessageTG:
        device_type_name = String[device.type.name.name]
        device_serial_number_text = (
            f"{String.NUMBER_SYMBOL} {device.serial_number}"
            if device.serial_number is not None
            else f"{String.ATTENTION_ICON} {String.ENTER_SERIAL_NUMBER}"  # nbsp
        )
        device_type_button = InlineKeyboardButtonTG(
            text=f"{String.TYPE}: {device_type_name} {String.EDIT}",
            callback_data=cb.device.edit_type(device.id),
        )
        device_type_statuses = self._get_device_type_statuses(device.type)
        status_tuple = None
        if device.status:
            status_tuple = device_type_statuses.get(device.status)
            if status_tuple:
                status_icon, status_name = status_tuple
                device_action_text = (
                    f"{String.ACTION}: "
                    f"{status_icon} "  # nbsp
                    f"{status_name}"
                )
            else:
                device_action_text = (
                    f"{String.ATTENTION_ICON} "  # nbsp
                    f"{String.UNRECOGNIZED_DEVICE_ACTION}"
                )
        else:
            device_action_text = (
                f"{String.ATTENTION_ICON} "  # nbsp
                f"{String.PICK_DEVICE_ACTION}"
            )
        device_action_button = InlineKeyboardButtonTG(
            text=f"{device_action_text} {String.EDIT}",
            callback_data=cb.device.edit_action(device.id),
        )
        serial_number_button = InlineKeyboardButtonTG(
            text=f"{device_serial_number_text} {String.EDIT}",  # nbsp
            callback_data=cb.device.edit_serial_number(device.id),
        )
        view_ticket_button = InlineKeyboardButtonTG(
            text=String.TICKET,
            callback_data=cb.ticket.view(ticket.id),
        )
        view_tickets_button = InlineKeyboardButtonTG(
            text=String.ALL_TICKETS,
            callback_data=cb.ticket.list_page(0),
        )
        delete_button = InlineKeyboardButtonTG(
            text=f"{String.TRASHCAN_ICON} {String.DELETE_DEVICE_FROM_TICKET}",
            callback_data=cb.device.delete(device.id),
        )
        main_menu_button = InlineKeyboardButtonTG(
            text=String.MAIN_MENU,
            callback_data=cb.menu.main(),
        )
        inline_keyboard_rows: list[list[InlineKeyboardButtonTG]] = [
            [device_type_button]
        ]
        possible_statuses_count = len(device_type_statuses)
        if possible_statuses_count > 1 or (
            possible_statuses_count == 1 and (not device.status or not status_tuple)
        ):
            inline_keyboard_rows.append([device_action_button])
        if device.type.has_serial_number:
            inline_keyboard_rows.append([serial_number_button])
        inline_keyboard_rows.append([delete_button])
        inline_keyboard_rows.append([view_ticket_button, view_tickets_button])
        inline_keyboard_rows.append([main_menu_button])
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard_rows),
        )

    async def _build_pick_writeoff_devices(
        self, text: str = f"{String.AVAILABLE_WRITEOFF_DEVICES_ACTIONS}."
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
            return self._build_main_menu(
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
            return self._build_main_menu(
                f"{String.CONFIGURATION_ERROR_DETECTED} "
                "(negative writeoff devices page_index). "
                f"{String.CONTACT_THE_ADMINISTRATOR}. "
                f"{String.PICK_A_FUNCTION}."
            )
        total_writeoff_devices = (
            await self.session.scalar(
                select(func.count())
                .select_from(WriteoffDeviceDB)
                .where(WriteoffDeviceDB.user_id == self.user_db.id)
            )
            or 0  # Mypy fix
        )
        writeoffs_per_page = settings.writeoffs_per_page
        total_pages = max(
            1,
            (total_writeoff_devices + writeoffs_per_page - 1) // writeoffs_per_page,
        )
        last_page_index = total_pages - 1
        if page_index is None:
            page_index = 0
        elif page_index > last_page_index:
            page_index = last_page_index
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
                callback_data=cb.writeoff.create_start(),
            ),
        ]
        inline_keyboard_rows.append(add_writeoff_device_button_row)
        existing_writeoff_devices_button_rows: list[list[InlineKeyboardButtonTG]] = []
        writeoff_device_index_offset = page_index * writeoffs_per_page
        current_page_writeoff_devices_result = await self.session.scalars(
            select(WriteoffDeviceDB)
            .where(WriteoffDeviceDB.user_id == self.user_db.id)
            .options(selectinload(WriteoffDeviceDB.type))
            .order_by(WriteoffDeviceDB.id.desc())
            .offset(writeoff_device_index_offset)
            .limit(writeoffs_per_page)
        )
        current_page_writeoff_devices = list(current_page_writeoff_devices_result)
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
            if writeoff_device.type.returnable:
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
                    callback_data=cb.writeoff.view(writeoff_device.id),
                ),
            ]
            writeoff_devices_dict[index] = writeoff_device.id
            existing_writeoff_devices_button_rows.append(writeoff_device_button)
        inline_keyboard_rows.extend(existing_writeoff_devices_button_rows)
        prev_next_buttons_row: list[InlineKeyboardButtonTG] = []
        if total_writeoff_devices > writeoffs_per_page:
            prev_button = InlineKeyboardButtonTG(
                text=f"{String.PREV_ONES}",
                callback_data=cb.writeoff.list_page(page_index + 1),
            )
            next_button = InlineKeyboardButtonTG(
                text=f"{String.NEXT_ONES}",
                callback_data=cb.writeoff.list_page(page_index - 1),
            )
            if page_index < last_page_index:
                prev_next_buttons_row.append(prev_button)
            if page_index > 0:
                prev_next_buttons_row.append(next_button)
        if prev_next_buttons_row:
            inline_keyboard_rows.append(prev_next_buttons_row)
        return_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
            text=String.DONE_BTN,
            callback_data=cb.menu.main(),
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
                            callback_data="placeholder",
                        ),
                        InlineKeyboardButtonTG(
                            text=String.CHANGED_MY_MIND,
                            callback_data="placeholder",
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
            select(DeviceTypeDB).where(DeviceTypeDB.returnable == False)  # noqa: E712
        )
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = []
        for device_type in device_types:
            try:
                button_text = String[device_type.name.name]
                # button_callback_data = CallbackData[device_type.name.name]
                inline_keyboard.append(
                    [
                        InlineKeyboardButtonTG(
                            text=button_text,
                            callback_data="placeholder",
                        )
                    ]
                )
            except KeyError as e:
                missing_member_value = device_type.name.name
                logger.error(
                    f"{self.log_prefix}Configuration error: Missing "
                    f"{String.__name__} enum member for "
                    f"{DeviceTypeName.__name__} "
                    f"'{missing_member_value}'. Original error: {e}"
                )
                logger.info(f"{self.log_prefix}Going back to the main menu.")
                self.next_state = None
                self.user_db.state_json = None
                method_tg = self._build_main_menu(
                    f"{String.CONFIGURATION_ERROR_DETECTED} (missing "
                    f"{String.__name__}.{missing_member_value}). "
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
            method_tg = self._build_main_menu(
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

    async def _build_pick_writeoff_device(
        self, text: str = f"{String.AVAILABLE_WRITEOFF_DEVICE_ACTIONS}."
    ) -> SendMessageTG:
        if self.next_state:
            current_state = self.next_state
        elif self.state:
            current_state = self.state
        else:
            raise ValueError("State is missing for building writeoff device menu.")
        writeoff_device_id = current_state.writeoff_device_id
        if not writeoff_device_id:  # Both None and 0 are covered this way.
            logger.error(
                f"{self.log_prefix}{self.user_db.full_name} "
                "is not working on any writeoff device."
            )
            logger.info(f"{self.log_prefix}Going back to the writeoff devices menu.")
            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
            return await self._build_pick_writeoff_devices(
                f"{String.INCONSISTENT_STATE_DETECTED} "
                "(missing writeoff_device_id). "
                f"{String.AVAILABLE_WRITEOFF_DEVICES_ACTIONS}."
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
            logger.info(f"{self.log_prefix}Going back to the writeoff devices menu.")
            self.next_state = StateJS(action=Action.WRITEOFF_DEVICES)
            return await self._build_pick_writeoff_devices(
                f"{String.WRITEOFF_DEVICE_NOT_FOUND}. "
                f"{String.AVAILABLE_WRITEOFF_DEVICES_ACTIONS}."
            )
        inline_keyboard_rows: list[list[InlineKeyboardButtonTG]] = []
        device_type_name = String[writeoff_device.type.name.name]
        if writeoff_device.type.returnable:
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
                f"{String.AVAILABLE_WRITEOFF_DEVICES_ACTIONS}."
            )
        else:
            device_serial_number_text = (
                writeoff_device.serial_number
                if writeoff_device.serial_number is not None
                else String.ENTER_SERIAL_NUMBER.value
            )
            device_type_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
                text=f"{device_type_name} {String.EDIT}",
                callback_data="placeholder",
            )
            serial_number_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
                text=f"{device_serial_number_text} {String.EDIT}",
                callback_data="placeholder",
            )
            return_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
                text=String.RETURN_BTN,
                callback_data="placeholder",
            )
            delete_button: InlineKeyboardButtonTG = InlineKeyboardButtonTG(
                text=String.DELETE_DEVICE_FROM_WRITEOFF,
                callback_data="placeholder",
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
