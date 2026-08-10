import os

from fastapi import APIRouter

from ..concurrency.drain_manager import get_drain_manager

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "commit": os.getenv("GIT_COMMIT", "unknown")}


@router.post("/drain")
async def drain(timeout_seconds: float = 90.0):
    """Stop accepting new /fulfill orders and wait for in-flight ones to finish.

    Called by the deploy pipeline right before it kills this process, so an
    order mid-payment isn't cut off with no result recorded.
    """
    mgr = get_drain_manager()
    mgr.start_draining()
    drained = await mgr.wait_drained(timeout_seconds)
    return {"drained": drained, "active_orders": mgr.active_orders}
