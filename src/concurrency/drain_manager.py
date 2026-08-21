import asyncio
from collections import OrderedDict

FULFILLED_MEMORY_SIZE = 500


class DrainManager:
    """Tracks in-flight /fulfill orders so a deploy can wait for them before
    killing the process, and guards against the same order being run twice."""

    def __init__(self):
        self._active: set[str] = set()
        self._draining = False
        # order_ids that finished successfully — a retry of one of these would
        # buy the coins a second time. Bounded; failed orders are NOT recorded,
        # so the core can legitimately retry them.
        self._fulfilled: OrderedDict[str, None] = OrderedDict()

    @property
    def draining(self) -> bool:
        return self._draining

    def begin(self, order_id: str) -> None:
        self._active.add(order_id)

    def end(self, order_id: str) -> None:
        self._active.discard(order_id)

    def is_active(self, order_id: str) -> bool:
        return order_id in self._active

    def mark_fulfilled(self, order_id: str) -> None:
        self._fulfilled[order_id] = None
        while len(self._fulfilled) > FULFILLED_MEMORY_SIZE:
            self._fulfilled.popitem(last=False)

    def was_fulfilled(self, order_id: str) -> bool:
        return order_id in self._fulfilled

    def start_draining(self) -> None:
        self._draining = True

    def stop_draining(self) -> None:
        """Undo start_draining — used when a deploy gives up waiting and aborts
        without killing this instance, so it goes back to accepting orders
        instead of being stuck rejecting everything until someone restarts it."""
        self._draining = False

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
