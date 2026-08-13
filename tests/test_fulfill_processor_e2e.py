"""E2E tests: fulfill_processor — full orchestration flow + error branches."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from src.fulfill_processor import process_order, _save_profile
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


async def test_fulfill_qr_timeout(fulfill_request, fake_core_client, patch_settings):
    """Full process_order with QR timeout (nobody scans) → QrScanTimeout."""
    result = await process_order(fulfill_request, fake_core_client)
    assert result.success is False
    assert result.failure_category == "QrScanTimeout"
    assert "QR scan timeout" in result.failure_reason

    phases = [c[1].get("fulfillmentPhase") for c in fake_core_client.update_order_calls]
    assert "LaunchingBrowser" in phases
    assert "WaitingForQrScan" in phases


async def test_fulfill_browser_crash(fulfill_request, fake_core_client, patch_settings):
    """Browser launch failure → Unknown category."""
    with patch("src.fulfill_processor.launch_browser", side_effect=Exception("Chrome not found")):
        result = await process_order(fulfill_request, fake_core_client)
    assert result.success is False
    assert result.failure_category == "Unknown"
    assert "Chrome not found" in result.failure_reason


async def test_fulfill_lock_per_user(fulfill_request, fake_core_client, patch_settings):
    """Lock is acquired and released per user_name-tiktok_username."""
    from src.concurrency.lock_manager import get_lock_manager
    lock_mgr = get_lock_manager()

    lock_key = f"{fulfill_request.user_name}-{fulfill_request.tiktok_username}"
    assert not lock_mgr.is_locked(lock_key)

    asyncio.create_task(process_order(fulfill_request, fake_core_client))
    await asyncio.sleep(1)

    assert lock_mgr.is_locked(lock_key)

    await asyncio.sleep(65)

    assert not lock_mgr.is_locked(lock_key)


async def test_save_profile_new(fake_core_client, fulfill_request, patch_settings):
    """_save_profile creates a new profile when it doesn't exist."""
    fake_core_client.profile_exists = False
    profile_path = "C:\\test\\profiles\\testuser-test_tiktok_user"
    await _save_profile(fake_core_client, fulfill_request, profile_path)
    assert len(fake_core_client.create_tiktok_profile_calls) == 1
    user_id, username, path = fake_core_client.create_tiktok_profile_calls[0]
    assert username == fulfill_request.tiktok_username
    assert path == profile_path


async def test_save_profile_exists(fake_core_client, fulfill_request, patch_settings):
    """_save_profile skips creation when profile already exists."""
    fake_core_client.profile_exists = True
    await _save_profile(fake_core_client, fulfill_request, "some_path")
    assert len(fake_core_client.create_tiktok_profile_calls) == 0


async def test_save_profile_api_error(fulfill_request, patch_settings):
    """_save_profile handles API errors gracefully (no exception raised)."""
    client = MagicMock()
    client.get_tiktok_profile = AsyncMock(side_effect=Exception("Network error"))
    client.create_tiktok_profile = AsyncMock()
    await _save_profile(client, fulfill_request, "path")
    assert client.create_tiktok_profile.call_count == 0
