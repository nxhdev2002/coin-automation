"""Tests: order-level guards — duplicate rejection, login-check errors,
whole-order timeout. No browser needed."""
import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import src.api.fulfill as fulfill_mod
import src.concurrency.drain_manager as drain_mod
from src.automation.tiktok_login import check_logged_in
from src.concurrency.drain_manager import DrainManager
from src.main import app
from src.models.fulfill import FulfillRequest, FulfillResult

pytestmark = pytest.mark.asyncio


# ---------- #1 duplicate orders ----------

@pytest.fixture
def fresh_drain():
    drain_mod._drain_manager = DrainManager()
    yield drain_mod._drain_manager
    drain_mod._drain_manager = None


async def test_drain_manager_duplicate_guards(fresh_drain):
    m = fresh_drain
    assert m.is_active("o1") is False
    m.begin("o1")
    assert m.is_active("o1") is True
    m.end("o1")
    assert m.is_active("o1") is False

    assert m.was_fulfilled("o1") is False
    m.mark_fulfilled("o1")
    assert m.was_fulfilled("o1") is True


async def test_fulfilled_memory_is_bounded(fresh_drain):
    m = fresh_drain
    for i in range(drain_mod.FULFILLED_MEMORY_SIZE + 10):
        m.mark_fulfilled(f"o{i}")
    assert m.was_fulfilled("o0") is False          # oldest evicted
    assert m.was_fulfilled(f"o{drain_mod.FULFILLED_MEMORY_SIZE + 9}") is True


async def test_endpoint_rejects_resend_while_active(patch_settings, fresh_drain):
    """Second POST for an in-flight order must not start a second purchase."""
    release = asyncio.Event()

    async def slow_process(request, client):
        await release.wait()
        return FulfillResult(success=True)

    body = {"order_id": "dup-1", "tiktok_username": "tt", "coin_amount": 30}
    transport = httpx.ASGITransport(app=app)
    with patch.object(fulfill_mod, "process_order", side_effect=slow_process), \
         patch.object(fulfill_mod, "get_core_client") as gcc:
        gcc.return_value.update_order = AsyncMock()
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            first = (await client.post("/fulfill", json=body)).json()
            assert first["success"] is True

            second = (await client.post("/fulfill", json=body)).json()
            assert second["success"] is False
            assert second["failure_category"] == "DuplicateOrder"

            release.set()
            await asyncio.sleep(0.05)  # let the background task finish


async def test_endpoint_rejects_resend_after_success(patch_settings, fresh_drain):
    """An order that already bought its coins must never run again."""
    body = {"order_id": "dup-2", "tiktok_username": "tt", "coin_amount": 30}
    transport = httpx.ASGITransport(app=app)
    with patch.object(fulfill_mod, "process_order",
                      AsyncMock(return_value=FulfillResult(success=True))), \
         patch.object(fulfill_mod, "get_core_client") as gcc:
        gcc.return_value.update_order = AsyncMock()
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            assert (await client.post("/fulfill", json=body)).json()["success"] is True
            await asyncio.sleep(0.05)

            resend = (await client.post("/fulfill", json=body)).json()
            assert resend["failure_category"] == "DuplicateOrder"


async def test_endpoint_allows_retry_after_failure(patch_settings, fresh_drain):
    """A FAILED order is retryable — only successful ones are remembered."""
    body = {"order_id": "retry-1", "tiktok_username": "tt", "coin_amount": 30}
    transport = httpx.ASGITransport(app=app)
    with patch.object(fulfill_mod, "process_order",
                      AsyncMock(return_value=FulfillResult(success=False, failure_category="PaymentFailed"))), \
         patch.object(fulfill_mod, "get_core_client") as gcc:
        gcc.return_value.update_order = AsyncMock()
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            assert (await client.post("/fulfill", json=body)).json()["success"] is True
            await asyncio.sleep(0.05)

            retry = (await client.post("/fulfill", json=body)).json()
            assert retry["success"] is True  # accepted again


# ---------- #5 check_logged_in ----------

async def test_check_logged_in_error_is_not_logged_in():
    """A page that can't be evaluated must not pass for a logged-in one."""
    tab = AsyncMock()
    tab.evaluate = AsyncMock(side_effect=RuntimeError("target crashed"))
    assert await check_logged_in(tab, timeout=0.5, poll_interval=0.1) is False


async def test_check_logged_in_recovers_after_transient_error():
    tab = AsyncMock()
    tab.evaluate = AsyncMock(side_effect=[RuntimeError("hiccup"), "logged_in"])
    assert await check_logged_in(tab, timeout=2, poll_interval=0.05) is True


# ---------- #6 whole-order timeout ----------

async def test_order_timeout_aborts_and_releases_lock(patch_settings, fulfill_request, fake_core_client):
    from src.concurrency.lock_manager import get_lock_manager
    from src.fulfill_processor import process_order
    from src.profile.paths import profile_name

    patch_settings.order_timeout_minutes = 0.002  # ~0.12s

    async def hang(*a, **k):
        await asyncio.sleep(30)

    with patch("src.fulfill_processor._dispatch", side_effect=hang):
        result = await process_order(fulfill_request, fake_core_client)

    assert result.success is False
    assert result.failure_category == "OrderTimeout"
    lock_key = profile_name(fulfill_request.user_name, fulfill_request.tiktok_username)
    assert get_lock_manager().is_locked(lock_key) is False


async def test_order_timeout_disabled_when_zero(patch_settings, fulfill_request, fake_core_client):
    from src.fulfill_processor import process_order

    patch_settings.order_timeout_minutes = 0

    with patch("src.fulfill_processor._dispatch",
               AsyncMock(return_value=FulfillResult(success=True))):
        result = await process_order(fulfill_request, fake_core_client)
    assert result.success is True
