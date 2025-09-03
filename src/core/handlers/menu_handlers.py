from __future__ import annotations
from typing import TYPE_CHECKING
from src.core.router import router
from src.core.callbacks import cb
from src.core.enums import String

if TYPE_CHECKING:
    from src.core.conversation import Conversation


@router.route(cb.menu.MAIN)
async def main_menu(conversation: Conversation) -> list:
    """Handles the command to display the main menu, clearing any prior state."""
    conversation.next_state = None
    return [
        conversation._build_edit_to_callback_button_text(suffix_text=">>"),
        conversation._build_main_menu(f"{String.PICK_A_FUNCTION}."),
    ]
