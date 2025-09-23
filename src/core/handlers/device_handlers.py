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
from src.tg.models import SendMessageTG, MethodTG
from src.db.models import TicketDB, DeviceDB, DeviceTypeDB

if TYPE_CHECKING:
    from src.core.conversation import Conversation


@router.route(cb.device.VIEW)
async def view_device(conv: Conversation, device_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [
        conv._build_edit_to_callback_button_text(prefix_text=String.DEVICE)
    ]
    result = await conv._get_device_for_editing(device_id_str)
    if not isinstance(result, SendMessageTG):
        ticket, device = result
        methods_tg_list.append(
            conv._build_device_view(
                ticket, device, f"{String.AVAILABLE_DEVICE_ACTIONS}."
            )
        )
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.device.EDIT_TYPE)
async def edit_type_action(conv: Conversation, device_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_device_for_editing(device_id_str)
    if not isinstance(result, SendMessageTG):
        ticket, device = result
        device_types_result = await conv.session.scalars(
            select(DeviceTypeDB).where(DeviceTypeDB.is_active == True)  # noqa: E712
        )
        device_types = list(device_types_result)
        methods_tg_list.append(
            conv._build_set_device_type_menu(
                ticket, device_types, f"{String.PICK_NEW_DEVICE_TYPE}.", device
            )
        )
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.device.SET_TYPE)
async def set_type_action(
    conv: Conversation, device_id_str: str, device_type_id_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_device_for_editing(device_id_str)
    if not isinstance(result, SendMessageTG):
        ticket, device = result
        device_types_result = await conv.session.scalars(
            select(DeviceTypeDB).where(DeviceTypeDB.is_active == True)  # noqa: E712
        )
        device_types = list(device_types_result)
        new_device_type_id = int(device_type_id_str)
        new_device_type = next(
            (dt for dt in device_types if dt.id == new_device_type_id), None
        )
        if (
            new_device_type
            and new_device_type.is_active
            and len(new_device_type.statuses) > 0
        ):
            if device.type.id != new_device_type.id:
                text = (
                    f"{String.DEVICE_TYPE_WAS_CHANGED_FOR} "
                    f"{String[new_device_type.name.name]}"
                )
                device.type = new_device_type
                if len(device.type.statuses) == 1:
                    old_device_status = device.status
                    new_device_status = device.type.statuses[0]
                    new_status_icon = conv._get_device_status_icon(new_device_status)
                    device.status = new_device_status
                    if old_device_status:
                        old_status_icon = conv._get_device_status_icon(
                            old_device_status
                        )
                        text = (
                            f"{text}. {String.DEVICE_ACTION_CHANGED}: "
                            f"{old_status_icon} {String[old_device_status.name.name]} >> "  # nbsp
                            f"{new_status_icon} {String[new_device_status.name.name]}"  # nbsp
                        )
                    else:
                        text = (
                            f"{text}. {String.DEVICE_ACTION_SET_TO}: "
                            f"{new_status_icon} {String[new_device_status.name.name]}"  # nbsp
                        )
                    if device.type.has_serial_number:
                        if device.serial_number:
                            text = f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}."
                            methods_tg_list.append(
                                conv._build_device_view(ticket, device, text),
                            )
                        else:
                            conv.next_state = StateJS(
                                pending_command_prefix=cb.device.set_serial_number(
                                    device.id
                                )
                            )
                            text = f"{text}. {String.ENTER_SERIAL_NUMBER}."
                            methods_tg_list.append(conv._build_new_text_message(text))
                    else:
                        if device.serial_number:
                            text = f"{text}. {String.SERIAL_NUMBER_REMOVED}"
                            device.serial_number = None
                        text = f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}."
                        methods_tg_list.append(
                            conv._build_device_view(ticket, device, text),
                        )
                else:
                    methods_tg_list.append(
                        conv._build_set_device_status_menu(
                            device, f"{String.PICK_DEVICE_ACTION}."
                        ),
                    )
            else:
                text = String.DEVICE_TYPE_REMAINED_THE_SAME
                methods_tg_list.append(
                    conv._build_device_view(
                        ticket,
                        device,
                        f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}.",
                    ),
                )
        else:
            if not new_device_type:
                text = String.DEVICE_TYPE_NOT_FOUND
            elif not new_device_type.is_active:
                text = String.DEVICE_TYPE_IS_DISABLED
            else:
                text = String.DEVICE_TYPE_HAS_NO_ACTIONS
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
        ticket, device = result
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
        ticket, device = result
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
                new_status_icon = conv._get_device_status_icon(new_device_status)
                old_device_status = device.status
                if (
                    not old_device_status
                    or old_device_status.id != new_device_status.id
                ):
                    device.status = new_device_status
                    if old_device_status:
                        old_status_icon = conv._get_device_status_icon(
                            old_device_status
                        )
                        text = (
                            f"{String.DEVICE_ACTION_CHANGED}: "
                            f"{old_status_icon} {String[old_device_status.name.name]} >> "  # nbsp
                            f"{new_status_icon} {String[new_device_status.name.name]}"  # nbsp
                        )
                    else:
                        text = (
                            f"{String.DEVICE_ACTION_SET_TO} "
                            f"{String[new_device_status.name.name]}"
                        )
                else:
                    text = f"{String.DEVICE_ACTION_REMAINED_THE_SAME}"
                if device.type.has_serial_number:
                    if device.serial_number:
                        text = f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}."
                        methods_tg_list.append(
                            conv._build_device_view(ticket, device, text),
                        )
                    else:
                        conv.next_state = StateJS(
                            pending_command_prefix=cb.device.set_serial_number(
                                device.id
                            )
                        )
                        text = f"{text}. {String.ENTER_SERIAL_NUMBER}."
                        methods_tg_list.append(conv._build_new_text_message(text))
                else:
                    if device.serial_number:
                        text = f"{text}. {String.SERIAL_NUMBER_REMOVED}"
                        device.serial_number = None
                    text = f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}."
                    methods_tg_list.append(
                        conv._build_device_view(ticket, device, text),
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
    device_id = int(device_id_str)
    device = await conv.session.get(
        DeviceDB, device_id, options=[selectinload(DeviceDB.type)]
    )
    if device:
        result = await conv._get_ticket_if_eligible(
            device.ticket_id,
            loader_options=[
                selectinload(TicketDB.contract),
                selectinload(TicketDB.devices).selectinload(DeviceDB.type),
            ],
        )
        if isinstance(result, TicketDB):
            ticket = result
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
                        ticket,
                        device,
                        (
                            f"{String.DEVICE_TYPE_HAS_NO_SERIAL_NUMBER}. "
                            f"{String.AVAILABLE_DEVICE_ACTIONS}."
                        ),
                    ),
                )
        else:
            methods_tg_list.append(
                conv._drop_state_goto_main_menu(f"{result}. {String.PICK_A_FUNCTION}.")
            )
    else:
        methods_tg_list.append(
            conv._drop_state_goto_main_menu(
                f"{String.DEVICE_NOT_FOUND}. {String.PICK_A_FUNCTION}."
            )
        )
    return methods_tg_list


@router.route(cb.device.SET_SERIAL_NUMBER)
async def set_device_serial_number(
    conv: Conversation, device_id_str: str, new_serial_number: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    result = await conv._get_device_for_editing(device_id_str)
    if not isinstance(result, SendMessageTG):
        ticket, device = result
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
                methods_tg_list.append(conv._build_device_view(ticket, device, text))
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
            methods_tg_list.append(
                conv._build_device_view(ticket, device, text),
            )
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.device.DELETE)
async def delete_action(conv: Conversation, device_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_device_for_editing(device_id_str)
    if not isinstance(result, SendMessageTG):
        ticket, device = result
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
                    f"{String.TICKET_DELETED}: "
                    f"{String[device_type_name]}. "
                    f"{String.AVAILABLE_TICKET_ACTIONS}."
                ),
            ),
        )
    else:
        methods_tg_list.append(result)
    return methods_tg_list
