"""E2E tests: TikTok payment flow — card form, pay button, result detection."""
import asyncio
from pathlib import Path

import pytest

from src.automation.browser import launch_browser
from src.automation.tiktok_payment import (
    fill_card_form, click_pay_now, wait_for_payment_result,
    parse_end_result_url, is_payment_success, error_scan_js,
    PAYMENT_ERROR_KEYWORDS, STRUCTURAL_ERROR_SCAN_JS,
)

pytestmark = pytest.mark.asyncio

RECHARGE_URL = "https://www.tiktok.com/coin"


async def test_fill_card_form_no_pipopay():
    """fill_card_form returns False when pipopay iframe not found."""
    profile = Path(__file__).parent / "profiles" / "fill_no_pipopay"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(RECHARGE_URL)
        await asyncio.sleep(5)
        result = await fill_card_form(browser, tab, "1234567890123456", "123", "12/28", "Test")
        assert result is False
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_click_pay_now_not_found():
    """click_pay_now returns False when cashier button not on page."""
    profile = Path(__file__).parent / "profiles" / "pay_not_found"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(RECHARGE_URL)
        await asyncio.sleep(5)
        result = await click_pay_now(tab)
        assert result is False
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_wait_for_payment_result_timeout():
    """wait_for_payment_result returns timeout when no payment processed."""
    profile = Path(__file__).parent / "profiles" / "pay_timeout"
    profile.mkdir(parents=True, exist_ok=True)
    browser = await launch_browser(str(profile))
    try:
        tab = await browser.get(RECHARGE_URL)
        await asyncio.sleep(5)
        result = await wait_for_payment_result(browser, tab, timeout_seconds=5)
        assert result["payment_status"] == "timeout"
        assert result["error_code"] == "TIMEOUT"
    finally:
        try:
            browser.stop()
        except Exception:
            pass


async def test_parse_end_result_url_success():
    """parse_end_result_url parses a success URL correctly."""
    url = "https://www.tiktok.com/coin/end-result?payment_status=success&error_code=&message=OK&order_id=abc&charge_id=ch_123&pay_method=card&is_redirect=0"
    result = parse_end_result_url(url)
    assert result["payment_status"] == "success"
    assert result["order_id"] == "abc"
    assert result["charge_id"] == "ch_123"
    assert result["pay_method"] == "card"


async def test_succeed_is_a_success():
    """TikTok really returns payment_status=succeed — a charged card must not be
    reported as a failed order (observed on order 3a230dd4 on 2026-08-13)."""
    url = "https://www.tiktok.com/coin/end-result?payment_status=succeed&error_code=&message=&order_id=abc&charge_id=ch_1&pay_method=card&is_redirect=0"
    result = parse_end_result_url(url)
    assert result["payment_status"] == "succeed"
    assert is_payment_success(result) is True


async def test_success_spellings_and_case():
    assert is_payment_success({"payment_status": "success"}) is True
    assert is_payment_success({"payment_status": "Succeed"}) is True
    assert is_payment_success({"payment_status": " succeeded "}) is True


async def test_non_success_statuses():
    assert is_payment_success({"payment_status": "failed"}) is False
    assert is_payment_success({"payment_status": "timeout", "error_code": "TIMEOUT"}) is False
    assert is_payment_success({"payment_status": "processing"}) is False
    assert is_payment_success({"payment_status": "unknown"}) is False
    assert is_payment_success({}) is False


async def test_error_scan_covers_vietnamese():
    """Cashier renders in the account's language — VN error wording must be in the scan
    (verified against the live vi-VN cashier via devtools on 2026-08-13)."""
    for kw in ("không hợp lệ", "bị từ chối", "thất bại", "hết hạn"):
        assert kw in PAYMENT_ERROR_KEYWORDS


async def test_error_scan_js_embeds_keywords_verbatim():
    js = error_scan_js(extra_keywords=("security reasons",))
    assert '"không hợp lệ"' in js          # diacritics survive json embedding
    assert '"security reasons"' in js      # extras appended
    assert '"declined"' in js
    assert js.count("const keywords") == 1


async def test_structural_scan_targets_pipopay_error_markup():
    """Structural detection keys on the DOM pipopay actually renders errors with
    (div.pipo-input-error-msg — verified live on 2026-08-13), so it works in any
    cashier language. It must require visible + non-empty text to avoid matching
    empty error-styled wrappers."""
    assert ".pipo-input-error-msg" in STRUCTURAL_ERROR_SCAN_JS
    assert '[role="alert"]' in STRUCTURAL_ERROR_SCAN_JS
    assert "getBoundingClientRect" in STRUCTURAL_ERROR_SCAN_JS  # visibility gate
    assert "innerText" in STRUCTURAL_ERROR_SCAN_JS              # non-empty text gate


async def test_error_scan_js_extras_do_not_mutate_base():
    error_scan_js(extra_keywords=("one-off",))
    assert "one-off" not in PAYMENT_ERROR_KEYWORDS


async def test_parse_end_result_url_failed():
    """parse_end_result_url parses a failed URL with 3DS error."""
    url = "https://www.tiktok.com/coin/end-result?payment_status=failed&error_code=3DS_REQUIRED&message=3D+Secure+verification+needed&order_id=&charge_id=&pay_method=card&is_redirect=1"
    result = parse_end_result_url(url)
    assert result["payment_status"] == "failed"
    assert result["error_code"] == "3DS_REQUIRED"
    assert result["message"] == "3D Secure verification needed"
    assert result["is_redirect"] == "1"
