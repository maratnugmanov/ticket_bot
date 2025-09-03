from __future__ import annotations
from typing import TYPE_CHECKING
from src.core.router import router
from src.core.callbacks import cb
from src.core.enums import String
from src.tg.models import (
    CallbackQueryUpdateTG,
    InlineKeyboardMarkupTG,
    SendMessageTG,
    EditMessageTextTG,
)

if TYPE_CHECKING:
    from src.core.conversation import Conversation


@router.route(cb.user.SET_HIRING)
async def set_hiring(conv: Conversation, enable_str: str) -> list:
    """Handles the command to enable or disable hiring."""
    if not conv.user_db.is_manager:
        return []
    if not isinstance(conv.update_tg, CallbackQueryUpdateTG):
        return [
            conv._drop_state_goto_main_menu(
                f"{String.ERROR_DETECTED} "
                "(invalid callback data source). "
                f"{String.CONTACT_THE_ADMINISTRATOR}. "
                f"{String.PICK_A_FUNCTION}."
            )
        ]
    enable = bool(int(enable_str))
    was_hiring = conv.user_db.is_hiring
    if was_hiring != enable:
        conv.user_db.is_hiring = enable
    if enable:
        text = String.HIRING_ALREADY_ENABLED if was_hiring else String.HIRING_ENABLED
    else:
        text = (
            String.HIRING_ALREADY_DISABLED if not was_hiring else String.HIRING_DISABLED
        )
    method_tg = conv._build_main_menu(f"{text}. {String.PICK_A_FUNCTION}.")
    conv.next_state = None
    return [conv._build_edit_to_callback_button_text(), method_tg]
