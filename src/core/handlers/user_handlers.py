from __future__ import annotations
from typing import TYPE_CHECKING

from src.core.router import router
from src.core.enums import String
from src.tg.models import (
    EditMessageTextTG,
    InlineKeyboardMarkupTG,
    CallbackQueryUpdateTG,
)

if TYPE_CHECKING:
    from src.core.conversation import Conversation


@router.route("user:set:hiring")
async def set_hiring(conversation: Conversation, enable_str: str) -> list:
    """Handles the command to enable or disable hiring."""
    if not conversation.user_db.is_manager:
        return []

    enable = bool(int(enable_str))

    if conversation.user_db.is_hiring == enable:
        text = (
            f"{String.HIRING_ALREADY_ENABLED}. {String.PICK_A_FUNCTION}."
            if enable
            else f"{String.HIRING_ALREADY_DISABLED}. {String.PICK_A_FUNCTION}."
        )
    else:
        conversation.user_db.is_hiring = enable
        text = (
            f"{String.HIRING_ENABLED}. {String.PICK_A_FUNCTION}."
            if enable
            else f"{String.HIRING_DISABLED}. {String.PICK_A_FUNCTION}."
        )

    if not isinstance(conversation.update_tg, CallbackQueryUpdateTG):
        return [conversation._drop_state_goto_mainmenu("Error: Invalid action.")]

    chat_id = conversation.update_tg.callback_query.message.chat.id
    message_id = conversation.update_tg.callback_query.message.message_id

    method_tg = EditMessageTextTG(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        reply_markup=InlineKeyboardMarkupTG(
            inline_keyboard=conversation._build_main_menu_keyboard_rows()
        ),
    )

    conversation.next_state = None
    return [method_tg]
