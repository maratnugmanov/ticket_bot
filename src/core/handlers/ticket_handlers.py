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
from src.db.models import ContractDB, TicketDB, DeviceDB, DeviceTypeDB

if TYPE_CHECKING:
    from src.core.conversation import Conversation


@router.route(cb.ticket.LIST)
async def list_tickets(conversation: Conversation, page_str: str = "0") -> list:
    """Handles the command to list tickets."""
    page = int(page_str)
    return [
        conversation._build_edit_to_text_message(f"{String.TICKETS} >>"),
        await conversation._build_tickets_list(page, f"{String.PICK_TICKETS_ACTION}."),
    ]


@router.route(cb.ticket.VIEW)
async def view_ticket(conv: Conversation, ticket_id_str: str) -> list:
    methods_tg_list: list[MethodTG] = [
        conv._build_edit_to_callback_button_text(suffix_text=">>")
    ]
    ticket_id = int(ticket_id_str)
    result = await conv._get_ticket_if_eligible(
        ticket_id,
        loader_options=[
            selectinload(TicketDB.contract),
            selectinload(TicketDB.devices).selectinload(DeviceDB.type),
        ],
    )
    if isinstance(result, TicketDB):
        ticket = result
        methods_tg_list.append(
            conv._build_ticket_view(ticket, f"{String.PICK_TICKET_ACTION}."),
        )
    else:
        methods_tg_list.append(
            conv._drop_state_goto_main_menu(f"{result}. {String.PICK_A_FUNCTION}."),
        )
    return methods_tg_list


@router.route(cb.ticket.EDIT_NUMBER)
async def edit_ticket_number(conv: Conversation, ticket_id_str: str) -> list:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    ticket_id = int(ticket_id_str)
    result = await conv._get_ticket_if_eligible(ticket_id)
    if isinstance(result, TicketDB):
        ticket = result
        conv.next_state = StateJS(
            pending_command_prefix=f"{cb.ticket.set_number(ticket.id)}"
        )
        methods_tg_list.append(
            conv._build_new_text_message(f"{String.ENTER_NEW_TICKET_NUMBER}.")
        )
    else:
        methods_tg_list.append(
            conv._drop_state_goto_main_menu(f"{result}. {String.PICK_A_FUNCTION}."),
        )
    return methods_tg_list


@router.route(cb.ticket.SET_NUMBER)
async def set_ticket_number(
    conv: Conversation, ticket_id_str: str, new_ticket_number_str: str
) -> list:
    ticket_id = int(ticket_id_str)
    new_ticket_number_str = new_ticket_number_str.strip().lstrip("0")
    if (
        re.fullmatch(settings.ticket_number_regex, new_ticket_number_str)
        and len(new_ticket_number_str) <= settings.ticket_number_max_length
    ):
        result = await conv._get_ticket_if_eligible(
            ticket_id,
            loader_options=[
                selectinload(TicketDB.contract),
                selectinload(TicketDB.devices).selectinload(DeviceDB.type),
            ],
        )
        if isinstance(result, TicketDB):
            ticket = result
            new_ticket_number = int(new_ticket_number_str)
            if ticket.number == new_ticket_number:
                status_text = String.TICKET_NUMBER_REMAINS_THE_SAME
            else:
                ticket.number = new_ticket_number
                status_text = String.TICKET_NUMBER_WAS_EDITED
            conv.next_state = None
            methods_tg_list = [
                conv._build_ticket_view(
                    ticket=ticket,
                    text=f"{status_text}. {String.PICK_TICKET_ACTION}.",
                )
            ]
        else:
            methods_tg_list = [
                conv._drop_state_goto_main_menu(f"{result}. {String.PICK_A_FUNCTION}.")
            ]
    else:
        conv.next_state = StateJS(
            pending_command_prefix=cb.ticket.set_number(ticket_id)
        )
        methods_tg_list = [
            conv._build_new_text_message(
                f"{String.INCORRECT_TICKET_NUMBER}. {String.ENTER_NEW_TICKET_NUMBER}."
            )
        ]
    return methods_tg_list


