from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, AwareDatetime, PrivateAttr


class UpdateTG(BaseModel):
    update_id: int
    message: MessageTG | None = None
    callback_query: CallbackQueryTG | None = None


class MessageTG(BaseModel):
    message_id: int
    from_: UserTG = Field(alias="from")  # Mandatory since private chats only
    date: AwareDatetime
    chat: ChatTG
    forward_origin: (
        MessageOriginUserTG
        | MessageOriginHiddenUserTG
        | MessageOriginChatTG
        | MessageOriginChannelTG
        | None
    ) = None
    forward_from: UserTG | None = None
    forward_date: AwareDatetime | None = None
    reply_to_message: MessageTG | None = None  # Same chat reply
    is_from_offline: bool | None = None  # Scheduled message
    text: str | None = None
    entities: list[MessageEntityTG] | None = None
    reply_markup: InlineKeyboardMarkupTG | None = None


class CallbackQueryTG(BaseModel):
    id: int
    from_: UserTG = Field(alias="from")
    message: MessageTG  # Mandatory since no "inline_message_id" will be used
    chat_instance: str
    data: str  # Mandatory since no "game_short_name" will be used


class UserTG(BaseModel):
    id: int
    is_bot: bool
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None
    is_premium: bool | None = None

    @property
    def full_name(self) -> str:
        stripped_first = self.first_name.strip() if self.first_name is not None else ""
        stripped_last = self.last_name.strip() if self.last_name is not None else ""
        combined_names = " ".join((stripped_first, stripped_last)).strip()
        if combined_names:
            return f"'{combined_names}' [{self.id}]"
        else:
            return f"[{self.id}]"


class ChatTG(BaseModel):
    id: int
    type: Literal["private", "group", "supergroup", "channel"]  # cspell: disable-line
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class MessageOriginTG(BaseModel):
    type: str
    date: AwareDatetime


class MessageOriginUserTG(MessageOriginTG):
    sender_user: UserTG


class MessageOriginHiddenUserTG(MessageOriginTG):
    sender_user_name: str


class MessageOriginChatTG(MessageOriginTG):
    sender_chat: ChatTG
    author_signature: str | None = None


class MessageOriginChannelTG(MessageOriginTG):
    chat: ChatTG
    message_id: int
    author_signature: str | None = None


class InlineKeyboardMarkupTG(BaseModel):
    inline_keyboard: list[list[InlineKeyboardButtonTG]]


class ReplyKeyboardMarkupTG(BaseModel):
    keyboard: list[list[KeyboardButtonTG]]
    is_persistent: bool | None = None
    resize_keyboard: bool | None = None
    one_time_keyboard: bool | None = None
    input_field_placeholder: str | None = Field(default=None, max_length=64)
    selective: bool | None = None


class ReplyKeyboardRemoveTG(BaseModel):
    remove_keyboard: bool
    selective: bool | None = None


class InlineKeyboardButtonTG(BaseModel):
    text: str
    url: str | None = Field(default=None, pattern=r"^(https*|tg):\/\/.*$")
    callback_data: str | None = None
    copy_text: CopyTextButtonTG | None = None


class ForceReplyTG(BaseModel):
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


class KeyboardButtonTG(BaseModel):
    """This object represents one button of the reply keyboard. At most
    one of the optional fields must be used to specify type of the
    button. For simple text buttons, String can be used instead of this
    object to specify the button text."""

    text: str
    request_contact: bool | None = None
    request_location: bool | None = None


class CopyTextButtonTG(BaseModel):
    text: str | None = Field(default=None, max_length=256)


class MessageEntityTG(BaseModel):
    type: str  # "bot_command"
    offset: int
    length: int
    url: str | None = None
    user: UserTG | None = None
    language: str | None = None
    custom_emoji_id: str | None = None


class ResponseTG(BaseModel):
    """The response contains a JSON object, which always has a Boolean
    field 'ok' and may have an optional String field 'description' with
    a human-readable description of the result. If 'ok' equals True, the
    request was successful and the result of the query can be found in
    the 'result' field. In case of an unsuccessful request, 'ok' equals
    false and the error is explained in the 'description'. An Integer
    'error_code' field is also returned, but its contents are subject to
    change in the future. Some errors may also have an optional field
    'parameters' of the type ResponseParameters, which can help to
    automatically handle the error."""

    ok: bool
    description: str | None = None
    result: MessageTG | None = None


class MethodTG(BaseModel):
    _url: str = PrivateAttr()


class SendMessageTG(MethodTG):
    chat_id: int | str
    text: str
    # https://core.telegram.org/bots/api#formatting-options
    parse_mode: Literal["MarkdownV2", "HTML"] | None = None
    reply_markup: (
        InlineKeyboardMarkupTG
        | ReplyKeyboardMarkupTG
        | ReplyKeyboardRemoveTG
        | ForceReplyTG
        | None
    ) = None
    _url: str = PrivateAttr(default="sendMessage")


class EditMessageTextTG(MethodTG):
    chat_id: int | str
    message_id: int
    text: str
    # https://core.telegram.org/bots/api#formatting-options
    parse_mode: Literal["MarkdownV2", "HTML"] | None = None
    reply_markup: (
        InlineKeyboardMarkupTG
        | ReplyKeyboardMarkupTG
        | ReplyKeyboardRemoveTG
        | ForceReplyTG
        | None
    ) = None
    _url: str = PrivateAttr(default="editMessageText")


if __name__ == "__main__":
    d: dict[str, object] = {
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

    obj = UpdateTG.model_validate(d)

    print(obj.model_dump_json(exclude_none=True))
    print(obj.update_id)
