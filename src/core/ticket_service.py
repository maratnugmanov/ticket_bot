from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from src.core.config import settings
from src.core.logger import logger
from src.core.decorators import require_ticket_context, require_writeoff_context
from src.core.enums import (
    RoleName,
    DeviceTypeName,
    CallbackData,
    String,
    Action,
    Script,
)
from src.core.models import DeviceJS, DeviceTypeJS, StateJS
from src.tg.models import (
    UpdateTG,
    MessageUpdateTG,
    CallbackQueryUpdateTG,
    MessageTG,
    CallbackQueryTG,
    UserTG,
    SendMessageTG,
    InlineKeyboardMarkupTG,
    InlineKeyboardButtonTG,
    SuccessTG,
    ErrorTG,
    MethodTG,
    DeleteMessagesTG,
    EditMessageTextTG,
)
from src.db.engine import SessionDep
from src.db.models import (
    RoleDB,
    UserDB,
    ContractDB,
    TicketDB,
    WriteoffDeviceDB,
    DeviceDB,
    DeviceTypeDB,
)


class TicketService:
    def __init__(self, session: SessionDep, log_prefix: str = ""):
        self.session = session
        self.log_prefix = log_prefix

    async def create_ticket(self, user_id: int, ticket_number: int) -> TicketDB:
        """
        Creates a new ticket for a user.
        This is the first step in the ticket creation process.
        """
        new_ticket = TicketDB(
            number=ticket_number,
            user_id=user_id,
        )
        self.session.add(new_ticket)
        await self.session.flush()
        return new_ticket

    async def set_contract_for_ticket(
        self, ticket: TicketDB, contract_number: int
    ) -> TicketDB:
        """
        Finds or creates a contract and associates it with a ticket.
        """
        # Business logic for finding/creating a contract is now isolated here
        existing_contract = await self.session.scalar(
            select(ContractDB).where(ContractDB.number == contract_number)
        )
        if existing_contract:
            logger.info(
                f"{self.log_prefix}Contract number={contract_number} "
                "was found in the database under "
                f"id={existing_contract.id}."
            )
            ticket.contract = existing_contract
        else:
            logger.info(
                f"{self.log_prefix}Contract number={contract_number} "
                "was not found in the database and will be added."
            )
            new_contract = ContractDB(number=contract_number)
            self.session.add(new_contract)
            ticket.contract = new_contract
        await self.session.flush()
        return ticket
