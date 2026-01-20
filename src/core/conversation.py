from __future__ import annotations
import inspect
from contextlib import asynccontextmanager
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine
from zoneinfo import ZoneInfo
import httpx
from pydantic import ValidationError
from sqlalchemy import select, exists, func
from sqlalchemy.orm import joinedload, selectinload
from src.core.config import settings
from src.core.logger import logger
from src.core.router import router
from src.core.callbacks import cb
from src.core.decorators import require_ticket_context, require_writeoff_context
from src.core.ticket_service import TicketService
from src.core.enums import (
    RoleName,
    DeviceStatus,
    DeviceTypeName,
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
    MessageOriginUserTG,
    MessageOriginHiddenUserTG,
    MessageOriginChatTG,
    MessageOriginChannelTG,
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
    DeviceStatusDB,
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
            # Swap for selectinload when querying more than one user.
            .options(joinedload(UserDB.roles))
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
        if not user_db.is_active:
            logger.info(
                f"{update_tg._log}User {user_db.full_name} is inactive "
                "and will be ignored."
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
                    f"only '{RoleName.GUEST}' role and will be ignored."
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
        methods_tg_list: list[MethodTG] = []
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
            elif self.update_tg.message.forward_origin and self.user_db.is_manager:
                logger.info(
                    f"{self.log_prefix}Manager {self.user_db.full_name} "
                    "forwarded a message."
                )
                methods_tg_list = await self._process_forwarded_message()
                if methods_tg_list:
                    return await self._make_delivery(methods_tg_list)
                else:
                    # If it returns an empty list, it means it was handled but no message is needed.
                    return True
            elif self.update_tg.message.text == "/start":
                command_string = cb.menu.main()
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

    async def _process_forwarded_message(self) -> list[MethodTG]:
        """Processes a forwarded message from a manager to create
        tickets."""
        if (
            not isinstance(self.update_tg, MessageUpdateTG)
            or not self.update_tg.message.forward_origin
        ):
            return []
        message = self.update_tg.message
        if isinstance(message.forward_origin, MessageOriginUserTG):
            op_user_tg = message.forward_origin.sender_user
            op_user_db: UserDB | None = await self.session.scalar(
                select(UserDB)
                .where(UserDB.telegram_uid == op_user_tg.id)
                # Swap for selectinload when querying more than one user.
                .options(joinedload(UserDB.roles))
            )
            if op_user_db:
                logger.info(
                    f"{self.log_prefix}Forwarded message is from "
                    f"existing user {op_user_db.full_name}."
                )
            else:
                logger.info(
                    f"{self.log_prefix}Forwarded message is from "
                    f"new user {op_user_tg.full_name}. "
                    "Creating as an engineer."
                )
                op_user_db = await self._create_forwarded_message_author(op_user_tg)
                if not op_user_db:
                    return [
                        self._build_new_text_message(
                            f"{String.TICKET_PARSING_FAILED}: "
                            f"{String.ENGINEER_CREATION_FAILED} "
                            f"({op_user_tg.full_name})."
                        )
                    ]
        elif isinstance(message.forward_origin, MessageOriginHiddenUserTG):
            op_hidden_user_name = message.forward_origin.sender_user_name
            logger.info(
                f"{self.log_prefix}Forwarded message is from hidden "
                f"user '{op_hidden_user_name}'. Ignoring."
            )
            return [
                self._build_new_text_message(
                    f"{String.FORWARDED_MESSAGE_FROM_HIDDEN_USER} "
                    f"({op_hidden_user_name})."
                )
            ]
        else:  # MessageOriginChatTG, MessageOriginChannelTG
            logger.info(
                f"{self.log_prefix}Forwarded message is from chat "
                "or channel, not a user. Ignoring."
            )
            return [
                self._build_new_text_message(
                    f"{String.FORWARDED_MESSAGE_NOT_FROM_USER}."
                )
            ]
        text = message.text
        if not text:
            logger.info(
                f"{self.log_prefix}Forwarded message from user "
                f"{op_user_db.full_name} has no text. Ignoring."
            )
            return [
                self._build_new_text_message(f"{String.FORWARDED_MESSAGE_HAS_NO_TEXT}.")
            ]
        logger.info(
            f"{self.log_prefix}Processing forwarded message from user "
            f"{op_user_db.full_name} with text: '{text.replace('\n', ' ')}'"
        )
        all_tokens = text.split()
        if not (
            all_tokens
            and re.fullmatch(r"\d{9,10}", all_tokens[0])
            and (first_ticket_number := int(all_tokens[0])) >= 250_000_000
        ):
            logger.info(
                f"{self.log_prefix}Message does not start with a valid ticket number."
            )
            return [
                self._build_new_text_message(
                    f"{String.FORWARDED_MESSAGE_INVALID_START}."
                )
            ]
        ticket_matches = []
        proximity = 200_000
        for match in re.finditer(r"\b(\d{9,10})\b", text):
            number = int(match.group(1))
            if abs(number - first_ticket_number) <= proximity:
                ticket_matches.append((number, match.start()))
        chunks = []
        for i in range(len(ticket_matches)):
            ticket_number, start_pos = ticket_matches[i]
            end_pos = ticket_matches[i + 1][1] if i + 1 < len(ticket_matches) else None
            chunk_text = text[start_pos:end_pos].strip()
            chunks.append((ticket_number, chunk_text))
            logger.info(
                f"{self.log_prefix}Found ticket chunk for user "
                f"'{op_user_db.full_name}': ticket_number={ticket_number}, "
                f"text='{chunk_text.replace('\n', ' ')}'."
            )
        for ticket_number, chunk_text in chunks:
            # Check for a recent existing ticket from the same user
            original_message_date: datetime = message.forward_origin.date
            cutoff_date = original_message_date - timedelta(days=1)
            future_cutoff_date = original_message_date + timedelta(days=1)
            existing_ticket = await self.session.scalar(
                select(TicketDB)
                .where(
                    TicketDB.number == ticket_number,
                    TicketDB.user_id == op_user_db.id,
                    TicketDB.created_at >= cutoff_date,
                    # TicketDB.is_closed == False,  # noqa: E712
                    TicketDB.created_at <= future_cutoff_date,
                )
                .order_by(TicketDB.created_at.desc())
            )
            if existing_ticket:
                logger.info(
                    f"{self.log_prefix}Found existing ticket "
                    f"id={existing_ticket.id} number={ticket_number}. "
                    "Will add actions to it."
                )
                # Placeholder for adding devices to the existing ticket
            else:
                logger.info(
                    f"{self.log_prefix}Creating new ticket number={ticket_number}."
                )
                new_ticket = TicketDB(number=ticket_number, user_id=op_user_db.id)
                new_ticket.created_at = original_message_date
                self.session.add(new_ticket)
                # Placeholder for parsing chunk_text and adding devices
        # split_text = re.split(r"[\s,.]+", text)
        # --- Placeholder for future implementation ---
        # 1. Find or create engineer UserDB from `original_author` if it's not None.
        # 2. Use re.findall(r'\b\d{9}\b', text) to get all ticket numbers.
        # 3. Split the text into chunks based on ticket numbers.
        # 4. For each chunk, tokenize the text (e.g., text.split()).
        # 5. Iterate through tokens, classifying them as device types, statuses, or serials.
        # 6. If an unknown token is found, store it and prepare to ask the manager for clarification.
        # 7. Create/update TicketDB and DeviceDB objects.
        self.next_state = None  # Ensure we clear any pending state
        return [self._build_new_text_message(f"{String.FORWARDED_MESSAGE_PROCESSED}.")]

    async def _create_forwarded_message_author(
        self, op_user_tg: UserTG
    ) -> UserDB | None:
        """Creates a new user with Engineer and Guest roles."""
        engineer_role_enums = [RoleName.ENGINEER, RoleName.GUEST]
        engineer_roles_result = await self.session.scalars(
            select(RoleDB).where(RoleDB.name.in_(engineer_role_enums))
        )
        engineer_roles = list(engineer_roles_result)
        if len(engineer_roles) != len(engineer_role_enums):
            logger.error(
                f"{self.log_prefix}Not all engineer user roles "
                "found in the database. Found: "
                f"{[role.name for role in engineer_roles]}."
            )
            return None
        op_user_db = UserDB(
            telegram_uid=op_user_tg.id,
            first_name=op_user_tg.first_name,
            last_name=op_user_tg.last_name,
            timezone=settings.user_default_timezone,
        )
        op_user_db.roles.extend(engineer_roles)
        self.session.add(op_user_db)
        await self.session.flush()
        return op_user_db

    async def _get_ticket_if_eligible(
        self, ticket_id_str: str, loader_options: list | None = None
    ) -> TicketDB | String:
        ticket_id = int(ticket_id_str)
        ticket = await self.session.get(
            TicketDB,
            ticket_id,
            options=loader_options,
        )
        if not ticket:
            result: TicketDB | String = String.TICKET_NOT_FOUND
        elif not (ticket.user_id == self.user_db.id or self.user_db.is_manager):
            result = String.FOREIGN_TICKET
        else:
            result = ticket
        return result

    async def _get_ticket_for_editing(self, ticket_id_str: str):
        """Returns TicketDB if the ticket is found, eligible, and open.
        Returns SendMessageTG with explanation of denial otherwise."""
        loader_options = [
            joinedload(TicketDB.contract),
            joinedload(TicketDB.devices).options(
                joinedload(DeviceDB.type).selectinload(DeviceTypeDB.statuses),
                joinedload(DeviceDB.status),
            ),
        ]
        ticket_or_string = await self._get_ticket_if_eligible(
            ticket_id_str, loader_options
        )
        result: TicketDB | SendMessageTG | None = None
        if isinstance(ticket_or_string, TicketDB):
            ticket = ticket_or_string
            if not ticket.is_closed:
                result = ticket
            else:
                result = self._build_ticket_view(
                    ticket,
                    (
                        f"{String.ATTENTION_ICON} "  # nbsp
                        f"{String.READONLY_MODE}. "
                        f"{String.CANNOT_EDIT_CLOSED_TICKET}."
                    ),
                )
        else:
            string = ticket_or_string
            result = self._drop_state_goto_main_menu(
                f"{string}. {String.PICK_A_FUNCTION}."
            )
        return result

    async def _get_device_for_editing(self, device_id_str: str):
        """Returns tuple[TicketDB, DeviceDB] if the device and ticket
        are found and editable. Returns SendMessageTG if the device is
        not found, or the ticket is closed/inaccessible."""
        device_id = int(device_id_str)
        device = await self.session.get(DeviceDB, device_id)
        result: tuple[DeviceDB, TicketDB] | SendMessageTG | None = None
        if device:
            ticket_or_method_tg = await self._get_ticket_for_editing(
                str(device.ticket_id)
            )
            if isinstance(ticket_or_method_tg, TicketDB):
                ticket = ticket_or_method_tg
                result = device, ticket
            else:
                result = ticket_or_method_tg
        else:
            result = self._drop_state_goto_main_menu(
                f"{String.DEVICE_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
        return result

    async def _get_writeoff_if_eligible(
        self, writeoff_id_str: str, loader_options: list | None = None
    ) -> WriteoffDeviceDB | String:
        writeoff_id = int(writeoff_id_str)
        writeoff = await self.session.get(
            WriteoffDeviceDB, writeoff_id, options=loader_options
        )
        if not writeoff:
            result: WriteoffDeviceDB | String = String.WRITEOFF_DEVICE_NOT_FOUND
        elif not (writeoff.user_id == self.user_db.id or self.user_db.is_manager):
            result = String.FOREIGN_WRITEOFF
        else:
            result = writeoff
        return result

    async def _get_writeoff_for_editing(
        self, writeoff_id_str: str
    ) -> WriteoffDeviceDB | SendMessageTG:
        """Returns WriteoffDeviceDB if it is found and eligible.
        Returns SendMessageTG with explanation of denial otherwise."""
        loader_options = [
            # Swap for selectinload(DeviceTypeDB.statuses) when querying more than one.
            joinedload(WriteoffDeviceDB.type).joinedload(DeviceTypeDB.statuses)
        ]
        writeoff_or_string = await self._get_writeoff_if_eligible(
            writeoff_id_str, loader_options
        )
        result: WriteoffDeviceDB | SendMessageTG | None = None
        if isinstance(writeoff_or_string, WriteoffDeviceDB):
            result = writeoff_or_string
        else:
            string = writeoff_or_string
            result = self._drop_state_goto_main_menu(
                f"{string}. {String.PICK_A_FUNCTION}."
            )
        return result

    async def _get_active_device_types(
        self, with_statuses: bool = False
    ) -> list[DeviceTypeDB]:
        """Returns a list of all active device types."""
        query = (
            select(DeviceTypeDB).where(DeviceTypeDB.is_active == True)  # noqa: E712
        )
        if with_statuses:
            query = query.options(selectinload(DeviceTypeDB.statuses))
        return list(await self.session.scalars(query))

    async def _get_active_writeoff_device_types(
        self, with_statuses: bool = False
    ) -> list[DeviceTypeDB]:
        """Returns a list of active device types
        eligible for writeoffs."""
        query = (
            select(DeviceTypeDB)
            .join(DeviceTypeDB.statuses)
            .where(
                DeviceTypeDB.is_active == True,  # noqa: E712
                DeviceStatusDB.name == DeviceStatus.RETURN,
            )
        )
        if with_statuses:
            query = query.options(selectinload(DeviceTypeDB.statuses))
        return list(await self.session.scalars(query))

    def _build_new_text_message(self, text: str) -> SendMessageTG:
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
        )

    def _handle_device_status_update(
        self,
        new_device_status: DeviceStatusDB,
        device: DeviceDB,
        ticket: TicketDB,
        prefix_text: str | None = None,
    ) -> SendMessageTG:
        """Updates device status and then determines the next step. If
        device requires serial number and doesn't have one, it returns
        message prompting for it. Otherwise returns message with
        device menu and ensures device serial number is set to None."""
        new_status_icon = self._get_device_status_icon(new_device_status)
        old_device_status = device.status
        if not old_device_status or old_device_status.id != new_device_status.id:
            device.status = new_device_status
            if old_device_status:
                old_status_icon = self._get_device_status_icon(old_device_status)
                text = (
                    f"{String.DEVICE_ACTION_CHANGED}: "
                    f"{old_status_icon} {String[old_device_status.name.name]} >> "  # nbsp
                    f"{new_status_icon} {String[new_device_status.name.name]}"  # nbsp
                )
            else:
                text = (
                    f"{String.DEVICE_ACTION_SET_TO}: "
                    f"{new_status_icon} {String[new_device_status.name.name]}"  # nbsp
                )
        else:
            text = (
                f"{String.DEVICE_ACTION_REMAINED_THE_SAME}: "
                f"{new_status_icon} {String[new_device_status.name.name]}"  # nbsp
            )
        if prefix_text:
            text = f"{prefix_text}. {text}"
        if device.type.has_serial_number:
            if not device.serial_number:
                self.next_state = StateJS(
                    pending_command_prefix=cb.device.set_serial_number(device.id)
                )
                text = f"{text}. {String.ENTER_SERIAL_NUMBER}."
                method_tg = self._build_new_text_message(text)
            else:
                text = f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}."
                method_tg = self._build_device_view(device, ticket, text)
        else:
            if device.serial_number:
                text = f"{text}. {String.SERIAL_NUMBER_REMOVED}"
                device.serial_number = None
            text = f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}."
            method_tg = self._build_device_view(device, ticket, text)
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
        """Returns a string with ticket icon, ticket number,
        ticket creation date, and >> symbol."""
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
        ticket_icon = (
            String.CLOSED_TICKET_ICON if ticket.is_closed else String.ATTENTION_ICON
        )
        ticket_created_at_local_timestamp = ticket.created_at.astimezone(user_timezone)
        day_number = ticket_created_at_local_timestamp.day
        month_number = ticket_created_at_local_timestamp.month
        hh_mm = ticket_created_at_local_timestamp.strftime("%H:%M")
        return (
            f"{ticket_icon} "  # nbsp
            f"{String.NUMBER_SYMBOL} "  # nbsp
            f"{ticket.number} {String.FROM_X} "
            f"{day_number} {months[month_number]} "  # nbsp
            f"{hh_mm} >>"  # nbsp
        )

    def _get_device_status_icon(self, status: DeviceStatusDB | None) -> String:
        """Returns the icon for a given status.
        Returns an attention icon if the status is missing
        or a question mark icon if it is unknown."""
        if not status:
            return String.ATTENTION_ICON
        status_icons = {
            DeviceStatus.RENT: String.RENT_DEVICE_ICON,
            DeviceStatus.SALE: String.SALE_DEVICE_ICON,
            DeviceStatus.RETURN: String.RETURN_DEVICE_ICON,
        }
        return status_icons.get(status.name, String.QUESTION_MARK_ICON)

    def _device_status_icon_if_valid_for_ticket_closing(
        self, device: DeviceDB
    ) -> String | None:
        """Returns device status icon if device is complete,
        otherwise returns None."""
        possible_status_ids = {status.id for status in device.type.statuses}
        if (
            device.status is None
            or device.status.id not in possible_status_ids
            or bool(device.serial_number) != device.type.has_serial_number
        ):
            return None
        return self._get_device_status_icon(device.status)

    def _get_device_overview(
        self, device: DeviceDB, ticket: TicketDB | None = None
    ) -> str:
        """Returns a string with device index (if ticket is provided),
        device status icon, device type name, and device serial number
        (if exist)."""
        device_icon = (
            self._device_status_icon_if_valid_for_ticket_closing(device)
            or String.ATTENTION_ICON
        )
        device_type_name = String[device.type.name.name]
        device_overview_text = f"{device_icon} {device_type_name}"  # nbsp
        if ticket:
            try:
                device_index = ticket.devices.index(device)
                device_overview_text = (
                    f"{device_index + 1}. {device_overview_text}"  # nbsp
                )
            except ValueError:
                logger.warning(
                    f"{self.log_prefix}Device with id={device.id} "
                    f"not found in ticket id={ticket.id}. "
                    "Omitting device number."
                )
        if device.serial_number is not None:
            device_overview_text = f"{device_overview_text} {device.serial_number}"
        return device_overview_text

    def _get_writeoff_overview(self, writeoff: WriteoffDeviceDB) -> str:
        """Returns a string with writeoff device icon (writeoff icon if
        complete or attention icon if incomplete), device type name,
        and device serial number (if exist)."""
        writeoff_icon = (
            String.WRITEOFF_ICON
            if bool(writeoff.serial_number) == writeoff.type.has_serial_number
            else String.ATTENTION_ICON
        )
        device_type_name = String[writeoff.type.name.name]
        writeoff_overview_text = f"{writeoff_icon} {device_type_name}"  # nbsp
        if writeoff.serial_number is not None:
            writeoff_overview_text = (
                f"{writeoff_overview_text} {writeoff.serial_number}"
            )
        return writeoff_overview_text

    def _ticket_valid_for_closing(self, ticket: TicketDB) -> bool:
        """Returns True if a ticket is valid for closing,
        otherwise returns False."""
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

    def _pagination_helper(
        self, elements_total: int, elements_per_page: int, page: int
    ) -> tuple[int, int]:
        """Checks if page index is not negative and not exceeding
        possible last page index. Returns a tuple of corrected page and
        last page index."""
        total_pages = max(
            1,
            (elements_total + elements_per_page - 1) // elements_per_page,
        )
        last_page = total_pages - 1
        if page < 0:
            logger.warning(
                f"{self.log_prefix}Current elements list page "
                f"is negative (page={page}). Setting it to 0. "
            )
            page = 0
        elif page > last_page:
            page = last_page
        logger.info(f"{self.log_prefix}User is on page {page + 1} of {total_pages}.")
        return page, last_page

    async def _get_paginated_tickets(
        self,
        page: int,
    ) -> tuple[list[TicketDB], int, int]:
        """Fetches a paginated list of recent tickets for the user."""
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
        page, last_page = self._pagination_helper(
            total_recent_tickets, tickets_per_page, page
        )
        offset = page * tickets_per_page
        tickets_result = await self.session.scalars(
            select(TicketDB)
            .where(
                TicketDB.user_id == self.user_db.id,
                TicketDB.created_at >= cutoff_date,
            )
            .order_by(TicketDB.created_at.desc())
            .offset(offset)
            .limit(tickets_per_page)
        )
        tickets = list(tickets_result)
        return tickets, page, last_page

    def _build_tickets_list(
        self,
        tickets: list[TicketDB],
        page: int,
        last_page: int,
        text: str = f"{String.AVAILABLE_TICKETS_ACTIONS}.",
    ) -> SendMessageTG:
        """Returns a telegram message object
        with a list of recent tickets."""
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = []
        add_ticket_button = InlineKeyboardButtonTG(
            text=String.ADD_TICKET_BTN,
            callback_data=cb.ticket.create_start(),
        )
        inline_keyboard.append([add_ticket_button])
        for ticket in tickets:
            inline_keyboard.append(
                [
                    InlineKeyboardButtonTG(
                        text=self._get_ticket_overview(ticket),
                        callback_data=cb.ticket.view(ticket.id),
                    )
                ]
            )
        prev_next_buttons_row: list[InlineKeyboardButtonTG] = []
        if last_page > 0:
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
        """Returns a telegram message object
        with ticket devices and available ticket actions.
        It does NOT check if ticket is foreign."""
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = []
        ticket_number_button = InlineKeyboardButtonTG(
            text=(
                f"{String.TICKET} "
                f"{String.NUMBER_SYMBOL} "  # nbsp
                f"{ticket.number} {String.EDIT}"
            ),
            callback_data=cb.ticket.edit_number(ticket.id),
        )
        inline_keyboard.append([ticket_number_button])
        if ticket.contract:
            contract_text = (
                f"{String.CONTRACT} "
                f"{String.NUMBER_SYMBOL} "  # nbsp
                f"{ticket.contract.number}"
            )
        else:
            contract_text = (
                f"{String.ATTENTION_ICON} "  # nbsp
                f"{String.ENTER_CONTRACT_NUMBER}"
            )
        contract_number_button = InlineKeyboardButtonTG(
            text=f"{contract_text} {String.EDIT}",
            callback_data=cb.ticket.edit_contract(ticket.id),
        )
        inline_keyboard.append([contract_number_button])
        for device in ticket.devices:
            device_overview_text = self._get_device_overview(device, ticket)
            device_button_text = f"{device_overview_text} >>"
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
            text=f"{String.TRASHCAN_ICON} {String.DELETE_TICKET}",  # nbsp
            callback_data=cb.ticket.delete_start(ticket.id),
        )
        all_tickets_button = InlineKeyboardButtonTG(
            text=String.ALL_TICKETS, callback_data=cb.ticket.list_page(0)
        )
        main_menu_button = InlineKeyboardButtonTG(
            text=String.MAIN_MENU, callback_data=cb.menu.main()
        )
        total_devices = len(ticket.devices)
        if not ticket.is_closed:
            if total_devices < settings.devices_per_ticket:
                inline_keyboard.append([add_device_button])
            if self._ticket_valid_for_closing(ticket):
                inline_keyboard.append([close_ticket_button])
        else:
            inline_keyboard.append([reopen_ticket_button])
        inline_keyboard.append([delete_ticket_button])
        inline_keyboard.append([all_tickets_button, main_menu_button])
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard),
        )

    def _build_confirm_ticket_deletion_menu(
        self, ticket_id: int, text: str = f"{String.CONFIRM_TICKET_DELETION}."
    ) -> SendMessageTG:
        """Returns a telegram message object
        with ticket removal confirmation options.
        It does NOT check if ticket is foreign."""
        method_tg = SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(
                inline_keyboard=[
                    [
                        InlineKeyboardButtonTG(
                            text=(
                                f"{String.WARNING_ICON} "  # nbsp
                                f"{String.CONFIRM_DELETE_TICKET}"
                            ),
                            callback_data=cb.ticket.delete_confirm(ticket_id),
                        ),
                        InlineKeyboardButtonTG(
                            text=String.CHANGED_MY_MIND,
                            callback_data=cb.ticket.view(ticket_id),
                        ),
                    ],
                ]
            ),
        )
        return method_tg

    def _build_set_device_type_menu(
        self,
        ticket: TicketDB,
        device_types: list[DeviceTypeDB],
        text: str = f"{String.PICK_DEVICE_TYPE}.",
        device: DeviceDB | None = None,
    ) -> SendMessageTG:
        """Returns a telegram message object with a list of device types
        to choose from to create a new device or to edit an existing one
        if one was provided.
        It does NOT check if ticket is closed or foreign."""
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
            logger.warning(
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
                    device, ticket, f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}."
                )
            else:
                method_tg = self._build_ticket_view(
                    ticket, f"{text}. {String.AVAILABLE_TICKET_ACTIONS}."
                )
        else:
            method_tg = SendMessageTG(
                chat_id=self.user_db.telegram_uid,
                text=text,
                reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard),
            )
        return method_tg

    def _build_set_device_status_menu(
        self,
        device: DeviceDB,
        text: str = f"{String.PICK_DEVICE_ACTION}.",
    ) -> SendMessageTG:
        """Returns a telegram message object with a list of device
        statuses to choose from to edit an existing device.
        It does NOT check if ticket is closed or foreign."""
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = []
        for status in device.type.statuses:
            status_icon = self._get_device_status_icon(status)
            status_name_str = String[status.name.name]
            button = InlineKeyboardButtonTG(
                text=f"{status_icon} {status_name_str}",  # nbsp
                callback_data=cb.device.set_status(device.id, status.name),
            )
            inline_keyboard.append([button])
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard),
        )

    def _build_device_view(
        self,
        device: DeviceDB,
        ticket: TicketDB,
        text: str = f"{String.AVAILABLE_DEVICE_ACTIONS}.",
    ) -> SendMessageTG:
        """Returns a telegram message object
        with device details and available device actions.
        It does NOT check if ticket is foreign."""
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = []
        device_type_name = String[device.type.name.name]
        device_type_button = InlineKeyboardButtonTG(
            text=f"{String.TYPE}: {device_type_name} {String.EDIT}",
            callback_data=cb.device.edit_type(device.id),
        )
        status_icon = self._get_device_status_icon(device.status)
        if device.status:
            status_name = String[device.status.name.name]
            device_status_text = (
                f"{String.ACTION}: "
                f"{status_icon} "  # nbsp
                f"{status_name}"
            )
        else:
            device_status_text = (
                f"{status_icon} "  # nbsp
                f"{String.PICK_DEVICE_ACTION}"
            )
        device_status_button = InlineKeyboardButtonTG(
            text=f"{device_status_text} {String.EDIT}",
            callback_data=cb.device.edit_status(device.id),
        )
        device_serial_number_text = (
            f"{String.NUMBER_SYMBOL} {device.serial_number}"  # nbsp
            if device.serial_number is not None
            else f"{String.ATTENTION_ICON} {String.ENTER_SERIAL_NUMBER}"  # nbsp
        )
        serial_number_button = InlineKeyboardButtonTG(
            text=f"{device_serial_number_text} {String.EDIT}",
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
            text=f"{String.TRASHCAN_ICON} {String.DELETE_DEVICE_FROM_TICKET}",  # nbsp
            callback_data=cb.device.delete(device.id),
        )
        main_menu_button = InlineKeyboardButtonTG(
            text=String.MAIN_MENU,
            callback_data=cb.menu.main(),
        )
        inline_keyboard.append([device_type_button])
        possible_status_ids = {status.id for status in device.type.statuses}
        possible_status_count = len(possible_status_ids)
        if possible_status_count > 1 or (
            possible_status_count == 1
            and (not device.status or device.status.id not in possible_status_ids)
        ):
            inline_keyboard.append([device_status_button])
        if device.type.has_serial_number:
            inline_keyboard.append([serial_number_button])
        inline_keyboard.append([delete_button])
        inline_keyboard.append([view_ticket_button, view_tickets_button])
        inline_keyboard.append([main_menu_button])
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard),
        )

    async def _get_paginated_writeoffs(
        self, page: int
    ) -> tuple[list[WriteoffDeviceDB], int, int, int]:
        """Fetches a paginated list of recent writeoffs for the user."""
        total_writeoffs = (
            await self.session.scalar(
                select(func.count())
                .select_from(WriteoffDeviceDB)
                .where(WriteoffDeviceDB.user_id == self.user_db.id)
            )
            or 0  # Mypy fix
        )
        writeoffs_per_page = settings.writeoffs_per_page
        page, last_page = self._pagination_helper(
            total_writeoffs, writeoffs_per_page, page
        )
        offset = page * writeoffs_per_page
        writeoffs_result = await self.session.scalars(
            select(WriteoffDeviceDB)
            .where(WriteoffDeviceDB.user_id == self.user_db.id)
            .options(joinedload(WriteoffDeviceDB.type))
            .order_by(WriteoffDeviceDB.id.desc())
            .offset(offset)
            .limit(writeoffs_per_page)
        )
        writeoffs = list(writeoffs_result)
        return writeoffs, page, last_page, total_writeoffs

    def _build_writeoff_devices_list(
        self,
        writeoffs: list[WriteoffDeviceDB],
        page: int,
        last_page: int,
        total_writeoffs: int,
        text: str = f"{String.AVAILABLE_WRITEOFF_DEVICES_ACTIONS}.",
    ) -> SendMessageTG:
        """Returns a telegram message object
        with a list of recent writeoff devices."""
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = []
        add_writeoff_device_button = InlineKeyboardButtonTG(
            text=f"{String.ADD_WRITEOFF_DEVICE_BTN}",
            callback_data=cb.writeoff.create_start(),
        )
        inline_keyboard.append([add_writeoff_device_button])
        offset = page * settings.writeoffs_per_page
        for index, writeoff_device in enumerate(writeoffs):
            writeoff_device_index = total_writeoffs - offset - index
            writeoff_overview_text = self._get_writeoff_overview(writeoff_device)
            writeoff_button_text = f"{writeoff_overview_text} >>"
            inline_keyboard.append(
                [
                    InlineKeyboardButtonTG(
                        text=(
                            f"{writeoff_device_index}. "  # nbsp
                            f"{writeoff_button_text}"
                        ),
                        callback_data=cb.writeoff.view(writeoff_device.id),
                    ),
                ]
            )
        prev_next_buttons_row: list[InlineKeyboardButtonTG] = []
        if last_page > 0:
            prev_button = InlineKeyboardButtonTG(
                text=f"{String.PREV_ONES}",
                callback_data=cb.writeoff.list_page(page + 1),
            )
            next_button = InlineKeyboardButtonTG(
                text=f"{String.NEXT_ONES}",
                callback_data=cb.writeoff.list_page(page - 1),
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

    def _build_writeoff_view(
        self,
        writeoff: WriteoffDeviceDB,
        text: str = f"{String.AVAILABLE_WRITEOFF_DEVICE_ACTIONS}.",
    ) -> SendMessageTG:
        """Returns a telegram message object
        with writeoff device details and available device actions.
        It does NOT check if writeoff device is foreign."""
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = []
        device_type_name = String[writeoff.type.name.name]
        device_type_button = InlineKeyboardButtonTG(
            text=f"{String.TYPE}: {device_type_name} {String.EDIT}",
            callback_data=cb.writeoff.edit_type(writeoff.id),
        )
        writeoff_serial_number_text = (
            f"{String.NUMBER_SYMBOL} {writeoff.serial_number}"  # nbsp
            if writeoff.serial_number is not None
            else f"{String.ATTENTION_ICON} {String.ENTER_SERIAL_NUMBER}"  # nbsp
        )
        serial_number_button = InlineKeyboardButtonTG(
            text=f"{writeoff_serial_number_text} {String.EDIT}",
            callback_data=cb.writeoff.edit_serial_number(writeoff.id),
        )
        delete_button = InlineKeyboardButtonTG(
            text=f"{String.TRASHCAN_ICON} {String.DELETE_DEVICE_FROM_WRITEOFF}",  # nbsp
            callback_data=cb.writeoff.delete_start(writeoff.id),
        )
        view_writeoffs_button = InlineKeyboardButtonTG(
            text=String.ALL_WRITEOFFS,
            callback_data=cb.writeoff.list_page(0),
        )
        main_menu_button = InlineKeyboardButtonTG(
            text=String.MAIN_MENU,
            callback_data=cb.menu.main(),
        )
        inline_keyboard.append([device_type_button])
        if writeoff.type.has_serial_number:
            inline_keyboard.append([serial_number_button])
        inline_keyboard.append([delete_button])
        inline_keyboard.append([view_writeoffs_button, main_menu_button])
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard),
        )

    def _build_confirm_writeoff_deletion_menu(
        self,
        writeoff_id: int,
        text: str = f"{String.CONFIRM_WRITEOFF_DEVICE_DELETION}.",
    ) -> SendMessageTG:
        """Returns a telegram message object
        with writeoff device removal confirmation options.
        It does NOT check if writeoff device is foreign."""
        method_tg = SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
            reply_markup=InlineKeyboardMarkupTG(
                inline_keyboard=[
                    [
                        InlineKeyboardButtonTG(
                            text=(
                                f"{String.WARNING_ICON} "  # nbsp
                                f"{String.CONFIRM_DELETE_WRITEOFF}"
                            ),
                            callback_data=cb.writeoff.delete_confirm(writeoff_id),
                        ),
                        InlineKeyboardButtonTG(
                            text=String.CHANGED_MY_MIND,
                            callback_data=cb.writeoff.list_page(0),
                        ),
                    ],
                ]
            ),
        )
        return method_tg

    async def _build_set_writeoff_device_type_menu(
        self,
        device_types: list[DeviceTypeDB],
        text: str = f"{String.PICK_WRITEOFF_DEVICE_TYPE}.",
        writeoff: WriteoffDeviceDB | None = None,
    ) -> SendMessageTG:
        """Returns a telegram message object with a list of device types
        to choose from to create a new writeoff device or to edit
        an existing one if one was provided.
        It does NOT check if writeoff device is foreign."""
        inline_keyboard: list[list[InlineKeyboardButtonTG]] = []
        for device_type in device_types:
            button_text = String[device_type.name.name]
            callback_data = (
                cb.writeoff.set_type(writeoff.id, device_type.id)
                if writeoff
                else cb.writeoff.create_confirm(device_type.id)
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
            logger.warning(
                f"{self.log_prefix}Configuration error: "
                "Not a single eligible (active) writeoff "
                f"{DeviceTypeDB.__name__} was found in the database. "
                f"Cannot build {DeviceTypeDB.__name__} "
                "selection keyboard. Investigate the logic."
            )
            text = (
                f"{String.CONFIGURATION_ERROR_DETECTED}. "
                f"{String.NO_WRITEOFF_DEVICE_TYPE_AVAILABLE}. "
                f"{String.CONTACT_THE_ADMINISTRATOR}"
            )
            if writeoff:
                method_tg = self._build_writeoff_view(
                    writeoff, f"{text}. {String.AVAILABLE_WRITEOFF_DEVICE_ACTIONS}."
                )
            else:
                (
                    writeoffs,
                    page,
                    last_page,
                    total_writeoffs,
                ) = await self._get_paginated_writeoffs(0)
                method_tg = self._build_writeoff_devices_list(
                    writeoffs,
                    page,
                    last_page,
                    total_writeoffs,
                    f"{text}. {String.AVAILABLE_WRITEOFF_DEVICES_ACTIONS}.",
                )
        else:
            method_tg = SendMessageTG(
                chat_id=self.user_db.telegram_uid,
                text=text,
                reply_markup=InlineKeyboardMarkupTG(inline_keyboard=inline_keyboard),
            )
        return method_tg
