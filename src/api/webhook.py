import httpx
from fastapi import APIRouter, Request, status

from src.core.config import settings
from src.core.logger import logger
from src.core.dispatcher import DispatcherDep, SessionDep
from src.tg.models import UpdateTG, ResponseTG

router = APIRouter()


@router.post("/", status_code=status.HTTP_200_OK)
async def handle_telegram_webhook(
    request: Request, dispatcher: DispatcherDep, session: SessionDep
):
    logger.debug("Webhook triggered.")
    request_result = await request.json()
    update = UpdateTG.model_validate(request_result)
    logger.debug(
        f"Received update: {UpdateTG.__name__}.model_dump_json(): {update.model_dump_json(exclude_none=True)}"
    )
    dispatcher_response = await dispatcher.process(update, session)
    if dispatcher_response:
        async with httpx.AsyncClient() as client:
            response_result: httpx.Response = await client.post(**dispatcher_response)
            response_result.raise_for_status()
        response = ResponseTG.model_validate(response_result.json())
        if response and response.ok is True:
            logger.debug(
                f"Successful reply: {ResponseTG.__name__}.model_dump_json(): {response.model_dump_json(exclude_none=True)}"
            )
        else:
            logger.debug(
                f"Reply failed: {ResponseTG.__name__}.model_dump_json(): {response.model_dump_json(exclude_none=True)}"
            )
    return None
