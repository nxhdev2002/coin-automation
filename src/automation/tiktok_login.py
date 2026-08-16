import asyncio
import time
from datetime import datetime, timedelta, timezone

from loguru import logger

from .browser import wait_for_element, human_sleep, parse_eval
from .selectors import SELECTORS
from .captcha_solver import detect_captcha, solve_captcha
from ..config import get_settings
from .tiktok_verify import (
    detect_verification_prompt,
    detect_verification_options,
    click_verification_option,
    fill_verification_code,
    wait_for_verification_resolved,
)


async def check_logged_in(tab, timeout: float = 30.0, poll_interval: float = 1.0) -> bool:
    """Check if the user is logged in by looking for profile indicators on the page.

    Returns True if logged in, False if not.
    """
    js = """
    (() => {
        if (document.readyState !== 'complete') return 'loading';

        const iconEl = document.querySelector('[data-e2e="profile-icon"]')
                    || document.querySelector('[data-e2e="profile-avatar"]')
                    || document.querySelector('[data-e2e="avatar"]');
        if (iconEl) return 'logged_in: icon';

        const nameEl = document.querySelector('[class*="wallet-user-name"]')
                    || document.querySelector('[class*="user-name"]');
        if (nameEl && nameEl.innerText && nameEl.innerText.trim()) return 'logged_in: name=' + nameEl.innerText.trim();

        const uploadLink = document.querySelector('a[href*="tiktokstudio/upload"]');
        if (uploadLink) return 'logged_in: upload-link';

        const inboxBtn = document.querySelector('[data-e2e="inbox-entry"]');
        if (inboxBtn) return 'logged_in: inbox';

        const loginBtn = document.querySelector('[data-e2e="top-login-button"]');
        if (loginBtn) return 'not_logged_in: login-btn';

        const url = location.href;
        if (url.includes('/login')) return 'not_logged_in: url=/login';

        const body = document.body ? document.body.innerText.slice(0, 200) : '';
        if (body.includes('Log in') || body.includes('Đăng nhập')) return 'not_logged_in: login-text';

        return 'unknown: url=' + url + ' body=' + body.slice(0, 100);
    })()
    """
    elapsed = 0.0
    while elapsed < timeout:
        try:
            result = await tab.evaluate(js)
            logger.info(f"[Login] check_logged_in result: {result}")
        except Exception as e:
            logger.warning(f"check_logged_in evaluate failed: {type(e).__name__}: {e}")
            result = "loading"
        if isinstance(result, str) and result.startswith("logged_in"):
            return True
        if isinstance(result, str) and result.startswith("not_logged_in"):
            return False
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    logger.warning(f"check_logged_in timeout after {timeout}s — assuming not logged in")
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

                if result == "qr_gone" and result2 == "qr_gone":
                    try:
                        url = await tab.evaluate("location.href")
                        url = parse_eval(url) if not isinstance(url, str) else url
                        if "/login" in url and "qrcode" not in url:
                            logger.info("[Login] QR gone but still on login page — QR likely expired, will refresh")
                            return "qr_expired"
                        body = await tab.evaluate("document.body.innerText.slice(0, 200)")
                        body = parse_eval(body) if not isinstance(body, str) else body
                        logger.info(f"[Login] QR gone — waiting for phone confirmation (URL: {url}, body: {body})")
                    except Exception:
                        logger.info("[Login] QR gone — waiting for phone confirmation")
                    await asyncio.sleep(5)

            return "still_waiting"
        return "still_waiting"
    except Exception as e:
        logger.warning(f"Login check error: {e}")
        return "still_waiting"


async def fetch_identity(tab) -> dict:
    """Read the logged-in account's own username/avatar off the /coin page.

    Only meaningful right after a fresh login — used to confirm which TikTok
    account actually just authenticated (add-account flow), rather than trusting
    whatever the caller typed. Verified 2026-08-15 against the live page: the
    avatar has no <img> tag — it's a CSS background-image on the profile-icon
    div, so it has to be pulled out of the computed style, not `.src`.
    """
    js = f"""
    (() => {{
        const nameEl = document.querySelector('{SELECTORS["wallet_user_name"]}');
        const iconEl = document.querySelector('{SELECTORS["wallet_avatar_icon"]}');
        let avatarUrl = null;
        if (iconEl) {{
            const bg = getComputedStyle(iconEl).backgroundImage;
            const match = bg && bg.match(/url\\((['"]?)(.*?)\\1\\)/);
            avatarUrl = match ? match[2] : null;
        }}
        return {{
            display_name: nameEl ? (nameEl.innerText || '').trim() : null,
            avatar_url: avatarUrl,
        }};
    }})()
    """
    try:
        result = await tab.evaluate(js)
        result = parse_eval(result) or {}
        logger.info(f"[Identity] fetched: {result}")
        return {
            "display_name": result.get("display_name") or None,
            "avatar_url": result.get("avatar_url") or None,
        }
    except Exception as e:
        logger.warning(f"[Identity] fetch failed: {e}")
        return {"display_name": None, "avatar_url": None}


