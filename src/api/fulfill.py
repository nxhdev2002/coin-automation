import asyncio

from fastapi import APIRouter
from loguru import logger

from ..callback.core_client import CoreClient
from ..concurrency.drain_manager import get_drain_manager
from ..config import get_settings
from ..fulfill_processor import process_order
from ..logging_context import order_log_context, set_order_status
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
    """Tag every log this order produces before doing any work, so ELK can filter
    a whole fulfillment (including deep automation logs) by order or buyer."""
    with order_log_context(
        order_id=request.order_id,
        buyer_id=request.user_id,
        buyer_user_name=request.user_name,
        tiktok_username=request.tiktok_username,
        order_status="Received",
    ):
        await _fulfill_and_report(request)


async def _fulfill_and_report(request: FulfillRequest):
    drain_mgr = get_drain_manager()
    try:
        result = await process_order(request, get_core_client())

        logger.info(
            f"Order {request.order_id} finished: success={result.success} "
            f"category={result.failure_category or '-'} reason={result.failure_reason or '-'}"
        )

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
        set_order_status("Error")
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
    finally:
        drain_mgr.end(request.order_id)


@router.post("/fulfill", response_model=FulfillResult)
async def fulfill_order(request: FulfillRequest):
    with order_log_context(
        order_id=request.order_id,
        buyer_id=request.user_id,
        buyer_user_name=request.user_name,
        tiktok_username=request.tiktok_username,
        order_status="Received",
    ):
        drain_mgr = get_drain_manager()
        if drain_mgr.draining:
            set_order_status("Rejected")
            logger.warning(f"Rejecting order {request.order_id}: service is draining for deploy")
            return FulfillResult(
                success=False,
                failure_category="ServiceDraining",
                failure_reason="Service is draining for a deploy, retry shortly",
            )

        logger.info(f"Received fulfill request for order {request.order_id}")
        drain_mgr.begin(request.order_id)
        asyncio.create_task(_run_fulfillment_background(request))
        return FulfillResult(
            success=True,
            fulfillment_phase="LaunchingBrowser",
        )
