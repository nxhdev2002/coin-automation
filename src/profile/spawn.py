import asyncio
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from loguru import logger

from ..api.fulfill import get_core_client
from ..automation.browser import close_browser
from ..automation.tiktok_login import check_logged_in
from ..concurrency.lock_manager import get_lock_manager
from ..config import get_settings
from .session_launch import launch_from_cookies_or_profile, teardown_session_browser


class ProfileNotFoundError(Exception):
    """The requested user-data-dir doesn't exist on this host."""


class ProfileBusyError(Exception):
    """The profile is already in use — by a fulfillment run or another spawn session."""


@dataclass
class SpawnSession:
    session_id: str
    profile_path: str
    lock_key: str
    url: str
    logged_in: bool
    started_at: datetime
    expires_at: datetime
    browser: object = None
    tab: object = field(default=None, repr=False)
    is_ephemeral: bool = False
    tiktok_profile_id: str = ""
    devtools_url: str = ""
    websocket_url: str = ""
    expiry_task: asyncio.Task | None = field(default=None, repr=False)


class SpawnManager:
    """Owns the browsers opened by the spawn API.

    A spawned browser has no order driving it, so nothing else would ever close
    it — the manager keeps a handle, holds the profile's lock for as long as the
    session lives, and force-closes it when the TTL expires so a forgotten
    session can't pin a profile (or leak a Chrome process on the VPS) forever.
    """

    def __init__(self):
        self._sessions: dict[str, SpawnSession] = {}

    @property
    def sessions(self) -> list[SpawnSession]:
        return list(self._sessions.values())

    def get(self, session_id: str) -> SpawnSession | None:
        return self._sessions.get(session_id)

    async def spawn(
        self,
        profile_path: str,
        lock_key: str,
        url: str,
        headless: bool = False,
        ttl_minutes: int = 30,
        create_if_missing: bool = False,
        session_cookies_json: str = "",
        tiktok_profile_id: str = "",
    ) -> SpawnSession:
        # With stored cookies there's no persistent dir to require — the session is
        # restored into a throwaway ephemeral profile instead (see launch_from_cookies_or_profile).
        if not session_cookies_json and not os.path.isdir(profile_path):
            if not create_if_missing:
                raise ProfileNotFoundError(profile_path)
            logger.info(f"Profile dir {profile_path} missing, creating a fresh one on request")

        lock_mgr = get_lock_manager()
        if lock_mgr.is_locked(lock_key):
            raise ProfileBusyError(lock_key)

        # No await between the check above and acquire below, so on the single
        # event loop this cannot race another spawn/fulfill for the same profile.
        await lock_mgr.acquire(lock_key)

        session_id = uuid.uuid4().hex
        browser = None
        actual_profile_path = profile_path
        is_ephemeral = False
        try:
            settings = get_settings()
            browser, actual_profile_path, is_ephemeral = await launch_from_cookies_or_profile(
                settings, session_id, profile_path, session_cookies_json, headless=headless,
            )
            tab = await browser.get(url)
            logged_in = await check_logged_in(tab)
        except Exception:
            if browser is not None:
                try:
                    await close_browser(browser)
                except Exception:
                    pass
                if is_ephemeral:
                    shutil.rmtree(actual_profile_path, ignore_errors=True)
            lock_mgr.release(lock_key)
            raise

        now = datetime.now(timezone.utc)
        session = SpawnSession(
            session_id=session_id,
            profile_path=actual_profile_path,
            lock_key=lock_key,
            url=url,
            logged_in=logged_in,
            started_at=now,
            expires_at=now + timedelta(minutes=ttl_minutes),
            browser=browser,
            tab=tab,
            is_ephemeral=is_ephemeral,
            tiktok_profile_id=tiktok_profile_id,
            devtools_url=_devtools_url(browser),
            websocket_url=getattr(browser, "websocket_url", "") or "",
        )
        self._sessions[session.session_id] = session
        session.expiry_task = asyncio.create_task(
            self._expire(session.session_id, ttl_minutes * 60)
        )
        logger.info(
            f"Spawned profile session {session.session_id} profile={actual_profile_path} "
            f"logged_in={logged_in} ephemeral={is_ephemeral} ttl={ttl_minutes}m"
        )
        return session

    async def close(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False

        task = session.expiry_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()

        # Re-check right before closing (not the stale flag from spawn time) — an
        # admin may have just finished a manual re-login inside this session, and
        # that's exactly the case worth persisting back to Core.
        currently_logged_in = session.logged_in
        try:
            currently_logged_in = await check_logged_in(session.tab)
        except Exception:
            pass

        await teardown_session_browser(
            session.browser, session.profile_path, session.tiktok_profile_id,
            get_core_client(), session.is_ephemeral, refresh_cookies=currently_logged_in,
        )

        get_lock_manager().release(session.lock_key)
        logger.info(f"Closed profile session {session_id} profile={session.profile_path}")
        return True

    async def close_all(self) -> int:
        closed = 0
        for session_id in list(self._sessions):
            if await self.close(session_id):
                closed += 1
        return closed

    async def _expire(self, session_id: str, delay_seconds: float) -> None:
        try:
            await asyncio.sleep(delay_seconds)
        except asyncio.CancelledError:
            return
        logger.warning(f"Profile session {session_id} hit its TTL, closing")
        await self.close(session_id)


def _devtools_url(browser) -> str:
    config = getattr(browser, "config", None)
    host = getattr(config, "host", None)
    port = getattr(config, "port", None)
    if not host or not port:
        return ""
    return f"http://{host}:{port}"


_spawn_manager: SpawnManager | None = None


def get_spawn_manager() -> SpawnManager:
    global _spawn_manager
    if _spawn_manager is None:
        _spawn_manager = SpawnManager()
    return _spawn_manager
