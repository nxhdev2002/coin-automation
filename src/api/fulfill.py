import asyncio

from fastapi import APIRouter
from loguru import logger

from ..callback.core_client import CoreClient
from ..config import get_settings
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


async def _run_fulfillment_background(request: FulfillRequest):
    try:
        result = await process_order(request, get_core_client())

        await get_core_client().update_order(request.order_id, {
            "fulfillmentPhase": "Done",
            "success": result.success,
            "failureReason": result.failure_reason,
            "failureCategory": result.failure_category,
            "screenshotPath": result.screenshot_path,
            "captchaEncountered": result.captcha_encountered,
            "captchaSolved": result.captcha_solved,
            "captchaCostUsd": result.captcha_cost_usd,
        })
    except Exception as e:
        import traceback
        logger.error(f"Background fulfillment error for order {request.order_id}: {e}")
        logger.error(traceback.format_exc())
        try:
            await get_core_client().update_order(request.order_id, {
                "fulfillmentPhase": "Done",
                "success": False,
                "failureReason": str(e)[:500],
                "failureCategory": "Unknown",
            })
        except Exception:
            pass


@router.post("/fulfill", response_model=FulfillResult)
async def fulfill_order(request: FulfillRequest):
    logger.info(f"Received fulfill request for order {request.order_id}")
    asyncio.create_task(_run_fulfillment_background(request))
    return FulfillResult(
        success=True,
        fulfillment_phase="LaunchingBrowser",
    )
