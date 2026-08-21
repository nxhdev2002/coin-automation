"""Tests: WarmBrowserPool discards stale/dead pooled browsers instead of handing
them out — the bug behind "pool browser bi timeout" when a pooled entry sits idle
for a long time (e.g. overnight) before the next new-account order acquires it."""
import asyncio

import pytest

import src.automation.browser_pool as browser_pool_mod
from src.automation.browser_pool import WarmBrowserPool

pytestmark = pytest.mark.asyncio


class FakeBrowser:
    def __init__(self, stopped=False, hang_on_update_targets=False):
        self.stopped = stopped
        self.hang_on_update_targets = hang_on_update_targets
        self.closed = False

    async def update_targets(self):
        if self.hang_on_update_targets:
            await asyncio.sleep(3600)  # never resolves within the liveness timeout
        if self.stopped:
            raise RuntimeError("connection closed")


@pytest.fixture(autouse=True)
def fast_liveness_timeout(monkeypatch):
    # Real timeout is 5s — too slow for a test exercising the hang path.
    monkeypatch.setattr(browser_pool_mod, "_LIVENESS_CHECK_TIMEOUT_SECONDS", 0.05)


async def _make_pool(monkeypatch, launch_results=None, max_age_minutes=30):
    pool = WarmBrowserPool(pool_dir="unused", size=0, max_age_minutes=max_age_minutes)
    monkeypatch.setattr(browser_pool_mod, "close_browser", _fake_close_browser)
    if launch_results is not None:
        results = iter(launch_results)

        async def fake_launch_browser(profile, **kwargs):
            return next(results)

        monkeypatch.setattr(browser_pool_mod, "launch_browser", fake_launch_browser)
    return pool


async def _fake_close_browser(browser):
    browser.closed = True


async def test_acquire_returns_none_on_empty_queue(monkeypatch):
    pool = await _make_pool(monkeypatch)
    assert await pool.acquire() is None


async def test_acquire_returns_healthy_fresh_browser(monkeypatch):
    pool = await _make_pool(monkeypatch)
    browser = FakeBrowser()
    await pool._queue.put((browser, "profile-a", asyncio.get_event_loop().time()))

    result = await pool.acquire()

    assert result == (browser, "profile-a")
    assert browser.closed is False


async def test_acquire_discards_stopped_browser_and_returns_none(monkeypatch):
    pool = await _make_pool(monkeypatch)
    dead = FakeBrowser(stopped=True)
    await pool._queue.put((dead, "profile-dead", asyncio.get_event_loop().time()))

    result = await pool.acquire()

    assert result is None
    assert dead.closed is True


async def test_acquire_discards_hanging_browser_and_returns_none(monkeypatch):
    """The exact overnight scenario: process alive, CDP connection stale — a raw
    call would hang forever without the bounded liveness check."""
    pool = await _make_pool(monkeypatch)
    hanging = FakeBrowser(hang_on_update_targets=True)
    await pool._queue.put((hanging, "profile-hanging", asyncio.get_event_loop().time()))

    result = await asyncio.wait_for(pool.acquire(), timeout=2)

    assert result is None
    assert hanging.closed is True


async def test_acquire_discards_entry_older_than_max_age(monkeypatch):
    pool = await _make_pool(monkeypatch, max_age_minutes=0)  # anything already queued is "too old"
    stale_but_alive = FakeBrowser()
    await pool._queue.put((stale_but_alive, "profile-old", asyncio.get_event_loop().time() - 10))

    result = await pool.acquire()

    assert result is None
    assert stale_but_alive.closed is True


async def test_acquire_skips_stale_entries_and_returns_next_healthy_one(monkeypatch):
    pool = await _make_pool(monkeypatch)
    dead = FakeBrowser(stopped=True)
    healthy = FakeBrowser()
    await pool._queue.put((dead, "profile-dead", asyncio.get_event_loop().time()))
    await pool._queue.put((healthy, "profile-healthy", asyncio.get_event_loop().time()))

    result = await pool.acquire()

    assert result == (healthy, "profile-healthy")
    assert dead.closed is True
    assert healthy.closed is False
