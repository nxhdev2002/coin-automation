"""Tests: CoreClient HTTP callbacks — mock httpx, no browser needed."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from src.callback.core_client import CoreClient

pytestmark = pytest.mark.asyncio


@pytest.fixture
def core_client():
    client = CoreClient("https://localhost:44396", "test-api-key")
    return client


async def test_update_order_success(core_client):
    """update_order sends via _request_with_retry and does not raise on 200."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    core_client._client.request = AsyncMock(return_value=mock_response)

    await core_client.update_order("order-1", {"fulfillmentPhase": "Done"})
    core_client._client.request.assert_called_once()
    args, kwargs = core_client._client.request.call_args
    assert args[0] == "POST"
    assert "order-1" in args[1]
    assert kwargs["json"]["fulfillmentPhase"] == "Done"


async def test_update_order_network_error_logged(core_client):
    """update_order logs error and does not raise on a persistent network failure
    (after exhausting retries — see test_update_order_retries_then_gives_up for the
    retry count itself)."""
    core_client._client.request = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
    await core_client.update_order("order-1", {"fulfillmentPhase": "Done"})


async def test_update_order_retries_then_succeeds_on_403(core_client):
    """A transient 403 (e.g. landing mid-refresh of Core's shared API-key config,
    seen in production) must not permanently drop the status report — it retries
    and succeeds once the transient condition clears."""
    forbidden = MagicMock(status_code=403)
    forbidden.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError("403", request=MagicMock(), response=forbidden))
    ok = MagicMock(status_code=200)
    ok.raise_for_status = MagicMock()
    core_client._client.request = AsyncMock(side_effect=[forbidden, ok])

    await core_client.update_order("order-1", {"fulfillmentPhase": "Done"})
    assert core_client._client.request.call_count == 2


async def test_update_order_retries_then_gives_up(core_client):
    """Persistent 403s exhaust all retry attempts and give up without raising
    (caller — the fulfillment flow — must never crash over a failed status report)."""
    forbidden = MagicMock(status_code=403)
    forbidden.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError("403", request=MagicMock(), response=forbidden))
    core_client._client.request = AsyncMock(return_value=forbidden)

    await core_client.update_order("order-1", {"fulfillmentPhase": "Done"})
    assert core_client._client.request.call_count == 4


async def test_update_order_404_not_retried(core_client):
    """A 404 (order not found) is not transient — it must fail on the first
    attempt, with no retry delay."""
    not_found = MagicMock(status_code=404)
    not_found.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError("404", request=MagicMock(), response=not_found))
    core_client._client.request = AsyncMock(return_value=not_found)

    await core_client.update_order("order-1", {"fulfillmentPhase": "Done"})
    assert core_client._client.request.call_count == 1


async def test_get_tiktok_profile_204(core_client):
    """get_tiktok_profile returns None on 204 No Content."""
    mock_response = MagicMock()
    mock_response.status_code = 204
    core_client._client.get = AsyncMock(return_value=mock_response)
    result = await core_client.get_tiktok_profile("user-1", "tiktok_user")
    assert result is None


async def test_get_tiktok_profile_404(core_client):
    """get_tiktok_profile returns None on 404."""
    mock_response = MagicMock()
    mock_response.status_code = 404
    core_client._client.get = AsyncMock(return_value=mock_response)
    result = await core_client.get_tiktok_profile("user-1", "tiktok_user")
    assert result is None


async def test_get_tiktok_profile_200(core_client):
    """get_tiktok_profile returns dict on 200."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value={"id": "abc", "tikTokUsername": "user1"})
    core_client._client.get = AsyncMock(return_value=mock_response)
    result = await core_client.get_tiktok_profile("user-1", "user1")
    assert result == {"id": "abc", "tikTokUsername": "user1"}


async def test_create_tiktok_profile_success(core_client):
    """create_tiktok_profile sends POST with correct payload."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"id": "new-id"})
    core_client._client.post = AsyncMock(return_value=mock_response)

    result = await core_client.create_tiktok_profile("user-1", "tiktok_user", "C:\\path")
    assert result == {"id": "new-id"}

    args, kwargs = core_client._client.post.call_args
    assert kwargs["json"]["userId"] == "user-1"
    assert kwargs["json"]["tiktokUsername"] == "tiktok_user"
    assert kwargs["json"]["profilePath"] == "C:\\path"
