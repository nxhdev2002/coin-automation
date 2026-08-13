"""E2E tests: network lag scenarios — throttle via CDP, offline, tab crash, recovery."""
import asyncio
from pathlib import Path

import pytest

from src.automation.browser import launch_browser, wait_for_element
from tests.conftest import throttle_network, unthrottle_network

pytestmark = pytest.mark.asyncio

LOGIN_URL = "https://www.tiktok.com/login"


async def test_element_timeout_under_slow_3g():
    """wait_for_element times out under Slow 3G (3s timeout, page too slow)."""
    profile = Path(__file__).parent / "profiles" / "net_slow_3g_timeout"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(5)
        await throttle_network(tab, "Slow 3G")
        try:
            result = await wait_for_element(tab, '[data-e2e="top-login-button"]', timeout=3)
            assert result is False
        finally:
            await unthrottle_network(tab)
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_element_found_under_slow_3g():
    """wait_for_element finds element under Slow 3G with longer timeout."""
    profile = Path(__file__).parent / "profiles" / "net_slow_3g_found"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(5)
        await throttle_network(tab, "Slow 3G")
        try:
            result = await wait_for_element(tab, 'body', timeout=30)
            assert result is True
        finally:
            await unthrottle_network(tab)
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_offline_navigation_fails():
    """Navigation under Offline conditions — element not found."""
    profile = Path(__file__).parent / "profiles" / "net_offline"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(3)
        await throttle_network(tab, "Offline")
        try:
            await tab.get("https://www.tiktok.com/coin")
            await asyncio.sleep(3)
            result = await wait_for_element(tab, '[data-e2e="wallet-buy-now-button"]', timeout=3)
            assert result is False
        finally:
            await unthrottle_network(tab)
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_payment_result_timeout_under_slow_network():
    """wait_for_payment_result times out quickly under Slow 3G."""
    from src.automation.tiktok_payment import wait_for_payment_result
    profile = Path(__file__).parent / "profiles" / "net_pay_slow"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(3)
        await throttle_network(tab, "Slow 3G")
        try:
            result = await wait_for_payment_result(browser, tab, timeout_seconds=5)
            assert result["payment_status"] == "timeout"
        finally:
            await unthrottle_network(tab)
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_network_recover_after_throttle():
    """Element loads after network is unthrottled."""
    profile = Path(__file__).parent / "profiles" / "net_recover"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(5)
        await throttle_network(tab, "Slow 3G")
        await asyncio.sleep(2)
        await unthrottle_network(tab)
        result = await wait_for_element(tab, 'body', timeout=15)
        assert result is True
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_intermittent_connection():
    """wait_for_element detects element when connection recovers mid-poll."""
    profile = Path(__file__).parent / "profiles" / "net_intermittent"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(5)
        await throttle_network(tab, "Slow 3G")
        await asyncio.sleep(1)
        await unthrottle_network(tab)
        result = await wait_for_element(tab, '[data-e2e="top-login-button"]', timeout=15)
        assert result is True
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_evaluate_fails_on_tab_crash():
    """tab.evaluate raises exception when tab is closed — handled gracefully."""
    from src.automation.tiktok_login import check_logged_in
    profile = Path(__file__).parent / "profiles" / "net_tab_crash"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(3)
        try:
            await tab.close()
        except Exception:
            pass
        await asyncio.sleep(1)
        result = await check_logged_in(tab)
        assert result is True
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_qr_login_timeout_under_slow_network(fake_core_client):
    """QR login fails under Slow 3G (QR doesn't load in time)."""
    from src.automation.tiktok_login import qr_login, click_qr_login
    profile = Path(__file__).parent / "profiles" / "net_qr_slow"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(5)
        await throttle_network(tab, "Slow 3G")
        await click_qr_login(tab)
        await asyncio.sleep(2)
        result = await qr_login(tab, fake_core_client, "test-qr-slow", timeout_minutes=1)
        assert result is False
        await unthrottle_network(tab)
    finally:
        try:
            browser.stop()
        except Exception:
            pass
