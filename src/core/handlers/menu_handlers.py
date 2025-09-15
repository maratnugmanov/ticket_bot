from __future__ import annotations
from typing import TYPE_CHECKING
from src.core.router import router
from src.core.callbacks import cb
from src.core.enums import String

if TYPE_CHECKING:
    from src.core.conversation import Conversation


@router.route(cb.menu.MAIN)
async def main_menu(conv: Conversation) -> list:
    """Handles the command to display the main menu, clearing any prior state."""
    conv.next_state = None
    return [
        conv._build_edit_to_text_message(f"{String.TO_MAIN_MENU} >>"),
        conv._build_main_menu(f"{String.PICK_A_FUNCTION}."),
    ]
