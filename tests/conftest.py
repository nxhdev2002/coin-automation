import asyncio
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Settings
from src.models.fulfill import FulfillRequest


TEST_DIR = Path(__file__).parent
TEST_PROFILE_DIR = TEST_DIR / "profiles"
TEST_SCREENSHOT_DIR = TEST_DIR / "screenshots"


class FakeCoreClient:
    """Mock CoreClient — logs callbacks instead of calling .NET."""

    def __init__(self):
        self.update_order_calls: list[tuple[str, dict]] = []
        self.create_tiktok_profile_calls: list[tuple[str, str, str]] = []
        self.profile_exists = False

    async def update_order(self, order_id: str, data: dict) -> None:
        self.update_order_calls.append((order_id, data))

    async def get_tiktok_profile(self, user_id: str, tiktok_username: str) -> dict | None:
        if self.profile_exists:
            return {"id": "fake-id", "tikTokUsername": tiktok_username}
        return None

    async def create_tiktok_profile(self, user_id: str, username: str, path: str) -> dict:
        self.create_tiktok_profile_calls.append((user_id, username, path))
        return {"id": "new-id", "tikTokUsername": username}

    async def get_card_secret(self, card_id: str) -> dict:
        return {}

    async def close(self):
        pass


@pytest.fixture
def test_settings():
    TEST_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    TEST_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return Settings(
        profile_dir=str(TEST_PROFILE_DIR),
        screenshot_dir=str(TEST_SCREENSHOT_DIR),
        log_dir=str(TEST_DIR / "logs"),
        qr_timeout_minutes=1,
    )


@pytest.fixture
def fake_core_client():
    return FakeCoreClient()


@pytest.fixture
def fulfill_request():
    return FulfillRequest(
        order_id="test-order-001",
        user_id="test-user-id",
        user_name="testuser",
        tiktok_username="test_tiktok_user",
        coin_amount=30,
        card_number="4288520224381899",
        card_cvv="966",
        card_expiry="11/28",
        card_holder_name="Test User",
    )


@pytest.fixture
def patch_settings(test_settings, monkeypatch):
    import src.config as cfg
    cfg.settings = test_settings
    yield test_settings


async def throttle_network(tab, condition="Slow 3G"):
    """Throttle network via CDP Network.emulateNetworkConditions."""
    import nodriver as uc

    presets = {
        "Offline": {"offline": True, "latency": 0, "download": 0, "upload": 0},
        "Slow 3G": {"offline": False, "latency": 2000, "download": 50000, "upload": 20000},
        "Fast 3G": {"offline": False, "latency": 562, "download": 180000, "upload": 84375},
    }
    p = presets.get(condition, {"offline": False, "latency": 2000, "download": 50000, "upload": 20000})
    await tab.send(uc.cdp.network.emulate_network_conditions(
        offline=p["offline"],
        latency=p["latency"],
        download_throughput=p["download"],
        upload_throughput=p["upload"],
    ))


async def unthrottle_network(tab):
    """Reset network conditions to normal."""
    import nodriver as uc
    await tab.send(uc.cdp.network.emulate_network_conditions(
        offline=False,
        latency=0,
        download_throughput=-1,
        upload_throughput=-1,
    ))
