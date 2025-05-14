from fastapi import APIRouter, status, Request, HTTPException
import json
from pydantic import ValidationError

from src.core.config import settings
from src.core.logger import logger
from src.core.conversation import Conversation
from src.db.engine import SessionDepDB
from src.tg.models import UpdateTG, MessageUpdateTG, CallbackQueryUpdateTG

router = APIRouter()

VALIDATION_MODELS: list[type[UpdateTG]] = [
    MessageUpdateTG,
    CallbackQueryUpdateTG,
    UpdateTG,
]


@router.post("/", status_code=status.HTTP_200_OK)
async def handle_telegram_webhook(
    request: Request,
    session_db: SessionDepDB,
):
    try:
        request_data = await request.json()
    except json.JSONDecodeError:
        logger.error("Invalid JSON format received in webhook.", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON format"
        )
    update_tg: UpdateTG | None = None
    validated_model_name: str | None = None
    for model in VALIDATION_MODELS:
        try:
            update_tg = model.model_validate(request_data)
            validated_model_name = model.__name__
            logger.debug(
                f"Successful validation against pydantic model {validated_model_name}."
            )
            break
        except ValidationError as e:
            logger.debug(
                f"Failed validation against pydantic model {model.__name__}: {e.errors()}."
            )
    if update_tg and validated_model_name:
        logger.debug(
            f"Received and validated update {update_tg.update_id} as "
            f"{validated_model_name}.\nRaw JSON: {request_data}"
        )
        if isinstance(update_tg, (MessageUpdateTG, CallbackQueryUpdateTG)):
            conversation = await Conversation.create(update_tg, session_db)
            if conversation:
                success = await conversation.process()
                if success:
                    logger.debug(
                        f"Successfully processed Update #{update_tg.update_id} via Conversation."
                    )
                else:
                    logger.error(
                        f"Conversation processing failed for Update #{update_tg.update_id}."
                    )
            else:
                logger.debug(
                    f"Conversation not initiated for Update #{update_tg.update_id} (type: {validated_model_name}). "
                    "This is expected for certain conditions (e.g. guest, bot)."
                )
        elif isinstance(update_tg, UpdateTG):
            logger.debug(
                f"Received a generic UpdateTG (Update ID: {update_tg.update_id}), "
                "which is not a MessageUpdateTG or CallbackQueryUpdateTG. No conversation action taken."
            )
    else:
        logger.error(
            "Failed to validate incoming webhook data against any known model. "
            f"Request data: {request_data}"
        )
    return None
