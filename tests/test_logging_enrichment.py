"""Tests: order fields (id, buyer, status) riding along on every log into ELK."""
import asyncio

import pytest
from loguru import logger

from src.logging_context import (
    ORDER_LOG_FIELDS, current_order_context, order_log_context, patch_record,
    set_order_status,
)
from src.logging_setup import build_log_document

pytestmark = pytest.mark.asyncio


@pytest.fixture
def captured_records():
    """Capture records through the same patcher the app installs."""
    records = []
    logger.configure(patcher=patch_record)
    sink_id = logger.add(lambda m: records.append(m.record), level="DEBUG", enqueue=False)
    yield records
    logger.remove(sink_id)
    logger.configure(patcher=lambda record: None)


async def test_context_stamps_order_fields(captured_records):
    with order_log_context(
        order_id="order-1",
        buyer_id="buyer-uuid",
        buyer_user_name="hoang",
        tiktok_username="abc123",
        order_status="Received",
    ):
        logger.info("launching browser")

    doc = build_log_document(captured_records[-1])
    assert doc["order_id"] == "order-1"
    assert doc["buyer_id"] == "buyer-uuid"
    assert doc["buyer_user_name"] == "hoang"
    assert doc["tiktok_username"] == "abc123"
    assert doc["order_status"] == "Received"
    assert doc["message"] == "launching browser"
    assert doc["namespace"] == "VCCUS-CoinAutomation"


async def test_status_follows_phase_changes(captured_records):
    with order_log_context(order_id="order-1", order_status="Received"):
        logger.info("first")
        set_order_status("PurchasingCoins")
        logger.info("second")

    statuses = [build_log_document(r)["order_status"] for r in captured_records]
    assert statuses == ["Received", "PurchasingCoins"]


async def test_no_order_fields_outside_context(captured_records):
    logger.info("service started")
    doc = build_log_document(captured_records[-1])
    assert not any(field in doc for field in ORDER_LOG_FIELDS)


async def test_empty_fields_are_omitted(captured_records):
    with order_log_context(order_id="order-1"):
        logger.info("no buyer info on this one")
    doc = build_log_document(captured_records[-1])
    assert doc["order_id"] == "order-1"
    assert "buyer_id" not in doc
    assert "order_status" not in doc


async def test_explicit_bind_wins(captured_records):
    with order_log_context(order_id="order-1"):
        logger.bind(order_id="override").info("bound")
    assert build_log_document(captured_records[-1])["order_id"] == "override"


async def test_concurrent_orders_do_not_mix(captured_records):
    """Two fulfillments in flight must not borrow each other's order fields."""

    async def run(order_id: str, status: str, delay: float):
        with order_log_context(order_id=order_id, order_status="Received"):
            await asyncio.sleep(delay)
            set_order_status(status)
            logger.info(f"working on {order_id}")
            assert current_order_context()["order_id"] == order_id

    await asyncio.gather(
        run("order-A", "PaymentInProgress", 0.02),
        run("order-B", "PurchasingCoins", 0.01),
    )

    docs = {d["order_id"]: d for d in (build_log_document(r) for r in captured_records)}
    assert docs["order-A"]["order_status"] == "PaymentInProgress"
    assert docs["order-B"]["order_status"] == "PurchasingCoins"


async def test_core_client_update_order_tracks_status(captured_records):
    """A phase callback to the core API moves the status on subsequent logs."""
    from unittest.mock import AsyncMock, MagicMock

    from src.callback.core_client import CoreClient

    client = CoreClient("https://localhost:44396", "key")
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    client._client.post = AsyncMock(return_value=mock_response)

    with order_log_context(order_id="order-1", order_status="Received"):
        await client.update_order("order-1", {"fulfillmentPhase": "PaymentInProgress"})
        logger.info("after phase update")

    assert build_log_document(captured_records[-1])["order_status"] == "PaymentInProgress"
