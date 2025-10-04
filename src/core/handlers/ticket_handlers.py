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
from src.db.models import ContractDB, TicketDB, DeviceDB, DeviceStatusDB, DeviceTypeDB

if TYPE_CHECKING:
    from src.core.conversation import Conversation


@router.route(cb.ticket.LIST)
async def list_tickets(conv: Conversation, page_str: str = "0") -> list[MethodTG]:
    """Handles the command to list tickets."""
    page = int(page_str)
    tickets, page, last_page = await conv._get_paginated_tickets(page)
    return [
        conv._build_edit_to_text_message(f"{String.ALL_TICKETS} >>"),
        conv._build_tickets_list(
            tickets, page, last_page, f"{String.AVAILABLE_TICKETS_ACTIONS}."
        ),
    ]


@router.route(cb.ticket.VIEW)
async def view_ticket(conv: Conversation, ticket_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    result = await conv._get_ticket_if_eligible(
        ticket_id_str,
        loader_options=[
            joinedload(TicketDB.contract),
            joinedload(TicketDB.devices).options(
                joinedload(DeviceDB.type).selectinload(DeviceTypeDB.statuses),
                joinedload(DeviceDB.status),
            ),
        ],
    )
    if isinstance(result, TicketDB):
        ticket = result
        ticket_overview_text = f"{String.TICKET} {conv._get_ticket_overview(ticket)}"
        methods_tg_list.append(conv._build_edit_to_text_message(ticket_overview_text))
        text = (
            f"{String.AVAILABLE_TICKET_ACTIONS}."
            if not ticket.is_closed
            else (
                f"{String.ATTENTION_ICON} "  # nbsp
                f"{String.READONLY_MODE}. "
                f"{String.CANNOT_EDIT_CLOSED_TICKET}."
            )
        )
        methods_tg_list.append(
            conv._build_ticket_view(ticket, text),
        )
    else:
        methods_tg_list.append(conv._build_edit_to_callback_button_text())
        methods_tg_list.append(
            conv._drop_state_goto_main_menu(f"{result}. {String.PICK_A_FUNCTION}."),
        )
    return methods_tg_list


@router.route(cb.ticket.CREATE_START)
async def create_ticket_start(conv: Conversation) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    conv.next_state = StateJS(pending_command_prefix=cb.ticket.create_confirm())
    methods_tg_list.append(
        conv._build_new_text_message(f"{String.ENTER_TICKET_NUMBER}.")
    )
    return methods_tg_list


@router.route(cb.ticket.CREATE_CONFIRM)
async def create_ticket_confirm(
    conv: Conversation, ticket_number_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    ticket_number_str = ticket_number_str.strip().lstrip("0")
    if (
        re.fullmatch(settings.ticket_number_regex, ticket_number_str)
        and len(ticket_number_str) <= settings.ticket_number_max_length
    ):
        ticket_number = int(ticket_number_str)
        new_ticket = TicketDB(number=ticket_number, user_id=conv.user_db.id)
        conv.session.add(new_ticket)
        await conv.session.flush()
        conv.next_state = StateJS(
            pending_command_prefix=cb.ticket.set_contract(new_ticket.id)
        )
        methods_tg_list.append(
            conv._build_new_text_message(f"{String.ENTER_CONTRACT_NUMBER}.")
        )
    else:
        conv.next_state = StateJS(pending_command_prefix=cb.ticket.create_confirm())
        methods_tg_list.append(
            conv._build_new_text_message(
                f"{String.INCORRECT_TICKET_NUMBER}. {String.ENTER_TICKET_NUMBER}."
            )
        )
    return methods_tg_list


@router.route(cb.ticket.DELETE_START)
async def delete_ticket_start(conv: Conversation, ticket_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_ticket_for_editing(ticket_id_str)
    if isinstance(result, TicketDB):
        ticket = result
        ticket_overview_text = conv._get_ticket_overview(ticket)
        text = (
            f"{String.TICKET} {ticket_overview_text}. {String.CONFIRM_TICKET_DELETION}."
        )
        methods_tg_list.append(
            conv._build_confirm_ticket_deletion_menu(ticket.id, text)
        )
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.ticket.DELETE_CONFIRM)
async def delete_ticket_confirm(
    conv: Conversation, ticket_id_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    result = await conv._get_ticket_for_editing(ticket_id_str)
    if isinstance(result, TicketDB):
        ticket = result
        ticket_overview_text = conv._get_ticket_overview(ticket)
        text = (
            f"{String.WARNING_ICON} "  # nbsp
            f"{String.CONFIRM_DELETE_TICKET} "
            f"{ticket_overview_text}"
        )
        methods_tg_list.append(conv._build_edit_to_text_message(text))
        await conv.session.delete(ticket)
        await conv.session.flush()
        tickets, page, last_page = await conv._get_paginated_tickets(0)
        methods_tg_list.append(
            conv._build_tickets_list(
                tickets,
                page,
                last_page,
                (
                    f"{String.TRASHCAN_ICON} "  # nbsp
                    f"{String.TICKET_DELETED}: "
                    f"{ticket_overview_text}. "
                    f"{String.AVAILABLE_TICKETS_ACTIONS}."
                ),
            ),
        )
    else:
        methods_tg_list.append(conv._build_edit_to_callback_button_text())
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.ticket.CLOSE)
async def close_ticket(conv: Conversation, ticket_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    result = await conv._get_ticket_for_editing(ticket_id_str)
    if isinstance(result, TicketDB):
        ticket = result
        ticket_overview_text = (
            f"{String.CLOSE_TICKET} {conv._get_ticket_overview(ticket)}"
        )
        methods_tg_list.append(conv._build_edit_to_text_message(ticket_overview_text))
        if conv._ticket_valid_for_closing(ticket):
            ticket.is_closed = True
            text = (
                f"{String.TICKET_CLOSED}. "
                f"{String.ATTENTION_ICON} "  # nbsp
                f"{String.READONLY_MODE}. "
                f"{String.CANNOT_EDIT_CLOSED_TICKET}."
            )
            methods_tg_list.append(conv._build_ticket_view(ticket, text))
        else:
            text = (
                f"{String.TICKET_ALREADY_CLOSED}. "
                f"{String.ATTENTION_ICON} "  # nbsp
                f"{String.CANNOT_CLOSE_TICKET}. "
                f"{String.AVAILABLE_TICKET_ACTIONS}."
            )
            methods_tg_list.append(conv._build_ticket_view(ticket, text))
    else:
        methods_tg_list.append(conv._build_edit_to_callback_button_text())
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.ticket.REOPEN)
async def reopen_ticket(conv: Conversation, ticket_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    result = await conv._get_ticket_if_eligible(
        ticket_id_str,
        loader_options=[
            joinedload(TicketDB.contract),
            joinedload(TicketDB.devices).options(
                joinedload(DeviceDB.type).selectinload(DeviceTypeDB.statuses),
                joinedload(DeviceDB.status),
            ),
        ],
    )
    if isinstance(result, TicketDB):
        ticket = result
        ticket_overview_text = (
            f"{String.REOPEN_TICKET_X} {conv._get_ticket_overview(ticket)}"
        )
        methods_tg_list.append(conv._build_edit_to_text_message(ticket_overview_text))
        if ticket.is_closed:
            ticket.is_closed = False
            text = f"{String.TICKET_REOPENED}"
        else:
            text = f"{String.TICKET_ALREADY_OPENED}"
        text = f"{text}. {String.AVAILABLE_TICKET_ACTIONS}."
        methods_tg_list.append(
            conv._build_ticket_view(ticket, text),
        )
    else:
        methods_tg_list.append(conv._build_edit_to_callback_button_text())
        methods_tg_list.append(
            conv._drop_state_goto_main_menu(f"{result}. {String.PICK_A_FUNCTION}."),
        )
    return methods_tg_list


@router.route(cb.ticket.EDIT_NUMBER)
async def edit_ticket_number(conv: Conversation, ticket_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_ticket_for_editing(ticket_id_str)
    if isinstance(result, TicketDB):
        ticket = result
        conv.next_state = StateJS(
            pending_command_prefix=f"{cb.ticket.set_number(ticket.id)}"
        )
        methods_tg_list.append(
            conv._build_new_text_message(f"{String.ENTER_NEW_TICKET_NUMBER}.")
        )
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.ticket.SET_NUMBER)
async def set_ticket_number(
    conv: Conversation, ticket_id_str: str, new_ticket_number_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    result = await conv._get_ticket_for_editing(ticket_id_str)
    if isinstance(result, TicketDB):
        ticket = result
        new_ticket_number_str = new_ticket_number_str.strip().lstrip("0")
        if (
            re.fullmatch(settings.ticket_number_regex, new_ticket_number_str)
            and len(new_ticket_number_str) <= settings.ticket_number_max_length
        ):
            new_ticket_number = int(new_ticket_number_str)
            if ticket.number != new_ticket_number:
                text = String.TICKET_NUMBER_WAS_EDITED
                ticket.number = new_ticket_number
            else:
                text = String.TICKET_NUMBER_REMAINED_THE_SAME
            methods_tg_list.append(
                conv._build_ticket_view(
                    ticket=ticket,
                    text=f"{text}. {String.AVAILABLE_TICKET_ACTIONS}.",
                )
            )
        else:
            conv.next_state = StateJS(
                pending_command_prefix=cb.ticket.set_number(ticket.id)
            )
            methods_tg_list.append(
                conv._build_new_text_message(
                    f"{String.INCORRECT_TICKET_NUMBER}. "
                    f"{String.ENTER_NEW_TICKET_NUMBER}."
                )
            )
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.ticket.EDIT_CONTRACT)
async def edit_contract(conv: Conversation, ticket_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_ticket_for_editing(ticket_id_str)
    if isinstance(result, TicketDB):
        ticket = result
        conv.next_state = StateJS(
            pending_command_prefix=f"{cb.ticket.set_contract(ticket.id)}"
        )
        text = (
            String.ENTER_NEW_CONTRACT_NUMBER
            if ticket.contract_id
            else String.ENTER_CONTRACT_NUMBER
        )
        methods_tg_list.append(conv._build_new_text_message(f"{text}."))
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.ticket.SET_CONTRACT)
async def set_contract(
    conv: Conversation, ticket_id_str: str, new_contract_number_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    result = await conv._get_ticket_for_editing(ticket_id_str)
    if isinstance(result, TicketDB):
        ticket = result
        new_contract_number_str = new_contract_number_str.strip().lstrip("0")
        if (
            re.fullmatch(settings.contract_number_regex, new_contract_number_str)
            and len(new_contract_number_str) <= settings.contract_number_max_length
        ):
            new_contract_number = int(new_contract_number_str)
            existing_contract = await conv.session.scalar(
                select(ContractDB).where(ContractDB.number == new_contract_number)
            )
            old_ticket_contract = ticket.contract
            if existing_contract:
                logger.info(
                    f"{conv.log_prefix}Contract number={new_contract_number} "
                    f"was found under id={existing_contract.id}."
                )
                ticket.contract = existing_contract
            else:
                logger.info(
                    f"{conv.log_prefix}Contract number={new_contract_number} "
                    "was not found and will be added."
                )
                new_contract = ContractDB(number=new_contract_number)
                conv.session.add(new_contract)
                ticket.contract = new_contract
            if old_ticket_contract:
                if old_ticket_contract.number == new_contract_number:
                    text = f"{String.CONTRACT_NUMBER_REMAINED_THE_SAME}"
                else:
                    text = f"{String.CONTRACT_NUMBER_WAS_EDITED}"
                methods_tg_list.append(
                    conv._build_ticket_view(
                        ticket=ticket,
                        text=f"{text}. {String.AVAILABLE_TICKET_ACTIONS}.",
                    )
                )
            else:
                text = f"{String.CONTRACT_NUMBER_WAS_ADDED}"
                device_types = await conv._get_active_device_types()
                text = f"{String.CONTRACT_NUMBER_WAS_ADDED}. {String.PICK_DEVICE_TYPE}."
                methods_tg_list.append(
                    conv._build_set_device_type_menu(ticket, device_types, text)
                )
        else:
            conv.next_state = StateJS(
                pending_command_prefix=cb.ticket.set_contract(ticket.id)
            )
            text = (
                String.ENTER_NEW_CONTRACT_NUMBER
                if ticket.contract
                else String.ENTER_CONTRACT_NUMBER
            )
            methods_tg_list.append(
                conv._build_new_text_message(
                    f"{String.INCORRECT_CONTRACT_NUMBER}. {text}."
                )
            )
    else:
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.ticket.ADD_DEVICE)
async def add_device(conv: Conversation, ticket_id_str: str) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = []
    result = await conv._get_ticket_for_editing(ticket_id_str)
    if isinstance(result, TicketDB):
        ticket = result
        text = (
            f"{String.PLUS_ICON} {String.ADD_DEVICE_TO_TICKET} "  # nbsp
            f"{String.NUMBER_SYMBOL} {ticket.number}"  # nbsp
        )
        methods_tg_list.append(conv._build_edit_to_text_message(text))
        if len(ticket.devices) < settings.devices_per_ticket:
            device_types = await conv._get_active_device_types()
            methods_tg_list.append(
                conv._build_set_device_type_menu(
                    ticket, device_types, f"{String.PICK_DEVICE_TYPE}."
                )
            )
        else:
            methods_tg_list.append(
                conv._build_ticket_view(
                    ticket,
                    (
                        f"{String.LIMIT_OF_X_DEVICES_REACHED}. "
                        f"{String.AVAILABLE_TICKET_ACTIONS}."
                    ),
                ),
            )
    else:
        methods_tg_list.append(conv._build_edit_to_callback_button_text())
        methods_tg_list.append(result)
    return methods_tg_list


@router.route(cb.ticket.CREATE_DEVICE)
async def create_device(
    conv: Conversation, ticket_id_str: str, device_type_id_str: str
) -> list[MethodTG]:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    result = await conv._get_ticket_for_editing(ticket_id_str)
    if isinstance(result, TicketDB):
        ticket = result
        if len(ticket.devices) < settings.devices_per_ticket:
            device_type_id = int(device_type_id_str)
            device_type = await conv.session.get(
                DeviceTypeDB,
                device_type_id,
                options=[joinedload(DeviceTypeDB.statuses)],
            )
            if device_type and device_type.is_active and len(device_type.statuses) > 0:
                new_device = DeviceDB(
                    ticket_id=ticket.id,
                    type_id=device_type.id,
                )
                conv.session.add(new_device)
                await conv.session.flush()
                if len(new_device.type.statuses) == 1:
                    status = new_device.type.statuses[0]
                    icon = conv._get_device_status_icon(status)
                    new_device.status = status
                    if new_device.type.has_serial_number:
                        conv.next_state = StateJS(
                            pending_command_prefix=cb.device.set_serial_number(
                                new_device.id
                            )
                        )
                        methods_tg_list.append(
                            conv._build_new_text_message(
                                f"{String.ENTER_SERIAL_NUMBER}."
                            )
                        )
                    else:
                        await conv.session.refresh(
                            ticket,
                            attribute_names=[TicketDB.devices.key],
                        )
                        new_device_icon = icon
                        methods_tg_list.append(
                            conv._build_ticket_view(
                                ticket,
                                (
                                    f"{String.DEVICE_ADDED}: "
                                    f"{new_device_icon} "  # nbsp
                                    f"{String[new_device.type.name.name]}. "
                                    f"{String.AVAILABLE_TICKET_ACTIONS}."
                                ),
                            ),
                        )
                else:
                    methods_tg_list.append(
                        conv._build_set_device_status_menu(
                            new_device, f"{String.PICK_DEVICE_ACTION}."
                        ),
                    )
            else:
                if not device_type:
                    text = String.DEVICE_TYPE_NOT_FOUND
                elif not device_type.is_active:
                    text = String.DEVICE_TYPE_IS_DISABLED
                else:
                    text = String.DEVICE_TYPE_HAS_NO_ACTIONS
                device_types = await conv._get_active_device_types()
                methods_tg_list.append(
                    conv._build_set_device_type_menu(
                        ticket, device_types, f"{text}. {String.PICK_DEVICE_TYPE}."
                    )
                )
        else:
            methods_tg_list.append(
                conv._build_ticket_view(
                    ticket,
                    (
                        f"{String.LIMIT_OF_X_DEVICES_REACHED}. "
                        f"{String.AVAILABLE_TICKET_ACTIONS}."
                    ),
                ),
            )
    else:
        methods_tg_list.append(result)
    return methods_tg_list