async def qr_login(tab, callback_client, order_id: str, timeout_minutes: int = 5, flow_started_at: float | None = None) -> bool:
    """Run the QR login flow.

    Assumes the caller has already navigated `tab` to the login page and
    confirmed the account isn't logged in — this function does not
    re-navigate there, to avoid loading the login page twice.

    `flow_started_at` (a `time.monotonic()` stamp from the caller) is optional
    and only used to log how long the first QR code took to become visible —
    passing nothing just skips that log line.
    """
    logger.info("Starting QR login flow")
    settings = get_settings()
    await callback_client.update_account_link(order_id, {
        "fulfillmentPhase": "WaitingForQrScan",
    })

    await click_qr_login(tab)
    await wait_for_element(tab, SELECTORS["qr_code_container"], timeout=30)

    deadline = datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes)
    qr_refreshed_at = datetime.now(timezone.utc)
    last_qr_sent = None

    while datetime.now(timezone.utc) < deadline:
        if await detect_verification_prompt(tab):
            logger.info("Verification popup detected in main loop")
            return await handle_verification(tab, callback_client, order_id, deadline)

        captcha_result = await solve_captcha(tab, settings)
        if captcha_result:
            if not captcha_result["solved"]:
                logger.error(f"Captcha not solved during QR login: {captcha_result}")
                return False
            logger.info(f"Captcha solved during QR login: {captcha_result}")

        qr_b64 = await get_qr_element_screenshot(tab)
        if qr_b64 and qr_b64 != last_qr_sent:
            if last_qr_sent is None and flow_started_at is not None:
                qr_ready_seconds = round(time.monotonic() - flow_started_at, 2)
                logger.bind(qr_ready_seconds=qr_ready_seconds).info(
                    f"[QR Timing] QR code ready to scan in {qr_ready_seconds}s"
                )
            qr_expires = datetime.now(timezone.utc) + timedelta(seconds=90)
            await callback_client.update_account_link(order_id, {
                "qrCodeBase64": qr_b64,
                "qrCodeExpiresAt": qr_expires.isoformat(),
            })
            last_qr_sent = qr_b64
            logger.info("QR code sent to frontend")

        status = await detect_login_success(tab)
        if status == "logged_in":
            logger.info("Login detected after QR scan")
            await callback_client.update_account_link(order_id, {
                "fulfillmentPhase": "LoggedIn",
            })
            return True

        if status == "verification":
            logger.info("Verification required — entering verify flow")
            return await handle_verification(tab, callback_client, order_id, deadline)

        if status == "qr_expired" or datetime.now(timezone.utc) - qr_refreshed_at > timedelta(seconds=80):
            logger.info("QR expired, refreshing...")
            await tab.get(SELECTORS["login_url"])
            await wait_for_element(tab, '[data-e2e="channel-item"]', timeout=30)
            await click_qr_login(tab)
            await wait_for_element(tab, SELECTORS["qr_code_container"], timeout=30)
            last_qr_sent = None
            qr_refreshed_at = datetime.now(timezone.utc)

        await asyncio.sleep(2)

    logger.warning("QR login timeout")
    return False


async def handle_verification(tab, callback_client, order_id: str, deadline, verify_target: str | None = None) -> bool:
    """Handle the post-QR verification step.

    1. Detect all available verification options (email/phone)
    2. Report options to backend so frontend can let user choose
    3. Poll backend for user's selected option
    4. Click selected option → code input appears
    5. Poll backend for user-submitted code
    6. Fill code + click Next
    7. Wait for dialog to disappear
    """
    settings = get_settings()

    # Extract all available verification options
    if not verify_target:
        options = await detect_verification_options(tab)
        if not options:
            # Fallback: try single-target detection
            verify_target = await detect_verification_prompt(tab)
            if not verify_target:
                logger.error("Verification dialog not found")
                return False
            options = [verify_target]

    logger.info(f"Verification options: {options}")
    await callback_client.update_account_link(order_id, {
        "fulfillmentPhase": "WaitingForVerification",
        "verificationOptions": options,
    })

    # If only one option, auto-select it. Otherwise wait for user to choose.
    if len(options) == 1:
        selected_option = options[0]
        logger.info(f"Only one verification option — auto-selecting: {selected_option}")
    else:
        logger.info(f"Multiple verification options — waiting for user to select one of: {options}")
        option_deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
        selected_option = None
        while datetime.now(timezone.utc) < option_deadline:
            selected_option = await callback_client.get_account_link_verification_option(order_id)
            if selected_option:
                logger.info(f"User selected verification option: {selected_option}")
                break
            await asyncio.sleep(3)

        if not selected_option:
            logger.error("Timeout waiting for user to select verification option")
            return False

    # Click the selected option
    clicked = await click_verification_option(tab, selected_option)
    if not clicked:
        logger.error(f"Could not click verification option: {selected_option}")
        return False
    logger.info(f"Clicked verification option: {selected_option} — input should appear")
    await human_sleep(1, 3)

    captcha_result = await solve_captcha(tab, settings)
    if captcha_result:
        if not captcha_result["solved"]:
            logger.error(f"Captcha not solved after verification click: {captcha_result}")
            return False
        logger.info(f"Captcha solved after verification click: {captcha_result}")

    is_password = "password" in selected_option.lower()
    logger.info(f"Verification type: {'password' if is_password else 'OTP code'}")

    poll_deadline = datetime.now(timezone.utc) + timedelta(minutes=5)
    while datetime.now(timezone.utc) < poll_deadline:
        code = await callback_client.get_account_link_verification_code(order_id)
        if code:
            logger.info(f"Verification {'password' if is_password else 'code'} received: {'***' if is_password else code}")
            filled = await fill_verification_code(tab, code, is_password=is_password)
            if not filled:
                logger.error(f"Failed to fill {'password' if is_password else 'verification code'}")
                return False

            resolved = await wait_for_verification_resolved(tab, timeout=120)
            if resolved:
                logger.info("Verification succeeded — login complete")
                await callback_client.update_account_link(order_id, {
                    "fulfillmentPhase": "LoggedIn",
                })
                return True
            else:
                logger.error("Verification code was rejected")
                return False

        await asyncio.sleep(3)

    logger.warning("Verification code timeout — user did not submit code in time")
    return False
