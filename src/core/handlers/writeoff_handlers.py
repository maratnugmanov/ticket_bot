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
from src.db.models import (
    TicketDB,
    WriteoffDeviceDB,
    DeviceDB,
    DeviceTypeDB,
    DeviceStatusDB,
)

if TYPE_CHECKING:
    from src.core.conversation import Conversation


@router.route(cb.writeoff.LIST)
async def list_writeoffs(conv: Conversation, page_str: str = "0") -> list[MethodTG]:
    """Handles the command to list writeoff devices."""
    page = int(page_str)
    (
        writeoffs,
        page,
        last_page,
        total_writeoffs,
    ) = await conv._get_paginated_writeoffs(page)
    return [
        conv._build_edit_to_text_message(f"{String.WRITEOFF_DEVICES} >>"),
        conv._build_writeoff_devices_list(
            writeoffs,
            page,
            last_page,
            total_writeoffs,
            f"{String.AVAILABLE_WRITEOFF_DEVICES_ACTIONS}.",
        ),
    ]


@router.route(cb.writeoff.VIEW)
async def view_writeoff(conv: Conversation, writeoff_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    result = await conv._get_writeoff_for_editing(writeoff_id_str)
    if not isinstance(result, SendMessageTG):
        writeoff = result
        writeoff_overview_text = (
            f"{String.WRITEOFF} {conv._get_writeoff_overview(writeoff)}"
        )
        methods_tg_list.append(conv._build_edit_to_text_message(writeoff_overview_text))
        text = f"{String.AVAILABLE_WRITEOFF_DEVICE_ACTIONS}."
        methods_tg_list.append(conv._build_writeoff_view(writeoff, text))
    else:
        methods_tg_list.append(conv._build_edit_to_callback_button_text())
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.writeoff.EDIT_TYPE)
async def edit_writeoff_type(
    conv: Conversation, writeoff_id_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_writeoff_for_editing(writeoff_id_str)
    if not isinstance(result, SendMessageTG):
        writeoff = result
        device_types = await conv._get_active_writeoff_device_types()
        methods_tg_list.append(
            await conv._build_set_writeoff_device_type_menu(
                device_types, f"{String.PICK_NEW_WRITEOFF_DEVICE_TYPE}.", writeoff
            )
        )
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.writeoff.SET_TYPE)
async def set_writeoff_type(
    conv: Conversation, writeoff_id_str: str, device_type_id_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_writeoff_for_editing(writeoff_id_str)
    if not isinstance(result, SendMessageTG):
        writeoff = result
        new_device_type_id = int(device_type_id_str)
        new_device_type = await conv.session.get(
            DeviceTypeDB,
            new_device_type_id,
            options=[joinedload(DeviceTypeDB.statuses)],
        )
        if (
            new_device_type
            and new_device_type.is_active
            and any(
                status.name == DeviceStatus.RETURN
                for status in new_device_type.statuses
            )
        ):
            if writeoff.type.id != new_device_type.id:
                text = (
                    f"{String.DEVICE_TYPE_WAS_CHANGED_FOR} "
                    f"{String[new_device_type.name.name]}"
                )
                writeoff.type = new_device_type
            else:
                text = (
                    f"{String.DEVICE_TYPE_REMAINED_THE_SAME}: "
                    f"{String[new_device_type.name.name]}"
                )
            if writeoff.type.has_serial_number:
                if not writeoff.serial_number:
                    conv.next_state = StateJS(
                        pending_command_prefix=cb.writeoff.set_serial_number(
                            writeoff.id
                        )
                    )
                    text = f"{text}. {String.ENTER_SERIAL_NUMBER}."
                    methods_tg_list.append(conv._build_new_text_message(text))
                else:
                    text = f"{text}. {String.AVAILABLE_WRITEOFF_DEVICE_ACTIONS}."
                    methods_tg_list.append(conv._build_writeoff_view(writeoff, text))
            else:
                if writeoff.serial_number:
                    text = f"{text}. {String.SERIAL_NUMBER_REMOVED}"
                    writeoff.serial_number = None
                text = f"{text}. {String.AVAILABLE_WRITEOFF_DEVICE_ACTIONS}."
                methods_tg_list.append(conv._build_writeoff_view(writeoff, text))
        else:
            if not new_device_type:
                text = String.DEVICE_TYPE_NOT_FOUND
            elif not new_device_type.is_active:
                text = String.DEVICE_TYPE_IS_DISABLED
            else:
                text = String.DEVICE_TYPE_HAS_NO_ACTIONS
            device_types = await conv._get_active_writeoff_device_types()
            methods_tg_list.append(
                await conv._build_set_writeoff_device_type_menu(
                    device_types,
                    f"{text}. {String.PICK_WRITEOFF_DEVICE_TYPE}.",
                    writeoff,
                )
            )
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.writeoff.CREATE_START)
async def create_writeoff_start(conv: Conversation) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    device_types = await conv._get_active_writeoff_device_types()
    methods_tg_list.append(
        await conv._build_set_writeoff_device_type_menu(
            device_types,
            f"{String.PICK_WRITEOFF_DEVICE_TYPE}.",
        )
    )
    return methods_tg_list


