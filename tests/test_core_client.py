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
    """update_order sends POST and does not raise on 200."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    core_client._client.post = AsyncMock(return_value=mock_response)

    await core_client.update_order("order-1", {"fulfillmentPhase": "Done"})
    core_client._client.post.assert_called_once()
    args, kwargs = core_client._client.post.call_args
    assert "order-1" in args[0]
    assert kwargs["json"]["fulfillmentPhase"] == "Done"


async def test_update_order_network_error_logged(core_client):
    """update_order logs error and does not raise on network failure."""
    core_client._client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
    await core_client.update_order("order-1", {"fulfillmentPhase": "Done"})


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
