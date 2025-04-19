import logging
import httpx
from fastapi import APIRouter, Request, status, HTTPException

from src.core.config import settings
from src.core.logger import logger
from src.core.dispatcher import DispatcherDep
from src.tg.models import UpdateTG, SendMessageTG, ResponseTG

router = APIRouter()


# ssh -R 80:localhost:8000 nokey@localhost.run
@router.post("/", status_code=status.HTTP_200_OK)
async def handle_telegram_webhook(request: Request, dispatcher: DispatcherDep):
    logger.debug("POST '/' triggered (webhook)")
    request_result = await request.json()
    update = UpdateTG.model_validate(request_result)
    print(update.model_dump_json(exclude_none=True))
    if update.message and update.message.text:
        chat_id = update.message.chat.id
        text = update.message.text
        answer = SendMessageTG(chat_id=chat_id, text=text)
        async with httpx.AsyncClient() as client:
            response_result = await client.post(
                settings.get_tg_endpoint("sendMessage"),
                json=answer.model_dump(exclude_none=True),
            )
            response = ResponseTG.model_validate(response_result.json())
            print(response.model_dump_json(exclude_none=True))
    return
