import asyncio
import shutil
from pathlib import Path

from loguru import logger

from ..concurrency.lock_manager import get_lock_manager
from ..config import get_settings

# Chrome subpaths that only ever hold disposable, regenerable cache — deleting
# them frees the bulk of a profile's on-disk size without touching the actual
# login session (Cookies, Local Storage, IndexedDB live elsewhere and are
# never listed here).
CACHE_SUBPATHS = (
    "Default/Cache",
    "Default/Code Cache",
    "Default/GPUCache",
    "Default/blob_storage",
    "Default/Service Worker/CacheStorage",
    "ShaderCache",
    "GrShaderCache",
    "component_crx_cache",
)


def _dir_size(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                pass
    return total


def clean_profile_cache(profile_path: str) -> int:
    """Delete Chrome's own disposable cache dirs under one profile.

    Runs as plain blocking I/O (caller is expected to offload it, e.g. via
    `asyncio.to_thread`). A subpath that fails to delete (e.g. a file still
    open in a Chrome process) is skipped rather than raised — Windows already
    refuses to remove files with an open handle, so this never corrupts a
    profile that's unexpectedly still in use; it just leaves that piece for
    the next sweep. Returns bytes freed.
    """
    freed = 0
    base = Path(profile_path)
    for sub in CACHE_SUBPATHS:
        target = base / sub
        if not target.exists():
            continue
        try:
            freed += _dir_size(target)
            shutil.rmtree(target, ignore_errors=True)
        except OSError as e:
            logger.warning(f"Could not clean {target}: {e}")
    return freed


class ProfileCacheCleaner:
    """Periodically strips Chrome's disposable cache dirs out of every stored
    TikTok profile — Cache/Code Cache/GPUCache routinely account for 80-95% of
    a profile's on-disk size but hold nothing needed to stay logged in.

    Skips any profile currently locked (an active fulfillment run or a debug
    spawn session) via `LockManager`, checked under both key schemes profiles
    can be locked by (see `fulfill_processor.process_order`): the profile path
    itself for an existing account, or `link:{order_id}` for a brand-new
    account being added — whose profile directory is named after that same
    order_id. `_warm_pool` is skipped entirely: those directories can be a
    browser still sitting live in the warm pool queue, which has no lock_manager
    entry at all to check against.
    """

    def __init__(self, profile_dir: str, interval_minutes: int):
        self._profile_dir = profile_dir
        self._interval_minutes = interval_minutes
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return self._interval_minutes > 0

    def start(self) -> None:
        if not self.enabled:
            logger.info("Profile cache cleaner disabled (interval <= 0)")
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_forever())
        logger.info(f"Profile cache cleaner started, sweeping every {self._interval_minutes} minute(s)")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("Profile cache cleaner stopped")

    async def _run_forever(self) -> None:
        delay = self._interval_minutes * 60
        while True:
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Profile cache sweep errored: {type(e).__name__}: {e}")
            await asyncio.sleep(delay)

    async def sweep(self) -> tuple[int, int]:
        """Run one cleanup pass over `profile_dir`. Returns (profiles_cleaned, bytes_freed)."""
        base = Path(self._profile_dir)
        if not base.is_dir():
            return 0, 0

        lock_mgr = get_lock_manager()
        cleaned = 0
        freed = 0
        for entry in base.iterdir():
            if not entry.is_dir() or entry.name == "_warm_pool":
                continue
            profile_path = str(entry)
            if lock_mgr.is_locked(profile_path) or lock_mgr.is_locked(f"link:{entry.name}"):
                continue
            try:
                # Blocking I/O — offload so one slow disk doesn't stall order
                # processing (this shares the event loop with live fulfillments).
                profile_freed = await asyncio.to_thread(clean_profile_cache, profile_path)
            except Exception as e:
                logger.warning(f"Cache cleanup failed for {profile_path}: {type(e).__name__}: {e}")
                continue
            if profile_freed:
                cleaned += 1
                freed += profile_freed

        if cleaned:
            logger.info(f"Profile cache sweep: cleaned {cleaned} profile(s), freed {freed / (1024 * 1024):.1f} MB")
        else:
            logger.debug("Profile cache sweep: nothing to clean")
        return cleaned, freed


_cleaner: ProfileCacheCleaner | None = None


def get_profile_cache_cleaner() -> ProfileCacheCleaner:
    global _cleaner
    if _cleaner is None:
        settings = get_settings()
        _cleaner = ProfileCacheCleaner(
            profile_dir=settings.profile_dir,
            interval_minutes=settings.profile_cache_cleanup_interval_minutes,
        )
    return _cleaner
