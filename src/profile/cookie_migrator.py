import asyncio
import json
import shutil
import uuid
from pathlib import Path

from loguru import logger

from ..automation.browser import launch_browser, close_browser, export_cookies, inject_cookies
from ..automation.selectors import SELECTORS
from ..automation.tiktok_login import check_logged_in
from ..callback.core_client import CoreClient
from ..concurrency.lock_manager import get_lock_manager
from ..config import get_settings
from .paths import profile_path

# When there's nothing to migrate, back off harder than the normal per-profile
# interval so an empty backlog doesn't spin-poll Core forever.
IDLE_BACKOFF_MULTIPLIER = 4
IDLE_BACKOFF_MIN_SECONDS = 60


class CookieMigrationJob:
    """Batch migration: for every existing persistent TikTok profile directory not yet
    cookie-backed, launch it once, export its session cookies, verify they actually
    restore a working session in a fresh ephemeral profile, push them to Core, and only
    then delete the old directory — reclaiming its disk space immediately.

    Throttled to one profile per sweep so a burst of Chrome launches across many
    accounts doesn't read as synchronized bot activity. Skips any profile currently
    locked (an active fulfillment run or spawn/debug session) — it'll be picked up on
    a later sweep once it's free. This is a *supplement* to the lazy migration that
    already happens as a side effect of normal top-up/re-login traffic (see
    `profile.session_launch.teardown_session_browser`) — this job exists to clear the
    backlog of dormant accounts that traffic alone wouldn't reach any time soon.
    """

    def __init__(self, core_client: CoreClient, profile_dir: str, interval_seconds: int, sadcaptcha_api_key: str = ""):
        self._core_client = core_client
        self._profile_dir = profile_dir
        self._interval_seconds = interval_seconds
        self._sadcaptcha_api_key = sadcaptcha_api_key
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return self._interval_seconds > 0

    def start(self) -> None:
        if not self.enabled:
            logger.info("Cookie migration job disabled (interval <= 0)")
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_forever())
        logger.info(f"Cookie migration job started, one profile every {self._interval_seconds}s")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("Cookie migration job stopped")

    async def _run_forever(self) -> None:
        while True:
            try:
                migrated = await self.migrate_one()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Cookie migration pass errored: {type(e).__name__}: {e}")
                migrated = False
            delay = self._interval_seconds if migrated else max(self._interval_seconds * IDLE_BACKOFF_MULTIPLIER, IDLE_BACKOFF_MIN_SECONDS)
            await asyncio.sleep(delay)

    async def migrate_one(self) -> bool:
        """Migrate at most one profile. Returns True if a profile was actually processed
        (so the caller can pace the next sweep accordingly)."""
        candidates = await self._core_client.get_profiles_needing_cookie_migration(take=5)
        if not candidates:
            logger.debug("Cookie migration: no profiles pending")
            return False

        lock_mgr = get_lock_manager()
        for candidate in candidates:
            profile_id = candidate.get("id")
            old_path = candidate.get("profilePath")
            if not profile_id or not old_path:
                continue
            if not Path(old_path).is_dir():
                logger.warning(f"Cookie migration: {old_path} no longer exists on disk, skipping")
                continue
            if lock_mgr.is_locked(old_path):
                continue  # actively in use right now — try again next sweep

            await self._migrate_profile(profile_id, old_path)
            return True

        logger.debug("Cookie migration: all pending candidates are currently locked, will retry")
        return False

    async def _migrate_profile(self, profile_id: str, old_path: str) -> None:
        logger.info(f"Cookie migration: processing profile {profile_id} at {old_path}")

        browser = await launch_browser(old_path, sadcaptcha_api_key=self._sadcaptcha_api_key)
        try:
            tab = await browser.get(SELECTORS["recharge_url"])
            await asyncio.sleep(4)
            if not await check_logged_in(tab):
                logger.warning(f"Cookie migration: profile {profile_id} session already invalid, skipping (left on disk for manual re-login)")
                return
            cookies = await export_cookies(browser)
        finally:
            try:
                await close_browser(browser)
            except Exception:
                pass

        if not await self._verify_cookies_restore_session(cookies):
            logger.warning(f"Cookie migration: exported cookies for {profile_id} did not restore a session — NOT deleting {old_path}")
            return

        await self._core_client.update_tiktok_profile(profile_id, {"sessionCookiesJson": json.dumps(cookies)})
        shutil.rmtree(old_path, ignore_errors=True)
        logger.info(f"Cookie migration: migrated profile {profile_id}, freed {old_path}")

    async def _verify_cookies_restore_session(self, cookies: list[dict]) -> bool:
        """Prove the cookies actually work in a fresh ephemeral profile — the same code
        path `_do_topup`/re-login will use — before trusting them enough to delete the
        original directory, not just that export succeeded without error."""
        settings = get_settings()
        verify_profile = profile_path(settings.profile_dir, f"_migration_verify_{uuid.uuid4().hex}")
        browser = await launch_browser(verify_profile, sadcaptcha_api_key=self._sadcaptcha_api_key)
        try:
            await inject_cookies(browser, cookies)
            tab = await browser.get(SELECTORS["recharge_url"])
            await asyncio.sleep(4)
            return await check_logged_in(tab)
        finally:
            try:
                await close_browser(browser)
            except Exception:
                pass
            shutil.rmtree(verify_profile, ignore_errors=True)


_migrator: CookieMigrationJob | None = None


def get_cookie_migrator(core_client: CoreClient) -> CookieMigrationJob:
    global _migrator
    if _migrator is None:
        settings = get_settings()
        _migrator = CookieMigrationJob(
            core_client=core_client,
            profile_dir=settings.profile_dir,
            interval_seconds=settings.cookie_migration_interval_seconds,
            sadcaptcha_api_key=settings.sadcaptcha_api_key,
        )
    return _migrator
