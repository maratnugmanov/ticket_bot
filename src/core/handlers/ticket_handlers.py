from __future__ import annotations
from src.core.router import router
from src.core.conversation import Conversation
from src.db.models import TicketDB
from src.core.enums import String, Action
from src.core.models import StateJS


@router.route("tickets:list")
async def list_tickets(conversation: Conversation, page_str: str = "0") -> list:
    """Handles the command to list tickets."""
    page = int(page_str)
    conversation.next_state = StateJS(tickets_page=page)
    return [await conversation._build_pick_tickets(f"{String.PICK_TICKETS_ACTION}.")]


@router.route("ticket:view")
async def view_ticket(conversation: Conversation, ticket: TicketDB) -> list:
    return [await conversation._build_pick_ticket_action("Viewing ticket...")]


@router.route("ticket:set:serial_number")
async def set_serial_number(
    conversation: Conversation,
    ticket: TicketDB,
    device_index_str: str,
    serial_number: str,
) -> list:
    device_index = int(device_index_str)
    return [await conversation._build_pick_ticket_action("Serial number updated.")]
