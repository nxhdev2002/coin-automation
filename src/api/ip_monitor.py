from fastapi import APIRouter

from ..models.ip import IpState
from ..monitor.ip_watcher import get_ip_watcher

router = APIRouter(tags=["ip"])


@router.get("/ip", response_model=IpState)
async def current_ip():
    """Public IP this VPS is going out with, as last seen by the monitor.

    Falls back to a live lookup if the poller hasn't produced a value yet
    (fresh boot, or monitoring disabled).
    """
    watcher = get_ip_watcher()
    if not watcher.has_checked:
        return await watcher.check()
    return watcher.state()


@router.post("/ip/check", response_model=IpState)
async def check_ip():
    """Force a check right now — alerts on Telegram if the IP moved."""
    return await get_ip_watcher().check()
