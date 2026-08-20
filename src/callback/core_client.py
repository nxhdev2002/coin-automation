import asyncio

import httpx
from loguru import logger

from ..logging_context import set_order_status

# 401/403 can happen if a request lands during a momentary refresh window on
# Core's shared API-key config (seen in production: the same order's earlier
# calls succeeded, only one landed mid-refresh and got a bogus 403); 429/5xx
# are ordinary backend hiccups. A single hit on a status-report call must not
# permanently drop that order's final outcome, so these get a few retries.
# Anything else (404 order-not-found, 400 bad payload, etc.) fails immediately.
_RETRYABLE_STATUS_CODES = {401, 403, 429, 500, 502, 503, 504}
_RETRY_BACKOFF_SECONDS = (1, 3, 8)

# Explicit backstop on top of the client's own `timeout=30.0` — seen in
# production: a request over the Tailscale-routed connection to Core silently
# hung well past 30s with zero error (a stale pooled connection / dead-socket
# "TCP black hole" that never surfaces a read/connect error at the transport
# layer). A plain httpx timeout wasn't enough to bound it, so this wraps every
# attempt in an asyncio-level wait_for that forcibly cancels and retries no
# matter what the transport is doing underneath.
_HARD_REQUEST_TIMEOUT_SECONDS = 35


class CoreClient:
    def __init__(self, base_url: str, api_key: str):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-Api-Key": api_key},
            timeout=30.0,
            verify=False,
        )

    async def _request_with_retry(self, method: str, url: str, json_body: dict) -> httpx.Response:
        """Send with retry+backoff on transient failures (401/403/429/5xx, or a
        network error). Raises on the final attempt's failure, same as a plain
        unretried request would — callers keep their existing try/except."""
        last_exc: Exception | None = None
        for delay in _RETRY_BACKOFF_SECONDS + (None,):
            try:
                resp = await asyncio.wait_for(
                    self._client.request(method, url, json=json_body),
                    timeout=_HARD_REQUEST_TIMEOUT_SECONDS,
                )
                resp.raise_for_status()
                return resp
            except httpx.HTTPStatusError as e:
                if delay is None or e.response.status_code not in _RETRYABLE_STATUS_CODES:
                    raise
                last_exc = e
            except (httpx.RequestError, asyncio.TimeoutError) as e:
                if delay is None:
                    raise
                last_exc = e
            logger.warning(f"{method} {url} failed ({last_exc}), retrying in {delay}s")
            await asyncio.sleep(delay)

    async def update_order(self, order_id: str, data: dict) -> None:
        # Every phase transition already flows through here, so this is the one
        # place that has to keep the order status on the logs up to date.
        set_order_status(data.get("fulfillmentPhase", ""))
        try:
            await self._request_with_retry("POST", f"/internal/coins/{order_id}/update", data)
            logger.debug(f"update_order {order_id}: {data}")
        except Exception as e:
            logger.error(f"update_order failed: {e}")

    async def get_tiktok_profile(self, user_id: str, tiktok_username: str) -> dict | None:
        resp = await self._client.get(f"/internal/coins/tiktok-profile/{user_id}/{tiktok_username}")
        if resp.status_code in (404, 204):
            return None
        resp.raise_for_status()
        return resp.json()

    async def create_tiktok_profile(self, user_id: str, username: str, path: str, session_cookies_json: str = "") -> dict:
        payload = {"userId": user_id, "tiktokUsername": username, "profilePath": path}
        if session_cookies_json:
            payload["sessionCookiesJson"] = session_cookies_json
        resp = await self._client.post("/internal/coins/tiktok-profile", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def update_tiktok_profile(self, profile_id: str, data: dict) -> None:
        try:
            await self._request_with_retry("PUT", f"/internal/coins/tiktok-profile/{profile_id}", data)
        except Exception as e:
            logger.error(f"update_tiktok_profile failed: {e}")

    async def update_account_link(self, link_request_id: str, data: dict) -> None:
        # Same status-report call as update_order, just for the login-only/
        # add-account flow — same retry+hard-timeout treatment.
        set_order_status(data.get("fulfillmentPhase", ""))
        try:
            await self._request_with_retry("POST", f"/internal/coins/account-link/{link_request_id}/update", data)
            logger.debug(f"update_account_link {link_request_id}: {data}")
        except Exception as e:
            logger.error(f"update_account_link failed: {e}")

    async def get_account_link_verification_code(self, link_request_id: str) -> str | None:
        try:
            resp = await self._client.get(f"/internal/coins/account-link/{link_request_id}/verification-code")
            if resp.status_code == 204 or not resp.text or resp.text.strip() in ("", "null"):
                return None
            resp.raise_for_status()
            return resp.text.strip()
        except Exception as e:
            logger.error(f"get_account_link_verification_code failed: {e}")
            return None

    async def get_account_link_verification_option(self, link_request_id: str) -> str | None:
        try:
            resp = await self._client.get(f"/internal/coins/account-link/{link_request_id}/verification-option")
            if resp.status_code == 204 or not resp.text or resp.text.strip() in ("", "null"):
                return None
            resp.raise_for_status()
            return resp.text.strip()
        except Exception as e:
            logger.error(f"get_account_link_verification_option failed: {e}")
            return None

    async def close(self):
        await self._client.aclose()
