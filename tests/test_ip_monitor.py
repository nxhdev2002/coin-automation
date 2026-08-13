"""Tests: VPS public IP monitor + Telegram alert. No network, no real Telegram."""
import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import src.monitor.ip_watcher as watcher_mod
from src.main import app
from src.monitor.ip_watcher import IpWatcher, fetch_public_ip
from src.notify.telegram import TelegramNotifier

pytestmark = pytest.mark.asyncio


def _response(text: str):
    resp = MagicMock()
    resp.text = text
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    return resp


@contextmanager
def http_returns(*results):
    """Patch the HTTP client used for IP lookups; each result is a response or an exception."""
    client = MagicMock()
    client.get = AsyncMock(side_effect=list(results))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    with patch.object(watcher_mod.httpx, "AsyncClient", MagicMock(return_value=ctx)):
        yield client


@contextmanager
def lookup_returns(ip: str, source: str = "https://p1"):
    """Patch the lookup itself — for tests about change detection, not fetching."""
    with patch.object(watcher_mod, "fetch_public_ip", AsyncMock(return_value=(ip, source))) as m:
        yield m


@pytest.fixture
def notifier():
    n = TelegramNotifier("token", "chat")
    n.send = AsyncMock(return_value=True)
    return n


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "state" / "ip_state.json")


def make_watcher(state_path, notifier, interval_minutes=5, **kwargs):
    return IpWatcher(
        state_path=state_path,
        notifier=notifier,
        interval_minutes=interval_minutes,
        providers=("https://p1", "https://p2", "https://p3"),
        **kwargs,
    )


async def seed(watcher, ip="203.0.113.7"):
    """Give the watcher a baseline and clear the resulting startup alert."""
    with lookup_returns(ip):
        await watcher.check()
    watcher._notifier.send.reset_mock()


async def test_fetch_falls_back_to_next_provider():
    """First provider erroring must not stop the lookup."""
    with http_returns(httpx.ConnectError("boom"), _response("203.0.113.7")) as client:
        ip, source = await fetch_public_ip(("https://p1", "https://p2"))
    assert (ip, source) == ("203.0.113.7", "https://p2")
    assert client.get.await_count == 2


async def test_fetch_rejects_non_ipv4_answers():
    """An error page or IPv6 answer is discarded, not mistaken for a new IP."""
    with http_returns(_response("<html>error</html>"), _response("2001:db8::1"),
                      _response("198.51.100.9")):
        ip, source = await fetch_public_ip(("https://p1", "https://p2", "https://p3"))
    assert (ip, source) == ("198.51.100.9", "https://p3")


async def test_fetch_all_providers_down():
    with http_returns(httpx.ConnectError("x"), httpx.ConnectError("y"), httpx.ConnectError("z")):
        assert await fetch_public_ip(("https://p1", "https://p2", "https://p3")) == ("", "")


async def test_first_check_sets_baseline_without_change_alert(state_path, notifier):
    """No previous state: record the IP and confirm the wiring, don't cry 'changed'."""
    w = make_watcher(state_path, notifier)
    with lookup_returns("203.0.113.7"):
        state = await w.check()

    assert state.public_ip == "203.0.113.7"
    assert state.previous_ip == ""
    assert state.monitor_enabled is True
    assert state.interval_minutes == 5
    notifier.send.assert_awaited_once()
    assert "IP monitor started" in notifier.send.await_args.args[0]
    with open(state_path, encoding="utf-8") as f:
        assert json.load(f)["public_ip"] == "203.0.113.7"


async def test_unchanged_ip_sends_nothing(state_path, notifier):
    w = make_watcher(state_path, notifier)
    await seed(w)

    with lookup_returns("203.0.113.7"):
        state = await w.check()

    notifier.send.assert_not_awaited()
    assert state.public_ip == "203.0.113.7"
    assert state.previous_ip == ""


