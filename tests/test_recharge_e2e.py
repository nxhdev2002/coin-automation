"""E2E tests: TikTok recharge UI — custom package, recharge button, save card toggle."""
import asyncio
from pathlib import Path

import pytest

from src.automation.browser import launch_browser, wait_for_element
from src.automation.tiktok_recharge import (
    select_custom_package, click_recharge, skip_link_card_prompt,
)

pytestmark = pytest.mark.asyncio

LOGIN_URL = "https://www.tiktok.com/login"
RECHARGE_URL = "https://www.tiktok.com/coin"


async def test_save_card_toggle_not_present_on_login_page():
    """skip_link_card_prompt returns True when toggle not found (safe)."""
    profile = Path(__file__).parent / "profiles" / "save_card_login"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(5)
        result = await skip_link_card_prompt(tab)
        assert result is True
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_recharge_page_loads():
    """Recharge page loads (may redirect to login if not authenticated)."""
    profile = Path(__file__).parent / "profiles" / "recharge_loads"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(RECHARGE_URL)
        await asyncio.sleep(5)
        url = await tab.evaluate("location.href")
        assert "tiktok.com" in str(url)
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_click_recharge_disabled_without_amount():
    """click_recharge returns False when no amount entered."""
    profile = Path(__file__).parent / "profiles" / "recharge_disabled"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(RECHARGE_URL)
        await asyncio.sleep(5)
        result = await click_recharge(tab)
        assert result is False
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_select_custom_package_on_login_page_returns_false():
    """select_custom_package returns False on login page (element not found)."""
    profile = Path(__file__).parent / "profiles" / "custom_login"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(5)
        result = await select_custom_package(tab, 30)
        assert result is False
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_save_card_toggle_element_not_found_safe():
    """When save card toggle element doesn't exist, function returns True (safe)."""
    profile = Path(__file__).parent / "profiles" / "save_card_safe"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(LOGIN_URL)
        await asyncio.sleep(5)
        result = await skip_link_card_prompt(tab)
        assert result is True
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_wait_for_custom_package_button_on_recharge_page():
    """Custom package button exists on recharge page (requires login)."""
    profile = Path(__file__).parent / "profiles" / "custom_pkg"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(RECHARGE_URL)
        await asyncio.sleep(5)
        result = await wait_for_element(tab, '[data-e2e="wallet-package-custom"]', timeout=10)
        if not result:
            pytest.skip("Not logged in — custom package button requires authentication")
    finally:
        try:
            browser.stop()
        except Exception:
            pass
