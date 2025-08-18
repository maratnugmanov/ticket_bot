from fastapi import APIRouter, status, Request, HTTPException
import json
from pydantic import ValidationError

from src.core.config import settings
from src.core.logger import logger
from src.core.conversation import Conversation
from src.db.engine import SessionDep
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
    session_db: SessionDep,
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
    validation_errors = {}
    for model in VALIDATION_MODELS:
        try:
            update_tg = model.model_validate(request_data)
            validated_model_name = model.__name__
            logger.info(
                f"Successful validation against pydantic model {validated_model_name}."
            )
            break
        except ValidationError as e:
            validation_errors[model.__name__] = e.errors()
    if update_tg and validated_model_name:
        logger.info(
            f"{update_tg._log}Received and validated the update as {validated_model_name}."
        )
        if isinstance(update_tg, (MessageUpdateTG, CallbackQueryUpdateTG)):
            conversation = await Conversation.create(update_tg, session_db)
            if conversation:
                success = await conversation.process()
                if success:
                    logger.info(
                        f"{update_tg._log}Successfully processed "
                        "the update via conversation."
                    )
                else:
                    await session_db.rollback()
                    logger.error(
                        f"{update_tg._log}Failed processing "
                        "the update via conversation."
                    )
            else:
                logger.info(
                    f"{update_tg._log}Conversation was not initiated "
                    f"for the update (type: {validated_model_name}). "
                    "This is expected for certain conditions "
                    "(e.g. guest, bot)."
                )
        elif isinstance(update_tg, UpdateTG):
            logger.info(
                f"{update_tg._log}Received a generic UpdateTG, which is "
                "not a MessageUpdateTG or CallbackQueryUpdateTG. "
                "No conversation action taken."
            )
    else:
        logger.error(
            "Failed to validate incoming webhook data against any known model."
        )
        for model_name, errors in validation_errors.items():
            logger.debug(f"Validation against {model_name} failed: {errors}")
        logger.debug(f"Raw JSON: {request_data}")
    return None
