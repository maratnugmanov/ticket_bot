from __future__ import annotations
import asyncio
import re
from typing import Any, Annotated
from fastapi import Depends
from sqlalchemy import select, exists
from sqlalchemy.orm import selectinload
from src.core.config import settings
from src.core.logger import logger
from src.core.enums import RoleName, DeviceTypeName, DialogueStrings, Scenario
from src.core.models import StateJS
from src.tg.models import MethodTG
from src.db.engine import SessionDepDB
from src.db.models import RoleDB, UserDB

# from src.core.models import Role, User
from src.tg.models import (
    UpdateTG,
    UserTG,
    SendMessageTG,
    InlineKeyboardMarkupTG,
    InlineKeyboardButtonTG,
    EditMessageTextTG,
)


class Conversation:
    """Receives Telegram Update (UpdateTG), database session
    (SessionDepDB), and User from the database (UserDB). Processes
    User's Request and Returns a Response."""

    def __init__(self, update_tg: UpdateTG, session_db: SessionDepDB, user_db: UserDB):
        self.update_tg: UpdateTG = update_tg
        self.session_db: SessionDepDB = session_db
        self.user_db: UserDB = user_db
        self.state: StateJS | None = (
            None
            if not user_db.conversation_json
            else StateJS.model_validate_json(user_db.conversation_json)
        )
        logger.debug(
            f"Conversation with {self.user_db.full_name}, "
            f"Update #{self.update_tg.update_id} initialized."
        )

    async def process(self) -> list[MethodTG]:
        response_methods_list = []
        if self.state is None:
            if self.update_tg.message:
                response_methods_list.append(await self.mainmenu())
            elif self.update_tg.callback_query:
                data = self.update_tg.callback_query.data
                chat_id = self.update_tg.callback_query.message.chat.id
                message_id = self.update_tg.callback_query.message.message_id
                if data == Scenario.ENABLE_HIRING:
                    if self.user_db.is_hiring:
                        buttons = self.get_mainmenu_buttons_list()
                        response_methods_list.append(
                            EditMessageTextTG(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=self.user_db.first_name
                                + DialogueStrings.HIRING_ALREADY_ENABLED,
                                reply_markup=InlineKeyboardMarkupTG(
                                    inline_keyboard=buttons
                                ),
                            )
                        )
                    else:
                        self.user_db.is_hiring = True
                        # self.session_db.add(self.user_db)
                        buttons = self.get_mainmenu_buttons_list()
                        response_methods_list.append(
                            EditMessageTextTG(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=self.user_db.first_name
                                + DialogueStrings.HIRING_ENABLED,
                                reply_markup=InlineKeyboardMarkupTG(
                                    inline_keyboard=buttons
                                ),
                            )
                        )
                elif data == Scenario.DISABLE_HIRING:
                    if not self.user_db.is_hiring:
                        buttons = self.get_mainmenu_buttons_list()
                        response_methods_list.append(
                            EditMessageTextTG(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=self.user_db.first_name
                                + DialogueStrings.HIRING_ALREADY_DISABLED,
                                reply_markup=InlineKeyboardMarkupTG(
                                    inline_keyboard=buttons
                                ),
                            )
                        )

                    else:
                        self.user_db.is_hiring = False
                        # self.session_db.add(self.user_db)
                        buttons = self.get_mainmenu_buttons_list()
                        response_methods_list.append(
                            EditMessageTextTG(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=self.user_db.first_name
                                + DialogueStrings.HIRING_DISABLED,
                                reply_markup=InlineKeyboardMarkupTG(
                                    inline_keyboard=buttons
                                ),
                            )
                        )
                elif data == Scenario.INITIAL_SERIAL_NUMBER_INPUT:
                    self.user_db.conversation_json = StateJS(
                        scenario=Scenario.INITIAL_SERIAL_NUMBER_INPUT
                    ).model_dump_json(exclude_none=True)
                    response_methods_list.append(
                        self.ticket_number_input(DialogueStrings.TICKET_NUMBER_INPUT)
                    )
        else:
            if self.state.scenario == Scenario.INITIAL_SERIAL_NUMBER_INPUT:
                if self.update_tg.message:
                    message_text = self.update_tg.message.text
                    if re.fullmatch(r"\d+", message_text):
                        self.user_db.conversation_json = StateJS(
                            scenario=Scenario.INITIAL_DEVICE_TYPE_BUTTONS,
                            ticket_number=message_text,
                        ).model_dump_json(exclude_none=True)
                    else:
                        response_methods_list.append(
                            self.ticket_number_input(
                                DialogueStrings.INCORRECT_TICKET_NUMBER
                                + DialogueStrings.TICKET_NUMBER_INPUT
                            )
                        )
        return response_methods_list

    async def mainmenu(self) -> MethodTG:
        buttons = self.get_mainmenu_buttons_list()
        send_message_tg = SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=DialogueStrings.HELLO_TO
            + self.user_db.first_name
            + DialogueStrings.THESE_FUNCTIONS_ARE_AVAILABLE,
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=buttons),
        )
        return send_message_tg

    def echo(self):
        return SendMessageTG(
            chat_id=self.update_tg.message.chat.id,
            text=self.update_tg.message.text,
        )

    def ticket_number_input(self, text: str = DialogueStrings.TICKET_NUMBER_INPUT):
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
        )

    def device_type_input(self):
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=DialogueStrings.DEVICE_TYPE_INPUT,
            reply_markup=InlineKeyboardMarkupTG(
                inline_keyboard=[
                    [
                        InlineKeyboardButtonTG(
                            text=DeviceTypeName.IP, callback_data="ip_btn"
                        ),
                        InlineKeyboardButtonTG(
                            text=DeviceTypeName.TVE, callback_data="tve_btn"
                        ),
                        InlineKeyboardButtonTG(
                            text=DeviceTypeName.ROUTER, callback_data="router_btn"
                        ),
                    ]
                ]
            ),
        )

    def get_mainmenu_buttons_list(self) -> list[list[InlineKeyboardButtonTG]]:
        buttons = []
        if self.user_db.is_engineer:
            buttons.append(
                [
                    InlineKeyboardButtonTG(
                        text="⚙ Закрыть заявку",
                        callback_data=Scenario.INITIAL_SERIAL_NUMBER_INPUT,
                    )
                ],
            )
            buttons.append(
                [
                    InlineKeyboardButtonTG(
                        text="🗓 История",
                        callback_data=Scenario.TICKETS_HISTORY_MENU_BUTTONS,
                    ),
                    InlineKeyboardButtonTG(
                        text="☠ Брак",
                        callback_data=Scenario.WRITEOFF_DEVICES_LIST,
                    ),
                ],
            )
        if self.user_db.is_manager:
            buttons.append(
                [
                    InlineKeyboardButtonTG(
                        text="🖨 Сформировать отчет", callback_data="new_report"
                    )
                ],
            )
            if self.user_db.is_hiring:
                buttons.append(
                    [
                        InlineKeyboardButtonTG(
                            text="🙅‍♀️ Закрыть найм",
                            callback_data=Scenario.DISABLE_HIRING,
                        )
                    ],
                )
            else:
                buttons.append(
                    [
                        InlineKeyboardButtonTG(
                            text="🙋‍♀️ Открыть найм",
                            callback_data=Scenario.ENABLE_HIRING,
                        )
                    ],
                )
        return buttons

    def serial_number_input(self):
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=DialogueStrings.SERIAL_NUMBER_INPUT,
        )

    def install_return_input(self):
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=DialogueStrings.INSTALL_RETURN_INPUT,
            reply_markup=InlineKeyboardMarkupTG(
                inline_keyboard=[
                    [
                        InlineKeyboardButtonTG(
                            text=DialogueStrings.INSTALL_BTN,
                            callback_data="install_btn",
                        ),
                        InlineKeyboardButtonTG(
                            text=DialogueStrings.REMOVE_BTN, callback_data="remove_btn"
                        ),
                    ],
                    [
                        InlineKeyboardButtonTG(
                            text=DialogueStrings.EDIT_SN_BTN,
                            callback_data="edit_sn_btn",
                        ),
                    ],
                ]
            ),
        )

    def ticket_devices_list(self):
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=DialogueStrings.TICKET_DEVICES_LIST,
            reply_markup=InlineKeyboardMarkupTG(
                inline_keyboard=[
                    [
                        InlineKeyboardButtonTG(
                            text=DialogueStrings.TICKET_NUMBER_BTN + "123456789",
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
                            text=DialogueStrings.ADD_DEVICE_BTN,
                            callback_data="install_b4tn",
                        ),
                    ],
                    [
                        InlineKeyboardButtonTG(
                            text=DialogueStrings.CLOSE_TICKET_BTN,
                            callback_data="install5_btn",
                        ),
                    ],
                    [
                        InlineKeyboardButtonTG(
                            text=DialogueStrings.QUIT_WITHOUT_SAVING,
                            callback_data="install6_btn",
                        ),
                    ],
                ]
            ),
        )


