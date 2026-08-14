import httpx
from loguru import logger

from ..logging_context import set_order_status


class CoreClient:
    def __init__(self, base_url: str, api_key: str):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-Api-Key": api_key},
            timeout=30.0,
            verify=False,
        )

    async def update_order(self, order_id: str, data: dict) -> None:
        # Every phase transition already flows through here, so this is the one
        # place that has to keep the order status on the logs up to date.
        set_order_status(data.get("fulfillmentPhase", ""))
        try:
            resp = await self._client.post(f"/internal/coins/{order_id}/update", json=data)
            resp.raise_for_status()
            logger.debug(f"update_order {order_id}: {data}")
        except Exception as e:
            logger.error(f"update_order failed: {e}")

    async def get_card_secret(self, card_id: str) -> dict:
        resp = await self._client.get(f"/internal/coins/card-secret/{card_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_tiktok_profile(self, user_id: str, tiktok_username: str) -> dict | None:
        resp = await self._client.get(f"/internal/coins/tiktok-profile/{user_id}/{tiktok_username}")
        if resp.status_code in (404, 204):
            return None
        resp.raise_for_status()
        return resp.json()

    async def create_tiktok_profile(self, user_id: str, username: str, path: str) -> dict:
        resp = await self._client.post(
            "/internal/coins/tiktok-profile",
            json={"userId": user_id, "tiktokUsername": username, "profilePath": path},
        )
        resp.raise_for_status()
        return resp.json()

    async def update_tiktok_profile(self, profile_id: str, data: dict) -> None:
        try:
            resp = await self._client.put(f"/internal/coins/tiktok-profile/{profile_id}", json=data)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"update_tiktok_profile failed: {e}")

    async def get_verification_code(self, order_id: str) -> str | None:
        try:
            resp = await self._client.get(f"/internal/coins/verification-code/{order_id}")
            if resp.status_code == 204:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"get_verification_code failed: {e}")
            return None

    async def close(self):
        await self._client.aclose()
