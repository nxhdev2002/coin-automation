import asyncio
from datetime import datetime, timedelta, timezone

from loguru import logger

from .browser import wait_for_element, human_sleep
from .selectors import SELECTORS
from .tiktok_verify import (
    detect_verification_prompt,
    click_verification_option,
    fill_verification_code,
    wait_for_verification_resolved,
)


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


async def detect_login_success(tab) -> str:
    """Returns: 'logged_in', 'verification', or 'still_waiting'."""
    js = """
    (() => {
        const profile = document.querySelector('[data-e2e="profile-icon"]')
                     || document.querySelector('[data-e2e="profile-avatar"]');
        if (profile) return 'logged_in';
        const url = location.href;
        if (!url.includes('/login') && url.includes('tiktok.com')) return 'url_changed';
        const qr = document.querySelector('[data-e2e="qr-code"]');
        const qrText = qr ? (qr.innerText || '').trim() : '';
        if (qr && qrText.toLowerCase().includes('scanned')) return 'qr_scanned';
        if (!qr) return 'qr_gone';
        return 'still_login';
    })()
    """
    try:
        result = await tab.evaluate(js)
        if result in ("logged_in", "url_changed", "qr_gone", "qr_scanned"):
            await human_sleep(1, 3)
            result2 = await tab.evaluate(js)
            logger.info(f"Login check: {result} -> {result2}")

            if result2 in ("logged_in", "url_changed"):
                return "logged_in"

            if result2 in ("still_login", "qr_gone", "qr_scanned"):
                verify_target = await detect_verification_prompt(tab)
                if verify_target:
                    logger.info(f"Verification prompt detected: {verify_target}")
                    return "verification"

            return "still_waiting"
        return "still_waiting"
    except Exception as e:
        logger.warning(f"Login check error: {e}")
        return "still_waiting"


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
        verify_target = await detect_verification_prompt(tab)
        if verify_target:
            logger.info(f"Verification popup detected in main loop: {verify_target}")
            return await handle_verification(tab, callback_client, order_id, deadline, verify_target)

        qr_b64 = await get_qr_element_screenshot(tab)
        if qr_b64 and qr_b64 != last_qr_sent:
            qr_expires = datetime.now(timezone.utc) + timedelta(seconds=90)
            await callback_client.update_order(order_id, {
                "qrCodeBase64": qr_b64,
                "qrCodeExpiresAt": qr_expires.isoformat(),
            })
            last_qr_sent = qr_b64
            logger.info("QR code sent to frontend")

        status = await detect_login_success(tab)
        if status == "logged_in":
            logger.info("Login detected after QR scan")
            await callback_client.update_order(order_id, {
                "fulfillmentPhase": "LoggedIn",
            })
            return True

        if status == "verification":
            logger.info("Verification required — entering verify flow")
            return await handle_verification(tab, callback_client, order_id, deadline)

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


async def handle_verification(tab, callback_client, order_id: str, deadline, verify_target: str | None = None) -> bool:
    """Handle the post-QR verification step.

    1. Detect verification target (masked email/phone)
    2. Report to backend so frontend can show input
    3. Poll backend for user-submitted code
    4. Fill code + click Next
    5. Wait for dialog to disappear
    """
    if not verify_target:
        verify_target = await detect_verification_prompt(tab)
        if not verify_target:
            logger.error("Verification dialog not found")
            return False

    logger.info(f"Verification target: {verify_target}")
    await callback_client.update_order(order_id, {
        "fulfillmentPhase": "WaitingForVerification",
        "verificationTarget": verify_target,
    })

    clicked = await click_verification_option(tab, verify_target)
    if not clicked:
        logger.error("Could not click verification option")
        return False
    logger.info("Clicked verification option — code input should appear")
    await human_sleep(1, 3)

    poll_deadline = min(deadline, datetime.now(timezone.utc) + timedelta(minutes=5))
    while datetime.now(timezone.utc) < poll_deadline:
        code = await callback_client.get_verification_code(order_id)
        if code:
            logger.info(f"Verification code received: {code}")
            filled = await fill_verification_code(tab, code)
            if not filled:
                logger.error("Failed to fill verification code")
                return False

            resolved = await wait_for_verification_resolved(tab, timeout=30)
            if resolved:
                logger.info("Verification succeeded — login complete")
                await callback_client.update_order(order_id, {
                    "fulfillmentPhase": "LoggedIn",
                })
                return True
            else:
                logger.error("Verification code was rejected")
                return False

        await asyncio.sleep(3)

    logger.warning("Verification code timeout — user did not submit code in time")
    return False
