from __future__ import annotations
from typing import TYPE_CHECKING
import re
from sqlalchemy.orm import selectinload
from src.core.config import settings
from src.core.router import router
from src.core.callbacks import cb
from src.core.enums import String
from src.core.models import StateJS
from src.tg.models import MethodTG
from src.db.models import DeviceDB, TicketDB

if TYPE_CHECKING:
    from src.core.conversation import Conversation


@router.route(cb.device.EDIT_ACTION)
async def edit_device_action(conv: Conversation, device_id_str: str) -> list:
    device_id = int(device_id_str)
    conv.next_state = StateJS(pending_command_prefix=cb.device.edit_action(device_id))
    return [
        conv._build_device_action_menu(
            device_id,
            f"{String.DEVICE_ACTION_WAS_NOT_PICKED}. {String.PICK_INSTALL_OR_RETURN}.",
        ),
    ]


@router.route(cb.device.SET_ACTION)
async def set_device_action(
    conv: Conversation, device_id_str: str, removal_str: str
) -> list:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    device_id = int(device_id_str)
    device = await conv.session.get(
        DeviceDB, device_id, options=[selectinload(DeviceDB.type)]
    )
    if device:
        removal = bool(removal_str)
        if not (device.type.is_disposable and removal):
            result = await conv._get_ticket_if_eligible(device.ticket_id)
            if isinstance(result, TicketDB):
                ticket = result
                device.removal = removal
                if device.type.has_serial_number:
                    conv.next_state = StateJS(
                        pending_command_prefix=cb.device.add_serial_number(device.id)
                    )
                    methods_tg_list.append(
                        conv._build_new_text_message(f"{String.ENTER_SERIAL_NUMBER}.")
                    )
                else:
                    methods_tg_list.append(
                        conv._build_ticket_view(
                            ticket,
                            (
                                f"{String.DEVICE_ADDED}: "
                                f"{String[device.type.name.name]}. "
                                f"{String.PICK_TICKET_ACTION}."
                            ),
                        ),
                    )
            else:
                methods_tg_list.append(
                    conv._drop_state_goto_main_menu(
                        f"{result}. {String.PICK_A_FUNCTION}."
                    )
                )
        else:
            methods_tg_list.append(
                conv._drop_state_goto_main_menu(
                    f"{String.DEVICE_TYPE_IS_DISPOSABLE}. {String.PICK_A_FUNCTION}."
                )
            )
    else:
        methods_tg_list.append(
            conv._drop_state_goto_main_menu(
                f"{String.DEVICE_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
        )
    return methods_tg_list


@router.route(cb.device.ADD_SERIAL_NUMBER)
async def add_device_serial_number(conv: Conversation, device_id_str: str) -> list:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    device_id = int(device_id_str)
    device = await conv.session.get(DeviceDB, device_id)
    if device:
        if device.type.has_serial_number:
            result = await conv._get_ticket_if_eligible(device.ticket_id)
            if isinstance(result, TicketDB):
                conv.next_state = StateJS(
                    pending_command_prefix=cb.device.set_serial_number(device.id)
                )
                methods_tg_list.append(
                    conv._build_new_text_message(f"{String.ENTER_SERIAL_NUMBER}.")
                )
            else:
                methods_tg_list.append(
                    conv._drop_state_goto_main_menu(
                        f"{result}. {String.PICK_A_FUNCTION}."
                    )
                )
        else:
            methods_tg_list.append(
                conv._drop_state_goto_main_menu(
                    f"{String.DEVICE_TYPE_HAS_NO_SERIAL_NUMBER}. {String.PICK_A_FUNCTION}."
                )
            )
    else:
        methods_tg_list.append(
            conv._drop_state_goto_main_menu(
                f"{String.DEVICE_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
        )
    return methods_tg_list
