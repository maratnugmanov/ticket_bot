from __future__ import annotations
from typing import TYPE_CHECKING
import re
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from src.core.config import settings
from src.core.logger import logger
from src.core.router import router
from src.core.callbacks import cb
from src.core.enums import String
from src.core.models import StateJS
from src.tg.models import MethodTG
from src.db.models import TicketDB, DeviceDB, DeviceTypeDB

if TYPE_CHECKING:
    from src.core.conversation import Conversation


@router.route(cb.device.VIEW)
async def view_device(conv: Conversation, device_id_str: str) -> list:
    methods_tg_list: list[MethodTG] = [
        conv._build_edit_to_callback_button_text(prefix_text=String.DEVICE)
    ]
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
            methods_tg_list.append(
                conv._build_device_view(
                    ticket, device, f"{String.AVAILABLE_DEVICE_ACTIONS}."
                )
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


@router.route(cb.device.EDIT_TYPE)
async def edit_type_action(conv: Conversation, device_id_str: str) -> list:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    device_id = int(device_id_str)
    device = await conv.session.get(
        DeviceDB, device_id, options=[selectinload(DeviceDB.ticket)]
    )
    if device:
        result = await conv._get_ticket_if_eligible(device.ticket_id)
        if isinstance(result, TicketDB):
            ticket = result
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


@router.route(cb.device.SET_TYPE)
async def set_type_action(
    conv: Conversation, device_id_str: str, device_type_id_str: str
) -> list:
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
            device_types_result = await conv.session.scalars(
                select(DeviceTypeDB).where(DeviceTypeDB.is_active == True)  # noqa: E712
            )
            device_types = list(device_types_result)
            new_device_type_id = int(device_type_id_str)
            new_device_type = next(
                (dt for dt in device_types if dt.id == new_device_type_id), None
            )
            if new_device_type:
                if new_device_type.is_active:
                    if device.type.id != new_device_type.id:
                        text = (
                            f"{String.DEVICE_TYPE_WAS_CHANGED_FOR} "
                            f"{String[new_device_type.name.name]}"
                        )
                        old_device_type = device.type
                        device.type = new_device_type
                        if device.type.is_disposable:
                            if device.removal:
                                text = f"{text}. {String.RETURN_CHANGED_TO_INSTALL}"
                            elif device.removal is None:
                                text = f"{text}. {String.DEVICE_ACTION_SET_TO_INSTALL}"
                            device.removal = False
                            if device.type.has_serial_number:
                                if device.serial_number:
                                    methods_tg_list.append(
                                        conv._build_device_view(
                                            ticket,
                                            device,
                                            f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}.",
                                        ),
                                    )
                                else:
                                    conv.next_state = StateJS(
                                        pending_command_prefix=cb.device.set_serial_number(
                                            device.id
                                        )
                                    )
                                    methods_tg_list.append(
                                        conv._build_new_text_message(
                                            f"{text}. {String.ENTER_SERIAL_NUMBER}."
                                        )
                                    )
                            else:
                                if device.serial_number:
                                    text = f"{text}. {String.SERIAL_NUMBER_REMOVED}"
                                    device.serial_number = None
                                methods_tg_list.append(
                                    conv._build_device_view(
                                        ticket,
                                        device,
                                        f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}.",
                                    ),
                                )
                        else:
                            if old_device_type.is_disposable:
                                device.removal = None
                            if device.removal is None:
                                methods_tg_list.append(
                                    conv._build_set_device_action_menu(
                                        device.id,
                                        f"{text}. {String.PICK_INSTALL_OR_RETURN}.",
                                    )
                                )
                            else:
                                if device.type.has_serial_number:
                                    if device.serial_number:
                                        methods_tg_list.append(
                                            conv._build_device_view(
                                                ticket,
                                                device,
                                                f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}.",
                                            ),
                                        )
                                    else:
                                        conv.next_state = StateJS(
                                            pending_command_prefix=cb.device.set_serial_number(
                                                device.id
                                            )
                                        )
                                        methods_tg_list.append(
                                            conv._build_new_text_message(
                                                f"{text}. {String.ENTER_SERIAL_NUMBER}."
                                            )
                                        )
                                else:
                                    if device.serial_number:
                                        text = f"{text}. {String.SERIAL_NUMBER_REMOVED}"
                                        device.serial_number = None
                                    methods_tg_list.append(
                                        conv._build_device_view(
                                            ticket,
                                            device,
                                            f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}.",
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
                    methods_tg_list.append(
                        conv._build_set_device_type_menu(
                            ticket,
                            device_types,
                            (
                                f"{String.DEVICE_TYPE_IS_DISABLED}. "
                                f"{String.PICK_NEW_DEVICE_TYPE}."
                            ),
                            device,
                        ),
                    )
            else:
                methods_tg_list.append(
                    conv._build_set_device_type_menu(
                        ticket,
                        device_types,
                        (
                            f"{String.DEVICE_TYPE_NOT_FOUND}. "
                            f"{String.PICK_NEW_DEVICE_TYPE}."
                        ),
                        device,
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


@router.route(cb.device.EDIT_ACTION)
async def edit_device_action(conv: Conversation, device_id_str: str) -> list:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    device_id = int(device_id_str)
    device = await conv.session.get(
        DeviceDB, device_id, options=[selectinload(DeviceDB.ticket)]
    )
    if device:
        result = await conv._get_ticket_if_eligible(device.ticket_id)
        if isinstance(result, TicketDB):
            methods_tg_list.append(
                conv._build_set_device_action_menu(
                    device_id,
                    f"{String.PICK_INSTALL_OR_RETURN}.",
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
        result = await conv._get_ticket_if_eligible(
            device.ticket_id,
            loader_options=[
                selectinload(TicketDB.contract),
                selectinload(TicketDB.devices).selectinload(DeviceDB.type),
            ],
        )
        if isinstance(result, TicketDB):
            ticket = result
            removal = bool(int(removal_str))
            if device.removal == removal:
                text = f"{String.INSTALL_OR_RETURN_REMAINED_THE_SAME}"
            elif removal:
                text = f"{String.DEVICE_ACTION_SET_TO_RETURN}"
            else:
                text = f"{String.DEVICE_ACTION_SET_TO_INSTALL}"
            if not device.type.is_disposable or (
                device.type.is_disposable and not removal
            ):
                device.removal = removal
                if device.type.has_serial_number:
                    if device.serial_number:
                        methods_tg_list.append(
                            conv._build_device_view(
                                ticket,
                                device,
                                f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}.",
                            ),
                        )
                    else:
                        conv.next_state = StateJS(
                            pending_command_prefix=cb.device.set_serial_number(
                                device.id
                            )
                        )
                        methods_tg_list.append(
                            conv._build_new_text_message(
                                f"{text}. {String.ENTER_SERIAL_NUMBER}."
                            )
                        )
                else:
                    if device.serial_number:
                        text = f"{text}. {String.SERIAL_NUMBER_REMOVED}"
                        device.serial_number = None
                    methods_tg_list.append(
                        conv._build_device_view(
                            ticket,
                            device,
                            f"{text}. {String.AVAILABLE_DEVICE_ACTIONS}.",
                        ),
                    )
            else:
                device.removal = False
                methods_tg_list.append(
                    conv._build_device_view(
                        ticket,
                        device,
                        (
                            f"{text}. "
                            f"{String.DEVICE_TYPE_IS_DISPOSABLE}. "
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


@router.route(cb.device.EDIT_SERIAL_NUMBER)
async def edit_device_serial_number(conv: Conversation, device_id_str: str) -> list:
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
                text = (
                    String.ENTER_NEW_SERIAL_NUMBER
                    if device.serial_number
                    else String.ENTER_SERIAL_NUMBER
                )
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
) -> list:
    methods_tg_list: list[MethodTG] = []
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
                new_serial_number = new_serial_number.strip().upper()
                if (
                    re.fullmatch(settings.serial_number_regex, new_serial_number)
                    and len(new_serial_number) <= settings.serial_number_max_length
                ):
                    if device.serial_number != new_serial_number:
                        text = (
                            String.SERIAL_NUMBER_EDITED
                            if device.serial_number
                            else String.SERIAL_NUMBER_ADDED
                        )
                        device.serial_number = new_serial_number
                    else:
                        text = String.SERIAL_NUMBER_REMAINED_THE_SAME
                    methods_tg_list.append(
                        conv._build_device_view(
                            ticket,
                            device,
                            f"{text}. {String.AVAILABLE_TICKET_ACTIONS}.",
                        )
                    )
                else:
                    conv.next_state = StateJS(
                        pending_command_prefix=cb.device.set_serial_number(device_id)
                    )
                    methods_tg_list.append(
                        conv._build_new_text_message(
                            f"{String.INCORRECT_SERIAL_NUMBER}. "
                            f"{String.ENTER_NEW_SERIAL_NUMBER}."
                        )
                    )
            else:
                device.serial_number = None
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


@router.route(cb.device.DELETE)
async def delete_action(conv: Conversation, device_id_str: str) -> list:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    device_id = int(device_id_str)
    device = await conv.session.get(DeviceDB, device_id)
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
                        f"{String.TRASHCAN_ICON} "
                        f"{String.DEVICE_WAS_DELETED_FROM_TICKET}: "
                        f"{String[device_type_name]}. "
                        f"{String.AVAILABLE_TICKET_ACTIONS}."
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
