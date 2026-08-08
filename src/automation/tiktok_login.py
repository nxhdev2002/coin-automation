import asyncio
import base64
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO

from loguru import logger
import nodriver as uc

from .selectors import SELECTORS


async def check_logged_in(tab) -> bool:
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
    try:
        result = await tab.evaluate(js)
        return result == "logged_in"
    except Exception:
        return True


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


async def get_qr_code_base64(tab) -> str | None:
    try:
        qr_el = await tab.find(SELECTORS["qr_code_container"], timeout=5)
        if not qr_el:
            return None
        screenshot = await tab.save_screenshot()
        if isinstance(screenshot, str):
            with open(screenshot, "rb") as f:
                return base64.b64encode(f.read()).decode()
        return None
    except Exception as e:
        logger.warning(f"QR capture failed: {e}")
        return None


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
            await asyncio.sleep(2)
            result2 = await tab.evaluate(js)
            logger.info(f"Login check: {result} -> {result2}")
            return result2 in ("logged_in", "url_changed")
        return False
    except Exception as e:
        logger.warning(f"Login check error: {e}")
        return False


async def qr_login(tab, callback_client, order_id: str, timeout_minutes: int = 5) -> bool:
    logger.info("Starting QR login flow")
    await callback_client.update_order(order_id, {
        "fulfillmentPhase": "WaitingForQrScan",
    })

    await tab.get(SELECTORS["login_url"])
    await asyncio.sleep(3)

    await click_qr_login(tab)
    await asyncio.sleep(2)

    deadline = datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes)
    qr_refreshed_at = datetime.now(timezone.utc)

    while datetime.now(timezone.utc) < deadline:
        qr_b64 = await get_qr_element_screenshot(tab)
        if qr_b64:
            qr_expires = datetime.now(timezone.utc) + timedelta(seconds=90)
            await callback_client.update_order(order_id, {
                "qrCodeBase64": qr_b64,
                "qrCodeExpiresAt": qr_expires.isoformat(),
            })
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
            await asyncio.sleep(2)
            await click_qr_login(tab)
            await asyncio.sleep(2)
            qr_refreshed_at = datetime.now(timezone.utc)

        await asyncio.sleep(2)

    logger.warning("QR login timeout")
    return False
