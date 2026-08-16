import asyncio
import uuid
from pathlib import Path

from loguru import logger

from .browser import launch_browser, close_browser


class WarmBrowserPool:
    """Pre-launches Chrome instances against fresh, empty profile dirs so a new-account
    QR login can skip Chrome's cold-start latency.

    Only valid for brand-new accounts: every entry has an empty profile with no TikTok
    session, so it can never be handed out for a re-login/top-up on an existing profile
    — those must launch against their own specific user-data-dir to keep its cookies.
    """

    def __init__(self, pool_dir: str, size: int, sadcaptcha_api_key: str = ""):
        self._pool_dir = Path(pool_dir)
        self._size = size
        self._sadcaptcha_api_key = sadcaptcha_api_key
        self._queue: asyncio.Queue = asyncio.Queue()
        self._pending = 0
        self._tasks: set[asyncio.Task] = set()
        self._stopped = False

    def start(self) -> None:
        self._pool_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_capacity()

    def _ensure_capacity(self) -> None:
        if self._stopped:
            return
        have = self._queue.qsize() + self._pending
        for _ in range(self._size - have):
            self._pending += 1
            task = asyncio.create_task(self._fill_one())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _fill_one(self) -> None:
        profile = str(self._pool_dir / uuid.uuid4().hex)
        try:
            browser = await launch_browser(profile, sadcaptcha_api_key=self._sadcaptcha_api_key)
            await self._queue.put((browser, profile))
            logger.info(f"[WarmPool] Pre-launched browser ready (profile={profile})")
        except Exception as e:
            logger.warning(f"[WarmPool] Pre-launch failed: {e}")
        finally:
            self._pending -= 1

    async def acquire(self):
        """Returns (browser, profile_path) instantly if a warm instance is ready,
        otherwise None — caller should cold-launch instead."""
        self._ensure_capacity()
        try:
            browser, profile = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
        self._ensure_capacity()
        return browser, profile

    async def close_all(self) -> int:
        self._stopped = True
        for task in list(self._tasks):
            task.cancel()
        closed = 0
        while not self._queue.empty():
            browser, _ = self._queue.get_nowait()
            try:
                await close_browser(browser)
                closed += 1
            except Exception:
                pass
        return closed


_pool: WarmBrowserPool | None = None


def get_warm_pool(settings) -> WarmBrowserPool:
    global _pool
    if _pool is None:
        _pool = WarmBrowserPool(
            pool_dir=str(Path(settings.profile_dir) / "_warm_pool"),
            size=settings.warm_pool_size,
            sadcaptcha_api_key=settings.sadcaptcha_api_key,
        )
        _pool.start()
    return _pool