class Dispatcher:
    """Extracts Telegram User (UserTG) from the Telegram Update
    (UpdateTG) and passes the corresponding User from the database
    (UserDB) to the Conversation (Conversation) along with the database
    session (SessionDepDB). Returns Conversation Result or None if the User
    is not an employee."""

    def __init__(self, update_tg: UpdateTG, session_db: SessionDepDB):
        self.update_tg: UpdateTG = update_tg
        self.session_db: SessionDepDB = session_db
        logger.debug(f"Dispatcher for Update #{self.update_tg.update_id} initialized.")

    async def process(self) -> tuple[MethodWithUrlTG] | None:
        user_tg: UserTG | None = self.get_user_tg()
        if not user_tg:
            logger.debug(
                "Ignoring update: Could not extract User from "
                "supported update types (private message/callback)."
            )
            return None
        user_db: UserDB | None = await self.session_db.scalar(
            select(UserDB)
            .where(UserDB.telegram_uid == user_tg.id)
            .options(selectinload(UserDB.roles))
        )
        if user_db is None:
            logger.debug(f"Guest {user_tg.full_name} is not registered.")
            hiring = await self.session_db.scalar(
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
            guest_role = await self.session_db.scalar(
                select(RoleDB).where(RoleDB.name == RoleName.GUEST)
            )
            if guest_role is None:
                error_message = logger.error(
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
            self.session_db.add(user_db)
            await self.session_db.flush()
            logger.debug(
                f"User DB {user_db.full_name} (ID: {user_db.id}) was "
                f"created with role '{RoleName.GUEST.name}' in the DB."
            )
            return None
        if len(user_db.roles) == 1 and user_db.roles[0].name == guest_role:
            logger.error(
                f"User DB {user_db.full_name} has only "
                f"'{RoleName.GUEST}' role and won't get any reply."
            )
            return None
        logger.debug(f"Validated User DB {user_db.full_name} as employee.")
        conversation_response_sequence: list[MethodWithUrlTG] = await Conversation(
            self.update_tg, self.session_db, user_db
        ).process()
        return conversation_response_sequence

    def get_user_tg(self) -> UserTG | None:
        """Returns Telegram User (UserTG) by extracting it from relevant
        Telegram Update object. Returns None otherwise."""
        user_tg = None
        if (
            self.update_tg.message
            and self.update_tg.message.from_
            and not self.update_tg.message.from_.is_bot
            and self.update_tg.message.chat.type == "private"
        ):
            user_tg = self.update_tg.message.from_
            logger.debug(f"Processing private message update from {user_tg.full_name}.")
        elif (
            self.update_tg.callback_query
            and self.update_tg.callback_query.from_
            and not self.update_tg.callback_query.from_.is_bot
            and self.update_tg.callback_query.message
            and self.update_tg.callback_query.message.from_
            and self.update_tg.callback_query.message.from_.is_bot
            and self.update_tg.callback_query.message.from_.id == settings.bot_id
            and self.update_tg.callback_query.message.chat.type == "private"
        ):
            user_tg = self.update_tg.callback_query.from_
            logger.debug(f"Processing callback query update from {user_tg.full_name}.")
        return user_tg
