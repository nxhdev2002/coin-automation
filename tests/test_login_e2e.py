"""E2E tests: TikTok login flow — check_logged_in, QR login, timeout."""
import asyncio
from pathlib import Path

import pytest

from src.automation.browser import launch_browser, wait_for_element
from src.automation.tiktok_login import check_logged_in, qr_login, click_qr_login

pytestmark = pytest.mark.asyncio

LOGIN_URL = "https://www.tiktok.com/login"


async def test_login_page_loads():
    """TikTok login page loads successfully."""
    profile = Path(__file__).parent / "profiles" / "login_loads"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(5)
        result = await wait_for_element(tab, '[data-e2e="top-login-button"]', timeout=15)
        assert result is True
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_check_logged_in_returns_bool():
    """check_logged_in returns a boolean."""
    profile = Path(__file__).parent / "profiles" / "login_bool"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(5)
        result = await check_logged_in(tab, timeout=10)
        assert isinstance(result, bool)
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_check_logged_in_not_logged_in():
    """On a fresh profile (not logged in), check_logged_in returns False."""
    profile = Path(__file__).parent / "profiles" / "login_fresh"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(5)
        result = await check_logged_in(tab, timeout=10)
        assert result is False
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_qr_login_button_exists():
    """QR login channel item exists on login page."""
    profile = Path(__file__).parent / "profiles" / "qr_exists"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(5)
        await click_qr_login(tab)
        await asyncio.sleep(2)
        result = await wait_for_element(tab, '[data-e2e="qr-code"]', timeout=10)
        assert result is True
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_qr_login_timeout(fake_core_client):
    """qr_login returns False when nobody scans the QR (timeout=1 min)."""
    profile = Path(__file__).parent / "profiles" / "qr_timeout"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(5)
        await click_qr_login(tab)
        await asyncio.sleep(2)
        result = await qr_login(tab, fake_core_client, "test-qr-timeout", timeout_minutes=1)
        assert result is False

        assert len(fake_core_client.update_order_calls) > 0
        phases = [c[1].get("fulfillmentPhase") for c in fake_core_client.update_order_calls]
        assert "WaitingForQrScan" in phases
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_qr_login_sends_qr_callback(fake_core_client):
    """qr_login calls update_order with qrCodeBase64 at least once."""
    profile = Path(__file__).parent / "profiles" / "qr_callback"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(5)
        await click_qr_login(tab)
        await asyncio.sleep(2)
        await qr_login(tab, fake_core_client, "test-qr-cb", timeout_minutes=1)

        qr_calls = [c for c in fake_core_client.update_order_calls if "qrCodeBase64" in c[1]]
        assert len(qr_calls) > 0
        assert qr_calls[0][1]["qrCodeBase64"].startswith("data:image")
    finally:
        try:
            browser.stop()
        except Exception:
            pass
