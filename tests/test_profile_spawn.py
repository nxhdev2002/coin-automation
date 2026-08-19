"""Tests: spawn API for saved profiles — browser launch is mocked, no Chrome needed."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import src.profile.spawn as spawn_mod
from src.concurrency.lock_manager import get_lock_manager
from src.main import app
from src.profile.paths import profile_name, profile_path
from src.profile.spawn import (
    ProfileBusyError, ProfileNotFoundError, SpawnManager,
)

pytestmark = pytest.mark.asyncio


class FakeBrowser:
    def __init__(self):
        self.config = MagicMock(host="127.0.0.1", port=9222)
        self.websocket_url = "ws://127.0.0.1:9222/devtools/browser/abc"
        self.stopped_called = False

    async def get(self, url):
        self.tab_url = url
        return MagicMock()

    def stop(self):
        self.stopped_called = True


@pytest.fixture
def fake_browser():
    return FakeBrowser()


@pytest.fixture
def patch_launch(fake_browser):
    async def fake_launch_from_cookies_or_profile(settings, correlation_id, profile_path_value, session_cookies_json, **kwargs):
        return fake_browser, profile_path_value, False

    with patch.object(spawn_mod, "launch_from_cookies_or_profile", AsyncMock(side_effect=fake_launch_from_cookies_or_profile)) as m, \
         patch.object(spawn_mod, "check_logged_in", AsyncMock(return_value=True)):
        yield m


@pytest.fixture
def saved_profile(tmp_path):
    """A profile dir that exists on disk, named the way fulfillment names it."""
    name = profile_name("testuser", "test_tiktok_user")
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return name, str(path)


@pytest.fixture
def manager():
    return SpawnManager()


@pytest.fixture
def api_client(patch_settings):
    """Drive the real router in-process, without running the app lifespan
    (which would fetch secrets and overwrite the test settings)."""
    spawn_mod._spawn_manager = None
    transport = httpx.ASGITransport(app=app)
    yield httpx.AsyncClient(transport=transport, base_url="http://test")
    spawn_mod._spawn_manager = None


async def test_profile_name_matches_fulfillment_lock_key():
    """Path/lock-key helper mirrors what the fulfillment flow builds."""
    assert profile_name("alice", "tiktok_a") == "alice-tiktok_a"
    assert profile_name("", "tiktok_a") == "tiktok_a"
    assert profile_path("C:\\profiles", "alice-tiktok_a").endswith("alice-tiktok_a")


async def test_spawn_missing_profile_raises(manager, patch_launch, tmp_path):
    """A profile that was never saved is a 404, not a silently-created empty one."""
    with pytest.raises(ProfileNotFoundError):
        await manager.spawn(
            profile_path=str(tmp_path / "does-not-exist"),
            lock_key="does-not-exist",
            url="https://www.tiktok.com/coin",
        )
    patch_launch.assert_not_called()


async def test_spawn_create_if_missing(manager, patch_launch, patch_settings, tmp_path):
    """create_if_missing lets a fresh profile be seeded by hand."""
    session = await manager.spawn(
        profile_path=str(tmp_path / "fresh"),
        lock_key="fresh",
        url="https://www.tiktok.com/coin",
        create_if_missing=True,
    )
    assert session.session_id
    await manager.close(session.session_id)


async def test_spawn_holds_and_releases_lock(manager, patch_launch, patch_settings, saved_profile, fake_browser):
    """Session holds the profile lock while open, releases it on close."""
    name, path = saved_profile
    session = await manager.spawn(profile_path=path, lock_key=name, url="https://x")

    assert get_lock_manager().is_locked(name)
    assert session.logged_in is True
    assert session.devtools_url == "http://127.0.0.1:9222"
    assert session.websocket_url.startswith("ws://")

    assert await manager.close(session.session_id) is True
    assert get_lock_manager().is_locked(name) is False
    assert fake_browser.stopped_called is True


async def test_spawn_rejects_busy_profile(manager, patch_launch, saved_profile):
    """Second spawn for the same profile is refused while the first is open."""
    name, path = saved_profile
    session = await manager.spawn(profile_path=path, lock_key=name, url="https://x")
    try:
        with pytest.raises(ProfileBusyError):
            await manager.spawn(profile_path=path, lock_key=name, url="https://x")
    finally:
        await manager.close(session.session_id)


async def test_spawn_releases_lock_when_launch_fails(manager, saved_profile):
    """A failed launch must not leave the profile locked forever."""
    name, path = saved_profile
    with patch.object(spawn_mod, "launch_from_cookies_or_profile", AsyncMock(side_effect=RuntimeError("chrome died"))):
        with pytest.raises(RuntimeError):
            await manager.spawn(profile_path=path, lock_key=name, url="https://x")
    assert get_lock_manager().is_locked(name) is False


async def test_ttl_closes_session(manager, patch_launch, saved_profile, fake_browser):
    """TTL expiry force-closes the browser and frees the lock."""
    name, path = saved_profile
    session = await manager.spawn(
        profile_path=path, lock_key=name, url="https://x", ttl_minutes=0,
    )
    await asyncio.sleep(0.05)
    assert session.session_id not in [s.session_id for s in manager.sessions]
    assert fake_browser.stopped_called is True
    assert get_lock_manager().is_locked(name) is False


async def test_close_unknown_session(manager):
    assert await manager.close("nope") is False


async def test_close_all(manager, patch_launch, saved_profile):
    name, path = saved_profile
    await manager.spawn(profile_path=path, lock_key=name, url="https://x")
    assert await manager.close_all() == 1
    assert manager.sessions == []


async def test_api_spawn_and_close(api_client, patch_launch, saved_profile, fake_browser):
    """POST /profile/spawn -> GET /profile/sessions -> DELETE, over the real router."""
    name, path = saved_profile
    async with api_client as client:
        resp = await client.post("/profile/spawn", json={"profile_path": path})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["profile_path"].endswith(name)
        assert body["logged_in"] is True
        assert body["url"] == "https://www.tiktok.com/coin"
        session_id = body["session_id"]

        listed = (await client.get("/profile/sessions")).json()["sessions"]
        assert [s["session_id"] for s in listed] == [session_id]

        # same profile, still open -> conflict
        busy = await client.post("/profile/spawn", json={"profile_path": path})
        assert busy.status_code == 409

        assert (await client.delete(f"/profile/sessions/{session_id}")).status_code == 200
        assert (await client.get("/profile/sessions")).json()["sessions"] == []
        assert fake_browser.stopped_called is True


async def test_api_spawn_requires_identifier(api_client, patch_launch):
    async with api_client as client:
        resp = await client.post("/profile/spawn", json={})
        assert resp.status_code == 400


async def test_api_spawn_unknown_profile_is_404(api_client, patch_launch):
    async with api_client as client:
        resp = await client.post("/profile/spawn", json={"tiktok_username": "never_saved"})
        assert resp.status_code == 404
