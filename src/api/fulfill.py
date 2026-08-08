from fastapi import APIRouter
from loguru import logger

from ..callback.core_client import CoreClient
from ..config import get_settings
from ..concurrency.lock_manager import get_lock_manager
from ..fulfill_processor import process_order
from ..models.fulfill import FulfillRequest, FulfillResult

router = APIRouter()

_core_client: CoreClient | None = None


def get_core_client() -> CoreClient:
    global _core_client
    if _core_client is None:
        settings = get_settings()
        _core_client = CoreClient(settings.core_api_url, settings.core_api_key)
    return _core_client


@router.post("/fulfill", response_model=FulfillResult)
async def fulfill_order(request: FulfillRequest):
    logger.info(f"Received fulfill request for order {request.order_id}")
    result = await process_order(request, get_core_client())

    core_client = get_core_client()
    await core_client.update_order(request.order_id, {
        "fulfillmentPhase": "Done",
        "success": result.success,
        "failureReason": result.failure_reason,
        "failureCategory": result.failure_category,
        "screenshotPath": result.screenshot_path,
        "captchaEncountered": result.captcha_encountered,
        "captchaSolved": result.captcha_solved,
        "captchaCostUsd": result.captcha_cost_usd,
    })

    return result
