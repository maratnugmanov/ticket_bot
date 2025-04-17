from __future__ import annotations
from pydantic import BaseModel, Field, AwareDatetime
from typing import Literal


class Update(BaseModel):
    update_id: int
    message: Message | None = None
    callback_query: CallbackQuery | None = None


class Message(BaseModel):
    message_id: int
    from_: User = Field(alias="from")  # Mandatory since private chats only
    date: AwareDatetime
    chat: Chat
    forward_origin: (
        MessageOriginUser
        | MessageOriginHiddenUser
        | MessageOriginChat
        | MessageOriginChannel
        | None
    ) = None
    forward_from: User | None = None
    forward_date: AwareDatetime | None = None
    reply_to_message: Message | None = None  # Same chat reply
    is_from_offline: bool | None = None  # Scheduled message
    text: str | None = None
    entities: list[MessageEntity] | None = None
    reply_markup: InlineKeyboardMarkup | None = None


class CallbackQuery(BaseModel):
    id: int
    from_: User = Field(alias="from")
    message: Message  # Mandatory since no "inline_message_id" will be used
    chat_instance: str
    data: str  # Mandatory since no "game_short_name" will be used


class User(BaseModel):
    id: int
    is_bot: bool
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None
    is_premium: bool | None = None


class Chat(BaseModel):
    id: int
    type: Literal["private", "group", "supergroup", "channel"]  # cspell: disable-line
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class MessageOrigin(BaseModel):
    type: str
    date: AwareDatetime


class MessageOriginUser(MessageOrigin):
    sender_user: User


class MessageOriginHiddenUser(MessageOrigin):
    sender_user_name: str


class MessageOriginChat(MessageOrigin):
    sender_chat: Chat
    author_signature: str | None = None


class MessageOriginChannel(MessageOrigin):
    chat: Chat
    message_id: int
    author_signature: str | None = None


class InlineKeyboardMarkup(BaseModel):
    inline_keyboard: list[list[InlineKeyboardButton]]


class ReplyKeyboardMarkup(BaseModel):
    keyboard: list[list[KeyboardButton]]
    is_persistent: bool | None = None
    resize_keyboard: bool | None = None
    one_time_keyboard: bool | None = None
    input_field_placeholder: str | None = Field(default=None, max_length=64)
    selective: bool | None = None


class ReplyKeyboardRemove(BaseModel):
    remove_keyboard: bool
    selective: bool | None = None


class InlineKeyboardButton(BaseModel):
    text: str
    url: str | None = Field(default=None, pattern=r"^(https*|tg):\/\/.*$")
    callback_data: str | None = None
    copy_text: CopyTextButton | None = None


class ForceReply(BaseModel):
    """Upon receiving a message with this object, Telegram clients will
    display a reply interface to the user (act as if the user has
    selected the bot's message and tapped 'Reply'). This can be
    extremely useful if you want to create user-friendly step-by-step
    interfaces without having to sacrifice privacy mode. Not supported
    in channels and for messages sent on behalf of a Telegram Business
    account."""

    force_reply: bool
    input_field_placeholder: str | None = Field(default=None, max_length=64)
    selective: bool | None = None


class KeyboardButton(BaseModel):
    """This object represents one button of the reply keyboard. At most
    one of the optional fields must be used to specify type of the
    button. For simple text buttons, String can be used instead of this
    object to specify the button text."""

    text: str
    request_contact: bool | None = None
    request_location: bool | None = None


class CopyTextButton(BaseModel):
    text: str | None = Field(default=None, max_length=256)


class MessageEntity(BaseModel):
    type: str  # "bot_command"
    offset: int
    length: int
    url: str | None = None
    user: User | None = None
    language: str | None = None
    custom_emoji_id: str | None = None


class SendMessage(BaseModel):
    chat_id: int | str
    text: str
    # https://core.telegram.org/bots/api#formatting-options
    parse_mode: Literal["MarkdownV2", "HTML"] | None = None
    reply_markup: (
        InlineKeyboardMarkup
        | ReplyKeyboardMarkup
        | ReplyKeyboardRemove
        | ForceReply
        | None
    ) = None


if __name__ == "__main__":
    d: dict = {
        "update_id": 661826899,
        "callback_query": {
            "id": "1827344727401131",
            "from": {
                "id": 425461,
                "is_bot": False,
                "first_name": "Marat",
                "last_name": "Nugmanov",
                "username": "maratnugmanov",
                "language_code": "en",
                "is_premium": True,
            },
            "message": {
                "message_id": 474,
                "from": {
                    "id": 8151889694,
                    "is_bot": True,
                    "first_name": "ТикетБот",
                    "username": "se_ticket_bot",
                },
                "chat": {
                    "id": 425461,
                    "first_name": "Marat",
                    "last_name": "Nugmanov",
                    "username": "maratnugmanov",
                    "type": "private",
                },
                "date": 1743433178,
                "text": "Серийный номер принят. Проверьте введенные данные:",
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "Номер: 8339", "callback_data": "edit_ticket"},
                            {"text": "Тип: IP", "callback_data": "edit_type"},
                            {"text": "S/N: 784", "callback_data": "edit_sn"},
                        ],
                        [{"text": "✅ Все верно", "callback_data": "confirm_ok"}],
                    ]
                },
            },
            "chat_instance": "-8614459959353990087",
            "data": "edit_ticket",
        },
    }

    obj = Update.model_validate(d)

    print(obj.model_dump_json(exclude_none=True))
    print(obj.update_id)