@router.route(cb.writeoff.CREATE_CONFIRM)
async def create_writeoff_confirm(
    conv: Conversation, device_type_id_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    device_type_id = int(device_type_id_str)
    device_type = await conv.session.get(
        DeviceTypeDB,
        device_type_id,
        options=[joinedload(DeviceTypeDB.statuses)],
    )
    if (
        device_type
        and device_type.is_active
        and any(status.name == DeviceStatus.RETURN for status in device_type.statuses)
    ):
        new_writeoff = WriteoffDeviceDB(
            user_id=conv.user_db.id,
            type_id=device_type.id,
        )
        conv.session.add(new_writeoff)
        await conv.session.flush()
        if new_writeoff.type.has_serial_number:
            conv.next_state = StateJS(
                pending_command_prefix=cb.writeoff.set_serial_number(new_writeoff.id)
            )
            methods_tg_list.append(
                conv._build_new_text_message(f"{String.ENTER_SERIAL_NUMBER}.")
            )
        else:
            (
                writeoffs,
                page,
                last_page,
                total_writeoffs,
            ) = await conv._get_paginated_writeoffs(0)
            methods_tg_list.append(
                conv._build_writeoff_devices_list(
                    writeoffs,
                    page,
                    last_page,
                    total_writeoffs,
                    (
                        f"{String.WRITEOFF_ICON} "
                        f"{String.DEVICE_ADDED}: "
                        f"{String[new_writeoff.type.name.name]}. "
                        f"{String.AVAILABLE_WRITEOFF_DEVICES_ACTIONS}."
                    ),
                ),
            )
    else:
        if not device_type:
            text = String.DEVICE_TYPE_NOT_FOUND
        elif not device_type.is_active:
            text = String.DEVICE_TYPE_IS_DISABLED
        else:
            text = String.DEVICE_TYPE_IS_DISPOSABLE
        device_types = await conv._get_active_writeoff_device_types()
        methods_tg_list.append(
            await conv._build_set_writeoff_device_type_menu(
                device_types, f"{text}. {String.PICK_WRITEOFF_DEVICE_TYPE}."
            )
        )
    return methods_tg_list


@router.route(cb.writeoff.EDIT_SERIAL_NUMBER)
async def edit_writeoff_serial_number(
    conv: Conversation, writeoff_id_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_writeoff_for_editing(writeoff_id_str)
    if not isinstance(result, SendMessageTG):
        writeoff = result
        if writeoff.type.has_serial_number:
            conv.next_state = StateJS(
                pending_command_prefix=cb.writeoff.set_serial_number(writeoff.id)
            )
            if writeoff.serial_number:
                text = String.ENTER_NEW_SERIAL_NUMBER
            else:
                text = String.ENTER_SERIAL_NUMBER
            methods_tg_list.append(conv._build_new_text_message(f"{text}."))
        else:
            methods_tg_list.append(
                conv._build_writeoff_view(
                    writeoff,
                    (
                        f"{String.DEVICE_TYPE_HAS_NO_SERIAL_NUMBER}. "
                        f"{String.AVAILABLE_WRITEOFF_DEVICE_ACTIONS}."
                    ),
                ),
            )
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.writeoff.SET_SERIAL_NUMBER)
async def set_writeoff_serial_number(
    conv: Conversation, writeoff_id_str: str, new_serial_number: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    result = await conv._get_writeoff_for_editing(writeoff_id_str)
    if not isinstance(result, SendMessageTG):
        writeoff = result
        if writeoff.type.has_serial_number:
            new_serial_number = new_serial_number.strip().upper()
            if (
                re.fullmatch(settings.serial_number_regex, new_serial_number)
                and len(new_serial_number) <= settings.serial_number_max_length
            ):
                if writeoff.serial_number != new_serial_number:
                    if writeoff.serial_number:
                        text = f"{String.SERIAL_NUMBER_EDITED}"
                    else:
                        text = f"{String.SERIAL_NUMBER_ADDED}"
                    writeoff.serial_number = new_serial_number
                else:
                    text = f"{String.SERIAL_NUMBER_REMAINED_THE_SAME}"
                text = f"{text}. {String.AVAILABLE_WRITEOFF_DEVICE_ACTIONS}."
                methods_tg_list.append(conv._build_writeoff_view(writeoff, text))
            else:
                conv.next_state = StateJS(
                    pending_command_prefix=cb.writeoff.set_serial_number(writeoff.id)
                )
                text = (
                    f"{String.INCORRECT_SERIAL_NUMBER}. "
                    f"{String.ENTER_NEW_SERIAL_NUMBER}."
                )
                methods_tg_list.append(conv._build_new_text_message(text))
        else:
            writeoff.serial_number = None
            text = (
                f"{String.DEVICE_TYPE_HAS_NO_SERIAL_NUMBER}. "
                f"{String.AVAILABLE_WRITEOFF_DEVICE_ACTIONS}."
            )
            methods_tg_list.append(conv._build_writeoff_view(writeoff, text))
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.writeoff.DELETE_START)
async def delete_writeoff_start(
    conv: Conversation, writeoff_id_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    result = await conv._get_writeoff_for_editing(writeoff_id_str)
    if not isinstance(result, SendMessageTG):
        writeoff = result
        writeoff_overview_text = (
            f"{String.WRITEOFF} {conv._get_writeoff_overview(writeoff)}"
        )
        methods_tg_list.append(conv._build_edit_to_text_message(writeoff_overview_text))
        text = f"{writeoff_overview_text}. {String.CONFIRM_WRITEOFF_DEVICE_DELETION}."
        methods_tg_list.append(
            conv._build_confirm_writeoff_deletion_menu(writeoff.id, text)
        )
    else:
        methods_tg_list.append(conv._build_edit_to_callback_button_text())
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.writeoff.DELETE_CONFIRM)
async def delete_writeoff_confirm(
    conv: Conversation, writeoff_id_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    result = await conv._get_writeoff_for_editing(writeoff_id_str)
    if not isinstance(result, SendMessageTG):
        writeoff = result
        writeoff_overview_text = conv._get_writeoff_overview(writeoff)
        text = (
            f"{String.WARNING_ICON} "  # nbsp
            f"{String.CONFIRM_DELETE_WRITEOFF} "
            f"{writeoff_overview_text}"
        )
        methods_tg_list.append(conv._build_edit_to_text_message(text))
        await conv.session.delete(writeoff)
        await conv.session.flush()
        (
            writeoffs,
            page,
            last_page,
            total_writeoffs,
        ) = await conv._get_paginated_writeoffs(0)
        methods_tg_list.append(
            conv._build_writeoff_devices_list(
                writeoffs,
                page,
                last_page,
                total_writeoffs,
                (
                    f"{String.TRASHCAN_ICON} "  # nbsp
                    f"{String.WRITEOFF_DEVICE_DELETED}: "
                    f"{writeoff_overview_text}. "
                    f"{String.AVAILABLE_WRITEOFF_DEVICES_ACTIONS}."
                ),
            ),
        )
    else:
        methods_tg_list.append(conv._build_edit_to_callback_button_text())
        methods_tg_list.append(result)
    return methods_tg_list