async def test_changed_ip_alerts_once_with_both_addresses(state_path, notifier):
    w = make_watcher(state_path, notifier)
    await seed(w)

    with lookup_returns("198.51.100.9"):
        state = await w.check()

    notifier.send.assert_awaited_once()
    message = notifier.send.await_args.args[0]
    assert "203.0.113.7" in message and "198.51.100.9" in message
    assert state.public_ip == "198.51.100.9"
    assert state.previous_ip == "203.0.113.7"
    assert state.changed_at

    notifier.send.reset_mock()
    with lookup_returns("198.51.100.9"):
        await w.check()
    notifier.send.assert_not_awaited()


async def test_restart_reloads_state_and_stays_quiet(state_path, notifier):
    """A deploy/restart must not look like an IP change."""
    first = make_watcher(state_path, notifier)
    await seed(first)

    fresh_notifier = TelegramNotifier("token", "chat")
    fresh_notifier.send = AsyncMock(return_value=True)
    second = make_watcher(state_path, fresh_notifier)
    with lookup_returns("203.0.113.7"):
        state = await second.check()

    fresh_notifier.send.assert_not_awaited()
    assert state.public_ip == "203.0.113.7"


async def test_state_file_is_source_of_truth(state_path, notifier):
    """Editing the state file by hand (the manual alert test) is picked up."""
    w = make_watcher(state_path, notifier)
    await seed(w)

    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"public_ip": "1.2.3.4", "previous_ip": "", "changed_at": ""}, f)

    with lookup_returns("203.0.113.7"):
        state = await w.check()

    notifier.send.assert_awaited_once()
    assert state.previous_ip == "1.2.3.4"
    assert state.public_ip == "203.0.113.7"


async def test_repeated_failures_warn_only_once(state_path, notifier):
    w = make_watcher(state_path, notifier, failure_threshold=3)
    await seed(w)

    for _ in range(5):
        with lookup_returns("", ""):
            state = await w.check()

    assert state.consecutive_failures == 5
    notifier.send.assert_awaited_once()
    assert "cannot determine" in notifier.send.await_args.args[0]
    assert state.public_ip == "203.0.113.7"  # last known IP kept, not wiped


async def test_failure_counter_resets_after_recovery(state_path, notifier):
    w = make_watcher(state_path, notifier, failure_threshold=2)
    await seed(w)
    for _ in range(2):
        with lookup_returns("", ""):
            await w.check()

    with lookup_returns("203.0.113.7"):
        state = await w.check()

    assert state.consecutive_failures == 0


async def test_disabled_monitor_does_not_start_task(state_path, notifier):
    w = make_watcher(state_path, notifier, interval_minutes=0)
    w.start()
    assert w.enabled is False
    assert w._task is None
    await w.stop()


async def test_corrupt_state_file_starts_fresh(state_path, notifier, tmp_path):
    (tmp_path / "state").mkdir()
    with open(state_path, "w", encoding="utf-8") as f:
        f.write("{not json")

    w = make_watcher(state_path, notifier)
    with lookup_returns("203.0.113.7"):
        state = await w.check()
    assert state.public_ip == "203.0.113.7"


async def test_telegram_disabled_when_unconfigured():
    n = TelegramNotifier("", "")
    assert n.enabled is False
    assert await n.send("hi") is False


async def test_api_endpoints(patch_settings, state_path, notifier):
    """GET /ip serves the cached state; POST /ip/check forces a fresh lookup."""
    watcher_mod._ip_watcher = make_watcher(state_path, notifier)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            with lookup_returns("203.0.113.7"):
                first = await client.get("/ip")
            assert first.status_code == 200
            assert first.json()["public_ip"] == "203.0.113.7"

            # cached: no lookup happens, so a dead provider changes nothing
            with lookup_returns("", "") as lookup:
                cached = await client.get("/ip")
            lookup.assert_not_awaited()
            assert cached.json()["public_ip"] == "203.0.113.7"
            assert cached.json()["consecutive_failures"] == 0

            with lookup_returns("198.51.100.9"):
                forced = await client.post("/ip/check")
            assert forced.json()["public_ip"] == "198.51.100.9"
            assert forced.json()["previous_ip"] == "203.0.113.7"
    finally:
        watcher_mod._ip_watcher = None
