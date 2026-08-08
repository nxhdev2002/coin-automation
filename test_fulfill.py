"""Test script: run full fulfillment flow with existing PoC session."""
import asyncio
import sys
import os

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

os.environ["LOG_DIR"] = os.path.join(os.path.dirname(__file__), "poc")
os.environ["PROFILE_DIR"] = os.path.join(os.path.dirname(__file__), "poc", "profiles")
os.environ["SCREENSHOT_DIR"] = os.path.join(os.path.dirname(__file__), "poc", "poc_screenshots")

from src.config import settings, Settings
from src.models.fulfill import FulfillRequest
from src.fulfill_processor import process_order
from src.callback.core_client import CoreClient


class FakeCoreClient:
    """Mock CoreClient that just logs callbacks instead of calling .NET."""
    async def update_order(self, order_id, data):
        print(f"  [CALLBACK] order={order_id} data={data}", flush=True)
    async def get_card_secret(self, card_id):
        return {}
    async def close(self):
        pass


async def main():
    settings = Settings(
        profile_dir=os.path.join(os.path.dirname(__file__), "poc", "profiles"),
        screenshot_dir=os.path.join(os.path.dirname(__file__), "poc", "poc_screenshots"),
        log_dir=os.path.join(os.path.dirname(__file__), "poc"),
        qr_timeout_minutes=1,
    )

    import src.config as cfg
    cfg.settings = settings

    request = FulfillRequest(
        order_id="test-001",
        tiktok_username="zmindsettt_5",
        coin_amount=30,
        card_id="fake",
        card_number="4288520224381899",
        card_cvv="966",
        card_expiry="11/28",
        card_holder_name="Test User",
    )

    client = FakeCoreClient()
    print("=" * 60, flush=True)
    print(f"  Test fulfill: order={request.order_id} user={request.tiktok_username}", flush=True)
    print(f"  card={request.card_number} coins={request.coin_amount}", flush=True)
    print("=" * 60, flush=True)

    result = await process_order(request, client)
    print(f"\n{'=' * 60}", flush=True)
    print(f"  RESULT: success={result.success}", flush=True)
    print(f"  failure_category={result.failure_category}", flush=True)
    print(f"  failure_reason={result.failure_reason}", flush=True)
    print(f"  screenshot={result.screenshot_path}", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
