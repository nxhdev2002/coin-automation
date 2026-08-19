"""E2E tests: fulfill_processor — full orchestration flow + error branches."""
import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from src.fulfill_processor import process_order
from src.models.fulfill import FulfillRequest, FulfillResult
from src.config import Settings

pytestmark = pytest.mark.asyncio

TEST_DIR = Path(__file__).parent
TEST_PROFILE_DIR = TEST_DIR / "profiles"
TEST_SCREENSHOT_DIR = TEST_DIR / "screenshots"


@pytest.fixture
def test_settings_local():
    TEST_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    TEST_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return Settings(
        profile_dir=str(TEST_PROFILE_DIR),
        screenshot_dir=str(TEST_SCREENSHOT_DIR),
        log_dir=str(TEST_DIR / "logs"),
        qr_timeout_minutes=1,
    )


async def test_fulfill_session_expired(fulfill_request, fake_core_client, patch_settings):
    """Top-up on a profile with no valid session fails fast with SessionExpired —
    _do_topup no longer falls back to a QR-login retry (that's the separate,
    explicit re-login flow via _do_login_only); it only ever checks the existing
    session and fails immediately if it isn't logged in."""
    result = await process_order(fulfill_request, fake_core_client)
    assert result.success is False
    assert result.failure_category == "SessionExpired"

    phases = [c[1].get("fulfillmentPhase") for c in fake_core_client.update_order_calls]
    assert "LaunchingBrowser" in phases


async def test_fulfill_browser_crash(fulfill_request, fake_core_client, patch_settings):
    """Browser launch failure → Unknown category."""
    with patch("src.fulfill_processor.launch_from_cookies_or_profile", side_effect=Exception("Chrome not found")):
        result = await process_order(fulfill_request, fake_core_client)
    assert result.success is False
    assert result.failure_category == "Unknown"
    assert "Chrome not found" in result.failure_reason


async def test_fulfill_lock_per_user(fulfill_request, fake_core_client, patch_settings):
    """Lock is acquired (keyed on the profile path, per process_order) and released."""
    from src.concurrency.lock_manager import get_lock_manager
    lock_mgr = get_lock_manager()

    lock_key = fulfill_request.profile_path
    assert not lock_mgr.is_locked(lock_key)

    asyncio.create_task(process_order(fulfill_request, fake_core_client))
    await asyncio.sleep(1)

    assert lock_mgr.is_locked(lock_key)

    await asyncio.sleep(10)

    assert not lock_mgr.is_locked(lock_key)
