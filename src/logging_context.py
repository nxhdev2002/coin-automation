from contextlib import contextmanager
from contextvars import ContextVar

# Fields stamped onto every log record emitted while an order is being processed,
# and the exact set the ELK sink maps into each document.
ORDER_LOG_FIELDS = (
    "order_id",
    "buyer_id",
    "buyer_user_name",
    "tiktok_username",
    "order_status",
    "flow_type",
)

# Ad-hoc metric fields set via logger.bind(...) at individual log call sites
# (not carried by the order context) — also promoted into the ELK document so
# they're queryable/aggregatable in Kibana instead of buried in free-text messages.
METRIC_LOG_FIELDS = (
    "browser_ready_seconds",
    "warm_pool_hit",
    "qr_ready_seconds",
    "order_success",
    "failure_category",
    "hit_3ds",
)

_order_context: ContextVar[dict[str, str] | None] = ContextVar(
    "coin_order_log_context", default=None
)


@contextmanager
def order_log_context(
    order_id: str = "",
    buyer_id: str = "",
    buyer_user_name: str = "",
    tiktok_username: str = "",
    order_status: str = "",
    flow_type: str = "",
):
    """Tag every log emitted inside this block with the order it belongs to.

    Backed by a contextvar, so concurrent fulfillments (each its own asyncio
    task) never see each other's fields. `flow_type` ("TopUp" | "LoginOnly",
    from `FulfillRequest.mode`) lets ELK split top-up orders from add-account/
    re-login traffic even for the shared deep-automation logs (QR timing etc.)
    that don't otherwise know which flow they're running under.
    """
    ctx = {
        "order_id": order_id,
        "buyer_id": buyer_id,
        "buyer_user_name": buyer_user_name,
        "tiktok_username": tiktok_username,
        "order_status": order_status,
        "flow_type": flow_type,
    }
    token = _order_context.set(ctx)
    try:
        yield ctx
    finally:
        _order_context.reset(token)


def set_order_status(status: str) -> None:
    """Update the status carried by subsequent logs of the current order."""
    ctx = _order_context.get()
    if ctx is not None and status:
        ctx["order_status"] = status


def current_order_context() -> dict[str, str]:
    ctx = _order_context.get()
    return dict(ctx) if ctx else {}


def patch_record(record) -> None:
    """loguru patcher — stamp the active order's fields onto `record["extra"]`.

    This has to happen here rather than in the sink: sinks are added with
    `enqueue=True` and therefore run on a worker thread, where the contextvar of
    the emitting task isn't visible. An explicit `logger.bind(...)` value wins.
    """
    ctx = _order_context.get()
    if not ctx:
        return
    extra = record["extra"]
    for key, value in ctx.items():
        if value and key not in extra:
            extra[key] = value
