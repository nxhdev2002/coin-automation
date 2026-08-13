import asyncio
from datetime import datetime, timedelta, timezone

from loguru import logger

from .browser import wait_for_element, human_sleep
from .selectors import SELECTORS


async def check_logged_in(tab, timeout: float = 6.0, poll_interval: float = 0.4) -> bool:
    """Poll the page as soon as it loads, instead of a fixed sleep + single check.

    Returns as soon as the login state is determinable (usually well under
    `timeout`), so an already-authenticated profile doesn't sit through a
    fixed wait before we notice it's logged in.
    """
    js = """
    (() => {
        const profile = document.querySelector('[data-e2e="profile-icon"]')
                     || document.querySelector('[data-e2e="profile-avatar"]');
        if (profile) return 'logged_in';
        const loginBtn = document.querySelector('[data-e2e="top-login-button"]');
        if (loginBtn) return 'not_logged_in';
        return 'unknown';
    })()
    """
    elapsed = 0.0
    while elapsed < timeout:
        try:
            result = await tab.evaluate(js)
        except Exception as e:
            # A broken page must not pass for a logged-in one — keep polling and
            # let the timeout fall through to False (the QR flow re-detects an
            # actually-logged-in session via detect_login_success).
            logger.warning(f"check_logged_in evaluate failed: {type(e).__name__}: {e}")
            result = "unknown"
        if result == "logged_in":
            return True
        if result == "not_logged_in":
            return False
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return False


async def click_qr_login(tab) -> None:
    js = """
    (() => {
        const items = document.querySelectorAll('[data-e2e="channel-item"]');
        for (const item of items) {
            if (item.innerText.toLowerCase().includes('qr')) {
                item.click();
                return true;
            }
        }
        return false;
    })()
    """
    result = await tab.evaluate(js)
    logger.info(f"QR login click result: {result}")


async def get_qr_element_screenshot(tab) -> str | None:
    js = """
    (() => {
        const canvas = document.querySelector('[data-e2e="qr-code"] canvas');
        if (!canvas) return null;
        try {
            const dataUrl = canvas.toDataURL('image/png');
            return dataUrl;
        } catch(e) { return null; }
    })()
    """
    try:
        result = await tab.evaluate(js)
        if isinstance(result, str) and result.startswith("data:image"):
            return result
        return None
    except Exception:
        return None


async def detect_login_success(tab) -> bool:
    js = """
    (() => {
        const profile = document.querySelector('[data-e2e="profile-icon"]')
                     || document.querySelector('[data-e2e="profile-avatar"]');
        if (profile) return 'logged_in';
        const url = location.href;
        if (!url.includes('/login') && url.includes('tiktok.com')) return 'url_changed';
        const qr = document.querySelector('[data-e2e="qr-code"]');
        if (!qr) return 'qr_gone';
        return 'still_login';
    })()
    """
    try:
        result = await tab.evaluate(js)
        if result in ("logged_in", "url_changed", "qr_gone"):
            await human_sleep(1, 3)
            result2 = await tab.evaluate(js)
            logger.info(f"Login check: {result} -> {result2}")
            return result2 in ("logged_in", "url_changed")
        return False
    except Exception as e:
        logger.warning(f"Login check error: {e}")
        return False


async def qr_login(tab, callback_client, order_id: str, timeout_minutes: int = 5) -> bool:
    """Run the QR login flow.

    Assumes the caller has already navigated `tab` to the login page and
    confirmed the account isn't logged in — this function does not
    re-navigate there, to avoid loading the login page twice.
    """
    logger.info("Starting QR login flow")
    await callback_client.update_order(order_id, {
        "fulfillmentPhase": "WaitingForQrScan",
    })

    await click_qr_login(tab)
    await wait_for_element(tab, SELECTORS["qr_code_container"], timeout=6)

    deadline = datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes)
    qr_refreshed_at = datetime.now(timezone.utc)
    last_qr_sent = None

    while datetime.now(timezone.utc) < deadline:
        qr_b64 = await get_qr_element_screenshot(tab)
        if qr_b64 and qr_b64 != last_qr_sent:
            qr_expires = datetime.now(timezone.utc) + timedelta(seconds=90)
            await callback_client.update_order(order_id, {
                "qrCodeBase64": qr_b64,
                "qrCodeExpiresAt": qr_expires.isoformat(),
            })
            last_qr_sent = qr_b64
            logger.info("QR code sent to frontend")

        if await detect_login_success(tab):
            logger.info("Login detected after QR scan")
            await callback_client.update_order(order_id, {
                "fulfillmentPhase": "LoggedIn",
            })
            return True

        if datetime.now(timezone.utc) - qr_refreshed_at > timedelta(seconds=80):
            logger.info("QR likely expired, refreshing...")
            await tab.get(SELECTORS["login_url"])
            await click_qr_login(tab)
            await wait_for_element(tab, SELECTORS["qr_code_container"], timeout=6)
            last_qr_sent = None
            qr_refreshed_at = datetime.now(timezone.utc)

        await asyncio.sleep(2)

    logger.warning("QR login timeout")
    return False
