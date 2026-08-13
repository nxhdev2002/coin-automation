"""E2E tests: browser basics — launch, navigate, evaluate, parse_eval, screenshot."""
import asyncio
import os
from pathlib import Path

import pytest

from src.automation.browser import launch_browser, wait_for_element, parse_eval

pytestmark = pytest.mark.asyncio


async def test_launch_browser():
    """Browser starts and returns a usable object."""
    profile = Path(__file__).parent / "profiles" / "test_launch"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    assert browser is not None
    try:
        tab = await browser.get("https://www.tiktok.com/login")
        assert tab is not None
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_navigate_and_evaluate():
    """Navigate to TikTok login and evaluate a simple expression."""
    profile = Path(__file__).parent / "profiles" / "test_nav"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get("https://www.tiktok.com/login")
        await asyncio.sleep(5)
        url = await tab.evaluate("location.href")
        assert isinstance(url, str)
        assert "tiktok.com" in url
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_wait_for_element_timeout():
    """wait_for_element returns False when element doesn't exist (within timeout)."""
    profile = Path(__file__).parent / "profiles" / "test_timeout"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get("https://www.tiktok.com/login")
        await asyncio.sleep(3)
        result = await wait_for_element(tab, '[data-e2e="this-does-not-exist"]', timeout=3)
        assert result is False
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_wait_for_element_found():
    """wait_for_element finds body on TikTok login page."""
    profile = Path(__file__).parent / "profiles" / "test_found"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get("https://www.tiktok.com/login")
        await asyncio.sleep(5)
        result = await wait_for_element(tab, 'body', timeout=10)
        assert result is True
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_evaluate_returns_value():
    """tab.evaluate returns a value for simple expressions."""
    profile = Path(__file__).parent / "profiles" / "test_eval"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get("https://www.tiktok.com/login")
        await asyncio.sleep(3)
        result = await tab.evaluate("1 + 1")
        assert result == 2
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_parse_eval_list_format():
    """parse_eval converts CDP list format to dict."""
    raw = [["found", {"type": "boolean", "value": True}],
           ["disabled", {"type": "boolean", "value": False}]]
    result = parse_eval(raw)
    assert result == {"found": True, "disabled": False}


async def test_parse_eval_null_type():
    """parse_eval handles CDP null type."""
    raw = [["minError", {"type": "null"}]]
    result = parse_eval(raw)
    assert result == {"minError": None}


async def test_parse_eval_undefined_type():
    """parse_eval handles CDP undefined type."""
    raw = [["checked", {"type": "undefined"}]]
    result = parse_eval(raw)
    assert result == {"checked": None}


async def test_parse_eval_none():
    """parse_eval handles None input."""
    assert parse_eval(None) is None


async def test_parse_eval_raw_dict():
    """parse_eval handles a plain dict with 'value' key."""
    raw = {"type": "string", "value": "hello"}
    result = parse_eval(raw)
    assert result == "hello"


async def test_screenshot():
    """take_screenshot saves a PNG file."""
    from src.automation.tiktok_payment import take_screenshot
    profile = Path(__file__).parent / "profiles" / "test_screenshot"
    profile.mkdir(parents=True, exist_ok=True)
    screenshot_dir = str(Path(__file__).parent / "screenshots")
    Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get("https://www.tiktok.com/login")
        await asyncio.sleep(3)
        path = await take_screenshot(tab, screenshot_dir, "test-browser")
        assert path != ""
        assert os.path.exists(path)
    finally:
        try:
            browser.stop()
        except Exception:
            pass
