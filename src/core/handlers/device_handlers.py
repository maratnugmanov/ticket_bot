from __future__ import annotations
import re
from typing import TYPE_CHECKING
from sqlalchemy.orm import selectinload
from src.core.config import settings
from src.core.router import router
from src.core.enums import String
from src.core.conversation import Conversation
from src.db.models import DeviceDB, TicketDB

if TYPE_CHECKING:
    from src.core.conversation import Conversation


@router.route("device:set:serial_number")
async def set_device_serial_number(
    conversation: Conversation,
    device_id_str: str,
    serial_number: str,
) -> list:
    """
    Handles the final command to set a device's serial number,
    including validation.
    """
    # 1. Validate the input from the user.
    if not re.fullmatch(settings.serial_number_regex, serial_number.upper()):
        # 2. If invalid, re-prompt and preserve the state for the next attempt.
        conversation.next_state = conversation.state
        return [
            conversation._build_new_text_message(
                f"{String.INCORRECT_SERIAL_NUMBER}. {String.ENTER_SERIAL_NUMBER}."
            )
        ]

    device_id = int(device_id_str)

    # 3. If valid, proceed with the business logic.
    # Directly load the device and the ticket it belongs to.
    device = await conversation.session.get(
        DeviceDB,
        device_id,
        options=[selectinload(DeviceDB.ticket)],  # Load the parent ticket
    )

    if not device or not device.ticket:
        # Handle case where device is not found
        return [conversation._drop_state_goto_mainmenu("Error: Device not found.")]

    # The core logic is clean and isolated.
    device.serial_number = serial_number.upper()

    # To return the user to the ticket view, we need to rebuild that view.
    # This implies your _build_... methods will also be refactored to
    # take data objects (like `device.ticket`) as arguments.
    # 4. Clear the state and return the user to the ticket view.
    conversation.next_state = None
    return [
        await conversation._build_pick_ticket_action(
            ticket=device.ticket, text=f"{String.SERIAL_NUMBER_WAS_EDITED}."
        )
    ]
