from __future__ import annotations
from typing import TYPE_CHECKING
import re
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload
from src.core.config import settings
from src.core.logger import logger
from src.core.router import router
from src.core.callbacks import cb
from src.core.enums import DeviceStatus, String
from src.core.models import StateJS
from src.tg.models import MethodTG, SendMessageTG
from src.db.models import TicketDB, DeviceDB, DeviceTypeDB

if TYPE_CHECKING:
    from src.core.conversation import Conversation


@router.route(cb.device.VIEW)
async def view_device(conv: Conversation, device_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    result = await conv._get_device_for_editing(device_id_str)
    if not isinstance(result, SendMessageTG):
        device, ticket = result
        device_overview_text = (
            f"{String.DEVICE} {conv._get_device_overview(device, ticket)} >>"
        )
        methods_tg_list.append(conv._build_edit_to_text_message(device_overview_text))
        methods_tg_list.append(
            conv._build_device_view(
                device, ticket, f"{String.AVAILABLE_DEVICE_ACTIONS}."
            )
        )
    else:
        methods_tg_list.append(
            conv._build_edit_to_callback_button_text(prefix_text=String.DEVICE)
        )
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.device.EDIT_TYPE)
async def edit_device_type(conv: Conversation, device_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_device_for_editing(device_id_str)
    if not isinstance(result, SendMessageTG):
        device, ticket = result
        device_types = await conv._get_active_device_types()
        methods_tg_list.append(
            conv._build_set_device_type_menu(
                ticket, device_types, f"{String.PICK_NEW_DEVICE_TYPE}.", device
            )
        )
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.device.SET_TYPE)
async def set_device_type(
    conv: Conversation, device_id_str: str, device_type_id_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_device_for_editing(device_id_str)
    if not isinstance(result, SendMessageTG):
        device, ticket = result
        new_device_type_id = int(device_type_id_str)
        new_device_type = await conv.session.get(
            DeviceTypeDB,
            new_device_type_id,
            options=[joinedload(DeviceTypeDB.statuses)],
        )
        if (
            new_device_type
            and new_device_type.is_active
            and len(new_device_type.statuses) > 0
        ):
            old_device_type = device.type
            if old_device_type.id == new_device_type.id:
                text = (
                    f"{String.DEVICE_TYPE_REMAINED_THE_SAME}: "
                    f"{String[new_device_type.name.name]}"
                )
                methods_tg_list.append(
                    conv._handle_device_status_update(
                        device.status, device, ticket, text
                    )
                )
            else:
                text = (
                    f"{String.DEVICE_TYPE_WAS_CHANGED_FOR} "
                    f"{String[new_device_type.name.name]}"
                )
                device.type = new_device_type
                if len(device.type.statuses) == 1:
                    new_device_status = device.type.statuses[0]
                    methods_tg_list.append(
                        conv._handle_device_status_update(
                            new_device_status, device, ticket, text
                        )
                    )
                else:
                    device.status = None
                    methods_tg_list.append(
                        conv._build_set_device_status_menu(
                            device, f"{text}. {String.PICK_DEVICE_ACTION}."
                        )
                    )
        else:
            if not new_device_type:
                text = String.DEVICE_TYPE_NOT_FOUND
            elif not new_device_type.is_active:
                text = String.DEVICE_TYPE_IS_DISABLED
            else:
                text = String.DEVICE_TYPE_HAS_NO_ACTIONS
            device_types = await conv._get_active_device_types()
            methods_tg_list.append(
                conv._build_set_device_type_menu(
                    ticket,
                    device_types,
                    f"{text}. {String.PICK_NEW_DEVICE_TYPE}.",
                    device,
                ),
            )
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.device.EDIT_STATUS)
async def edit_device_status(conv: Conversation, device_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_device_for_editing(device_id_str)
    if not isinstance(result, SendMessageTG):
        device, ticket = result
        methods_tg_list.append(
            conv._build_set_device_status_menu(device, f"{String.PICK_DEVICE_ACTION}."),
        )
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.device.SET_STATUS)
async def set_device_status(
    conv: Conversation, device_id_str: str, device_status_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_device_for_editing(device_id_str)
    if not isinstance(result, SendMessageTG):
        device, ticket = result
        try:
            new_device_status_enum = DeviceStatus(device_status_str)
            new_device_status = next(
                (
                    status
                    for status in device.type.statuses
                    if status.name == new_device_status_enum
                ),
                None,
            )
            if new_device_status:
                methods_tg_list.append(
                    conv._handle_device_status_update(new_device_status, device, ticket)
                )
            else:
                text = f"{String.INELIGIBLE_DEVICE_TYPE_ACTION}. {String.PICK_DEVICE_ACTION}."
                methods_tg_list.append(conv._build_set_device_status_menu(device, text))
        except ValueError:
            text = f"{String.UNRECOGNIZED_DEVICE_ACTION}. {String.PICK_DEVICE_ACTION}."
            methods_tg_list.append(conv._build_set_device_status_menu(device, text))
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.device.EDIT_SERIAL_NUMBER)
async def edit_device_serial_number(
    conv: Conversation, device_id_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_device_for_editing(device_id_str)
    if not isinstance(result, SendMessageTG):
        device, ticket = result
        if device.type.has_serial_number:
            conv.next_state = StateJS(
                pending_command_prefix=cb.device.set_serial_number(device.id)
            )
            if device.serial_number:
                text = String.ENTER_NEW_SERIAL_NUMBER
            else:
                text = String.ENTER_SERIAL_NUMBER
            methods_tg_list.append(conv._build_new_text_message(f"{text}."))
        else:
            methods_tg_list.append(
                conv._build_device_view(
                    device,
                    ticket,
                    (
                        f"{String.DEVICE_TYPE_HAS_NO_SERIAL_NUMBER}. "
                        f"{String.AVAILABLE_DEVICE_ACTIONS}."
                    ),
                ),
            )
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.device.SET_SERIAL_NUMBER)
async def set_device_serial_number(
    conv: Conversation, device_id_str: str, new_serial_number: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    result = await conv._get_device_for_editing(device_id_str)
    if not isinstance(result, SendMessageTG):
        device, ticket = result
        if device.type.has_serial_number:
            new_serial_number = new_serial_number.strip().upper()
            if (
                re.fullmatch(settings.serial_number_regex, new_serial_number)
                and len(new_serial_number) <= settings.serial_number_max_length
            ):
                if device.serial_number != new_serial_number:
                    if device.serial_number:
                        text = f"{String.SERIAL_NUMBER_EDITED}"
                    else:
                        text = f"{String.SERIAL_NUMBER_ADDED}"
                    device.serial_number = new_serial_number
                else:
                    text = f"{String.SERIAL_NUMBER_REMAINED_THE_SAME}"
                text = f"{text}. {String.AVAILABLE_TICKET_ACTIONS}."
                methods_tg_list.append(conv._build_device_view(device, ticket, text))
            else:
                conv.next_state = StateJS(
                    pending_command_prefix=cb.device.set_serial_number(device.id)
                )
                text = (
                    f"{String.INCORRECT_SERIAL_NUMBER}. "
                    f"{String.ENTER_NEW_SERIAL_NUMBER}."
                )
                methods_tg_list.append(conv._build_new_text_message(text))
        else:
            device.serial_number = None
            text = (
                f"{String.DEVICE_TYPE_HAS_NO_SERIAL_NUMBER}. "
                f"{String.AVAILABLE_DEVICE_ACTIONS}."
            )
            methods_tg_list.append(conv._build_device_view(device, ticket, text))
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.device.DELETE)
async def delete_device(conv: Conversation, device_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_device_for_editing(device_id_str)
    if not isinstance(result, SendMessageTG):
        device, ticket = result
        device_type_name = device.type.name.name
        await conv.session.delete(device)
        await conv.session.flush()
        await conv.session.refresh(
            ticket,
            attribute_names=[TicketDB.devices.key],
        )
        methods_tg_list.append(
            conv._build_ticket_view(
                ticket,
                (
                    f"{String.TRASHCAN_ICON} "  # nbsp
                    f"{String.DEVICE_DELETED}: "
                    f"{String[device_type_name]}. "
                    f"{String.AVAILABLE_TICKET_ACTIONS}."
                ),
            ),
        )
    else:
        methods_tg_list.append(result)
    return methods_tg_list
