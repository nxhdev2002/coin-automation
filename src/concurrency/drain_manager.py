import asyncio


class DrainManager:
    """Tracks in-flight /fulfill orders so a deploy can wait for them before killing the process."""

    def __init__(self):
        self._active: set[str] = set()
        self._draining = False

    @property
    def draining(self) -> bool:
        return self._draining

    def begin(self, order_id: str) -> None:
        self._active.add(order_id)

    def end(self, order_id: str) -> None:
        self._active.discard(order_id)

    def start_draining(self) -> None:
        self._draining = True

    async def wait_drained(self, timeout_seconds: float) -> bool:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_seconds
        while self._active and loop.time() < deadline:
            await asyncio.sleep(1)
        return not self._active

    @property
    def active_orders(self) -> list[str]:
        return list(self._active)


_drain_manager: DrainManager | None = None


def get_drain_manager() -> DrainManager:
    global _drain_manager
    if _drain_manager is None:
        _drain_manager = DrainManager()
    return _drain_manager
