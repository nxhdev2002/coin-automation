"""Run full flow 5 times with fake card to find issues."""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

os.environ["LOG_DIR"] = os.path.join(os.path.dirname(__file__), "poc")
os.environ["PROFILE_DIR"] = os.path.join(os.path.dirname(__file__), "poc", "profiles")
os.environ["SCREENSHOT_DIR"] = os.path.join(os.path.dirname(__file__), "poc", "poc_screenshots")

from src.config import Settings
from src.models.fulfill import FulfillRequest
from src.fulfill_processor import process_order

import src.config as cfg


class FakeCoreClient:
    async def update_order(self, order_id, data):
        phase = data.get("fulfillmentPhase", "")
        if phase:
            print(f"  [{order_id}] Phase: {phase}", flush=True)
        if "qrCodeBase64" in data:
            print(f"  [{order_id}] QR shown", flush=True)
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
    cfg.settings = settings

    client = FakeCoreClient()

    for i in range(1, 6):
        order_id = f"batch-{i:03d}"
        print(f"\n{'='*60}", flush=True)
        print(f"  RUN {i}/5  order={order_id}", flush=True)
        print(f"{'='*60}", flush=True)

        # Fresh profile per run to test QR login every time
        username = f"zmindsettt_5_run{i}"

        request = FulfillRequest(
            order_id=order_id,
            tiktok_username=username,
            coin_amount=30,
            card_id="fake",
            card_number="5587402109876543",
            card_cvv="123",
            card_expiry="12/28",
            card_holder_name="Test User",
        )

        try:
            result = await process_order(request, client)
            print(f"  RESULT: success={result.success} cat={result.failure_category} reason={result.failure_reason}", flush=True)
        except Exception as e:
            print(f"  EXCEPTION: {e}", flush=True)

        # small delay between runs
        await asyncio.sleep(3)

    print(f"\n{'='*60}", flush=True)
    print(f"  ALL 5 RUNS COMPLETE", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
