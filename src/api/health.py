import os

from fastapi import APIRouter

from ..concurrency.drain_manager import get_drain_manager
from ..config import get_settings

router = APIRouter()


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "commit": os.getenv("GIT_COMMIT", "unknown"),
        "version": os.getenv("VERSION", "unknown"),
    }


@router.post("/drain")
async def drain(timeout_seconds: float | None = None):
    """Stop accepting new /fulfill orders and wait for in-flight ones to finish.

    Called by the deploy pipeline before it kills this process — the deploy
    script only proceeds to kill once this reports drained=true, so an order
    mid-payment gets a chance to actually finish instead of being cut off with
    no result recorded. Defaults to the order-timeout ceiling plus a margin:
    a real order can legitimately run that long (QR/3DS waits included), so
    the default has to cover it — anything still active after that is stuck
    past its own ceiling, not just running long.
    """
    settings = get_settings()
    if timeout_seconds is None:
        timeout_seconds = settings.order_timeout_minutes * 60 + 120
    mgr = get_drain_manager()
    mgr.start_draining()
    drained = await mgr.wait_drained(timeout_seconds)
    return {"drained": drained, "active_orders": mgr.active_orders}


@router.post("/drain/resume")
async def drain_resume():
    """Undo a /drain call — used by the deploy pipeline when it gives up
    waiting and aborts without killing this instance, so it goes back to
    accepting orders instead of being stuck rejecting everything."""
    mgr = get_drain_manager()
    mgr.stop_draining()
    return {"draining": mgr.draining}