@router.route(cb.ticket.EDIT_CONTRACT)
async def edit_contract_number(conv: Conversation, ticket_id_str: str) -> list:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    ticket_id = int(ticket_id_str)
    result = await conv._get_ticket_if_eligible(ticket_id)
    if isinstance(result, TicketDB):
        ticket = result
        conv.next_state = StateJS(
            pending_command_prefix=f"{cb.ticket.set_contract(ticket.id)}"
        )
        methods_tg_list.append(
            conv._build_new_text_message(f"{String.ENTER_NEW_CONTRACT_NUMBER}.")
        )
    else:
        methods_tg_list.append(
            conv._drop_state_goto_main_menu(f"{result}. {String.PICK_A_FUNCTION}."),
        )
    return methods_tg_list


@router.route(cb.ticket.SET_CONTRACT)
async def set_contract_number(
    conv: Conversation, ticket_id_str: str, new_contract_number_str: str
) -> list:
    ticket_id = int(ticket_id_str)
    new_contract_number_str = new_contract_number_str.strip().lstrip("0")
    if (
        re.fullmatch(settings.contract_number_regex, new_contract_number_str)
        and len(new_contract_number_str) <= settings.contract_number_max_length
    ):
        result = await conv._get_ticket_if_eligible(
            ticket_id,
            loader_options=[
                selectinload(TicketDB.contract),
                selectinload(TicketDB.devices).selectinload(DeviceDB.type),
            ],
        )
        if isinstance(result, TicketDB):
            ticket = result
            new_contract_number = int(new_contract_number_str)
            if ticket.contract and ticket.contract.number == new_contract_number:
                status_text = String.CONTRACT_NUMBER_REMAINS_THE_SAME
            else:
                existing_contract = await conv.session.scalar(
                    select(ContractDB).where(ContractDB.number == new_contract_number)
                )
                if existing_contract:
                    logger.info(
                        f"{conv.log_prefix}Contract number={new_contract_number} "
                        f"was found in the database under id={existing_contract.id}."
                    )
                    ticket.contract = existing_contract
                else:
                    logger.info(
                        f"{conv.log_prefix}Contract number={new_contract_number} "
                        "was not found in the database and will be added."
                    )
                    new_contract = ContractDB(number=new_contract_number)
                    conv.session.add(new_contract)
                    ticket.contract = new_contract
                status_text = String.CONTRACT_NUMBER_WAS_EDITED
            conv.next_state = None
            methods_tg_list = [
                conv._build_ticket_view(
                    ticket=ticket,
                    text=f"{status_text}. {String.PICK_TICKET_ACTION}.",
                )
            ]
        else:
            methods_tg_list = [
                conv._drop_state_goto_main_menu(f"{result}. {String.PICK_A_FUNCTION}.")
            ]
    else:
        conv.next_state = StateJS(
            pending_command_prefix=cb.ticket.set_contract(ticket_id)
        )
        methods_tg_list = [
            conv._build_new_text_message(
                f"{String.INCORRECT_CONTRACT_NUMBER}. {String.ENTER_NEW_CONTRACT_NUMBER}."
            )
        ]
    return methods_tg_list


