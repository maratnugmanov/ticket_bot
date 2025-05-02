from __future__ import annotations
import re
from src.core.logger import logger
from src.core.enums import DeviceTypeName, Strings, Scenario, Modifier
from src.core.models import StateJS
from src.db.engine import SessionDepDB
from src.db.models import UserDB
from src.tg.models import (
    UpdateTG,
    SendMessageTG,
    InlineKeyboardMarkupTG,
    InlineKeyboardButtonTG,
    MethodTG,
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
            logger.debug(
                f"There is no Conversation State with {self.user_db.full_name}."
            )
            if self.update_tg.message:
                logger.debug(f"Responding with Main Menu to {self.user_db.full_name}.")
                response_methods_list.append(self.mainmenu())
            elif self.update_tg.callback_query:
                data = self.update_tg.callback_query.data
                chat_id = self.update_tg.callback_query.message.chat.id
                message_id = self.update_tg.callback_query.message.message_id
                if data == Scenario.ENABLE_HIRING:
                    if not self.user_db.is_hiring:
                        self.user_db.is_hiring = True
                        buttons = self.get_mainmenu_buttons_array()
                        response_methods_list.append(
                            EditMessageTextTG(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=f"{self.user_db.first_name}, {Strings.HIRING_ENABLED}",
                                reply_markup=InlineKeyboardMarkupTG(
                                    inline_keyboard=buttons
                                ),
                            )
                        )
                    else:
                        buttons = self.get_mainmenu_buttons_array()
                        response_methods_list.append(
                            EditMessageTextTG(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=f"{self.user_db.first_name}, {Strings.HIRING_ALREADY_ENABLED}",
                                reply_markup=InlineKeyboardMarkupTG(
                                    inline_keyboard=buttons
                                ),
                            )
                        )
                elif data == Scenario.DISABLE_HIRING:
                    if self.user_db.is_hiring:
                        self.user_db.is_hiring = False
                        buttons = self.get_mainmenu_buttons_array()
                        response_methods_list.append(
                            EditMessageTextTG(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=f"{self.user_db.first_name}, {Strings.HIRING_DISABLED}",
                                reply_markup=InlineKeyboardMarkupTG(
                                    inline_keyboard=buttons
                                ),
                            )
                        )
                    else:
                        buttons = self.get_mainmenu_buttons_array()
                        response_methods_list.append(
                            EditMessageTextTG(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=f"{self.user_db.first_name}, {Strings.HIRING_ALREADY_DISABLED}",
                                reply_markup=InlineKeyboardMarkupTG(
                                    inline_keyboard=buttons
                                ),
                            )
                        )
                elif data == Scenario.TICKET_NUMBER_INPUT:
                    self.user_db.conversation_json = StateJS(
                        scenario=Scenario.TICKET_NUMBER_INPUT,
                        modifier=Modifier.INITIAL_DATA,
                    ).model_dump_json(exclude_none=True)
                    response_methods_list.append(
                        self.enter_ticket_number(Strings.ENTER_TICKET_NUMBER)
                    )
        elif self.state.modifier == Modifier.INITIAL_DATA:
            if self.state.scenario == Scenario.TICKET_NUMBER_INPUT:
                if self.update_tg.message and self.update_tg.message.text:
                    message_text = self.update_tg.message.text
                    if re.fullmatch(r"\d+", message_text):
                        self.user_db.conversation_json = StateJS(
                            scenario=Scenario.DEVICE_TYPE_BUTTONS,
                            modifier=Modifier.INITIAL_DATA,
                            ticket_number=message_text,
                        ).model_dump_json(exclude_none=True)
                        response_methods_list.append(self.pick_device_type())
                    else:
                        response_methods_list.append(
                            self.enter_ticket_number(
                                f"{Strings.INCORRECT_TICKET_NUMBER} "
                                f"{Strings.ENTER_TICKET_NUMBER}"
                            )
                        )
            elif self.state.scenario == Scenario.DEVICE_TYPE_BUTTONS:
                if self.update_tg.callback_query:
                    data = self.update_tg.callback_query.data
                    try:
                        device_type_enum = DeviceTypeName(data)
                        if device_type_enum.name in Strings.__members__:
                            chat_id = self.update_tg.callback_query.message.chat.id
                            message_id = (
                                self.update_tg.callback_query.message.message_id
                            )
                            self.user_db.conversation_json = StateJS(
                                scenario=Scenario.DEVICE_SERIAL_NUMBER,
                                modifier=Modifier.INITIAL_DATA,
                                ticket_number=self.state.ticket_number,
                                device_type=device_type_enum,
                            ).model_dump_json(exclude_none=True)
                            response_methods_list.append(
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
        return response_methods_list

    def mainmenu(self) -> MethodTG:
        buttons = self.get_mainmenu_buttons_array()
        send_message_tg = SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=f"{Strings.HELLO}, {self.user_db.first_name}, "
            f"{Strings.THESE_FUNCTIONS_ARE_AVAILABLE}",
            reply_markup=InlineKeyboardMarkupTG(inline_keyboard=buttons),
        )
        return send_message_tg

    def enter_ticket_number(self, text: str = Strings.ENTER_TICKET_NUMBER):
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=text,
        )

    def pick_device_type(self):
        return SendMessageTG(
            chat_id=self.user_db.telegram_uid,
            text=Strings.PICK_DEVICE_TYPE,
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

    def echo(self):
        return SendMessageTG(
            chat_id=self.update_tg.message.chat.id,
            text=self.update_tg.message.text,
        )

    def get_mainmenu_buttons_array(self) -> list[list[InlineKeyboardButtonTG]]:
        buttons = []
        if self.user_db.is_engineer:
            buttons.append(
                [
                    InlineKeyboardButtonTG(
                        text=Strings.CLOSE_TICKET_BTN,
                        callback_data=Scenario.TICKET_NUMBER_INPUT,
                    )
                ],
            )
            buttons.append(
                [
                    InlineKeyboardButtonTG(
                        text=Strings.TICKETS_HISTORY_BTN,
                        callback_data=Scenario.TICKETS_HISTORY_MENU_BUTTONS,
                    ),
                    InlineKeyboardButtonTG(
                        text=Strings.WRITEOFF_DEVICES_BTN,
                        callback_data=Scenario.WRITEOFF_DEVICES_LIST,
                    ),
                ],
            )
        if self.user_db.is_manager:
            buttons.append(
                [
                    InlineKeyboardButtonTG(
                        text=Strings.FORM_REPORT_BTN,
                        callback_data=Scenario.FORM_REPORT,
                    )
                ],
            )
            if self.user_db.is_hiring:
                buttons.append(
                    [
                        InlineKeyboardButtonTG(
                            text=Strings.DISABLE_HIRING_BTN,
                            callback_data=Scenario.DISABLE_HIRING,
                        )
                    ],
                )
            else:
                buttons.append(
                    [
                        InlineKeyboardButtonTG(
                            text=Strings.ENABLE_HIRING_BTN,
                            callback_data=Scenario.ENABLE_HIRING,
                        )
                    ],
                )
        return buttons

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
