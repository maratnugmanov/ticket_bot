import httpx
from fastapi import APIRouter, Request, status

from src.core.config import settings
from src.core.logger import logger
from src.core.dispatcher import Dispatcher
from src.db.engine import SessionDepDB
from src.tg.models import MethodTG, UpdateTG, ResponseTG

router = APIRouter()


@router.post("/", status_code=status.HTTP_200_OK)
async def handle_telegram_webhook(
    request: Request,
    session_db: SessionDepDB,
):
    logger.debug("Webhook triggered.")
    request_result = await request.json()
    update_tg = UpdateTG.model_validate(request_result)
    logger.debug(
        f"Received update: {UpdateTG.__name__}.model_dump_json(): {update_tg.model_dump_json(exclude_none=True)}"
    )
    dispatcher_methods_tg_list: list[MethodTG] | None = await Dispatcher(
        update_tg, session_db
    ).process()
    if dispatcher_methods_tg_list:
        async with httpx.AsyncClient() as client:
            logger.debug("Response iteration started.")
            for method_tg in dispatcher_methods_tg_list:
                response_result: httpx.Response = await client.post(
                    url=settings.get_tg_endpoint(method_tg._url),
                    json=method_tg.model_dump(exclude_none=True),
                )
                # response_result.raise_for_status()
                print(response_result.json())
            response = ResponseTG.model_validate(response_result.json())
            if response and response.ok:
                logger.debug(
                    f"Successful reply: {ResponseTG.__name__}.model_dump_json(): {response.model_dump_json(exclude_none=True)}"
                )
            else:
                logger.warning(
                    f"Reply failed: {ResponseTG.__name__}.model_dump_json(): {response.model_dump_json(exclude_none=True)}"
                )
            logger.debug("Response iteration ended.")
    return None