@router.route(cb.ticket.ADD_DEVICE)
async def add_device(conv: Conversation, ticket_id_str: str) -> list:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    ticket_id = int(ticket_id_str)
    result = await conv._get_ticket_if_eligible(
        ticket_id,
        loader_options=[
            selectinload(TicketDB.contract),
            selectinload(TicketDB.devices).selectinload(DeviceDB.type),
        ],
    )
    if isinstance(result, TicketDB):
        ticket = result
        if len(ticket.devices) < settings.devices_per_ticket:
            device_types_result = await conv.session.scalars(
                select(DeviceTypeDB).where(DeviceTypeDB.is_active == True)  # noqa: E712
            )
            device_types = list(device_types_result)
            methods_tg_list.append(
                conv._build_pick_device_type(
                    ticket, device_types, f"{String.PICK_DEVICE_TYPE}."
                )
            )
        else:
            methods_tg_list.append(
                conv._build_ticket_view(
                    ticket,
                    (
                        f"{String.THE_LIMIT_OF} "
                        f"{settings.devices_per_ticket} "
                        f"{String.DEVICES_REACHED}. "
                        f"{String.PICK_TICKET_ACTION}."
                    ),
                ),
            )
    else:
        methods_tg_list = [
            conv._drop_state_goto_main_menu(f"{result}. {String.PICK_A_FUNCTION}.")
        ]
    return methods_tg_list


@router.route(cb.ticket.CREATE_DEVICE)
async def create_device(
    conv: Conversation, ticket_id_str: str, device_type_id_str: str
) -> list:
    methods_tg_list: list[MethodTG] = [conv._build_edit_to_callback_button_text()]
    ticket_id = int(ticket_id_str)
    result = await conv._get_ticket_if_eligible(
        ticket_id,
        loader_options=[
            selectinload(TicketDB.contract),
            selectinload(TicketDB.devices).selectinload(DeviceDB.type),
        ],
    )
    if isinstance(result, TicketDB):
        ticket = result
        if len(ticket.devices) < settings.devices_per_ticket:
            device_type_id = int(device_type_id_str)
            device_type = await conv.session.get(DeviceTypeDB, device_type_id)
            if device_type and device_type.is_active:
                new_device = DeviceDB(
                    ticket_id=ticket.id,
                    type_id=device_type.id,
                )
                conv.session.add(new_device)
                await conv.session.flush()
                if device_type.is_disposable:
                    new_device.removal = False
                    if device_type.has_serial_number:
                        conv.next_state = StateJS(
                            pending_command_prefix=cb.device.add_serial_number(
                                new_device.id
                            )
                        )
                        methods_tg_list.append(
                            conv._build_new_text_message(
                                f"{String.ENTER_SERIAL_NUMBER}."
                            )
                        )
                    else:
                        methods_tg_list.append(
                            conv._build_ticket_view(
                                ticket,
                                (
                                    f"{String.DEVICE_ADDED}: "
                                    f"{String[new_device.type.name.name]}. "
                                    f"{String.PICK_TICKET_ACTION}."
                                ),
                            ),
                        )
                else:
                    conv.next_state = StateJS(
                        pending_command_prefix=cb.device.edit_action(new_device.id)
                    )
                    methods_tg_list.append(
                        conv._build_device_action_menu(
                            new_device.id, f"{String.PICK_INSTALL_OR_RETURN}."
                        ),
                    )
            else:
                status_text = (
                    String.DEVICE_TYPE_NOT_FOUND
                    if not device_type
                    else String.DEVICE_TYPE_IS_DISABLED
                )
                device_types_result = await conv.session.scalars(
                    select(DeviceTypeDB).where(DeviceTypeDB.is_active == True)  # noqa: E712
                )
                device_types = list(device_types_result)
                methods_tg_list.append(
                    conv._build_pick_device_type(
                        ticket,
                        device_types,
                        f"{status_text}. {String.PICK_DEVICE_TYPE}.",
                    )
                )
        else:
            methods_tg_list.append(
                conv._build_ticket_view(
                    ticket,
                    (
                        f"{String.THE_LIMIT_OF} "
                        f"{settings.devices_per_ticket} "
                        f"{String.DEVICES_REACHED}. "
                        f"{String.PICK_TICKET_ACTION}."
                    ),
                ),
            )
    else:
        methods_tg_list = [
            conv._drop_state_goto_main_menu(f"{result}. {String.PICK_A_FUNCTION}.")
        ]
    return methods_tg_list
