import httpx
from fastapi import APIRouter, status

from src.core.config import settings
from src.core.logger import logger
from src.core.conversation import Conversation
from src.db.engine import SessionDepDB
from src.tg.models import UpdateTG, MessageUpdateTG, CallbackQueryUpdateTG, ResponseTG

router = APIRouter()


@router.post("/", status_code=status.HTTP_200_OK)
async def handle_telegram_webhook(
    update_tg: MessageUpdateTG | CallbackQueryUpdateTG | UpdateTG,
    session_db: SessionDepDB,
):
    logger.debug(
        f"Received update: {type(update_tg).__name__}.model_dump_json(exclude_none=True): "
        f"{update_tg.model_dump_json(exclude_none=True)}"
    )
    if isinstance(update_tg, (MessageUpdateTG, CallbackQueryUpdateTG)):
        conversation = await Conversation.create(update_tg, session_db)
        if conversation:
            success = await conversation.process()
            if success:
                logger.debug(f"Success in processing of Update #{update_tg.update_id}.")
            else:
                logger.error(f"Failed in processing of Update #{update_tg.update_id}.")
    elif isinstance(update_tg, UpdateTG):
        logger.debug(
            "No valid conversation created for Update #"
            f"{update_tg.update_id}. No action taken."
        )
    else:
        logger.debug("Couldn't process the Update.")
    return None
