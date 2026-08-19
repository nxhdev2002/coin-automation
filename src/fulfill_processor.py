import asyncio
import json
import time

from loguru import logger

from .automation.browser import (
    launch_browser, close_browser, wait_for_element, human_sleep, parse_eval,
    get_with_retry, export_cookies, inject_cookies,
)
from .automation.captcha_solver import detect_captcha, solve_captcha
from .automation.tiktok_login import qr_login, check_logged_in, fetch_identity
from .automation.tiktok_recharge import (
    select_custom_package, click_recharge, select_add_card,
    skip_link_card_prompt, detect_post_recharge_redirect, wait_for_post_recharge_return,
    uncheck_invite_reward,
)
from .automation.tiktok_payment import (
    fill_card_form, click_pay_now, wait_for_payment_result,
    take_screenshot, is_payment_success,
)
from .automation.selectors import SELECTORS
from .automation.browser_pool import get_warm_pool
from .callback.core_client import CoreClient
from .config import get_settings
from .concurrency.lock_manager import get_lock_manager
from .models.fulfill import FulfillRequest, FulfillResult
from .profile.paths import profile_path, graduate_profile
from .profile.session_launch import launch_from_cookies_or_profile, teardown_session_browser


async def process_order(request: FulfillRequest, core_client: CoreClient) -> FulfillResult:
    settings = get_settings()
    lock_mgr = get_lock_manager()

    # TopUp and re-login both act on an existing, already-known profile directory —
    # lock on that path directly. A brand-new add-account has no profile yet, so it
    # locks on its own link-request id instead (never collides with a real profile path).
    lock_key = request.profile_path or f"link:{request.order_id}"
    await lock_mgr.acquire(lock_key)
    try:
        # Hard ceiling for the whole run: every step has its own timeout, but
        # a hung CDP call would otherwise block forever — holding this profile's
        # lock and queueing every later request for the same account.
        timeout_s = settings.order_timeout_minutes * 60
        if timeout_s > 0:
            async with asyncio.timeout(timeout_s):
                return await _dispatch(request, core_client, settings)
        return await _dispatch(request, core_client, settings)
    except TimeoutError:
        logger.error(f"Request {request.order_id} exceeded {settings.order_timeout_minutes} min, aborted")
        return FulfillResult(
            success=False,
            failure_category="OrderTimeout",
            failure_reason=f"Fulfillment exceeded {settings.order_timeout_minutes} minutes and was aborted",
        )
    except Exception as e:
        import traceback
        logger.error(f"Fulfillment error: {e}")
        logger.error(traceback.format_exc())
        return FulfillResult(
            success=False,
            failure_category="Unknown",
            failure_reason=str(e),
        )
    finally:
        lock_mgr.release(lock_key)


async def _dispatch(request: FulfillRequest, core_client: CoreClient, settings) -> FulfillResult:
    if request.mode == "LoginOnly":
        return await _do_login_only(request, core_client, settings)
    return await _do_topup(request, core_client, settings)


async def _do_topup(request: FulfillRequest, core_client: CoreClient, settings) -> FulfillResult:
    """Recharge on an already-linked, session-valid profile.

    Deliberately does NOT fall back to a QR login: a stale session must
    surface as "please log in again" (fast, via the account-link/re-login
    flow), not silently retry a full login mid-purchase.
    """
    _captcha = {"encountered": False, "solved": False, "cost": 0.0}

    async def _check_captcha(tab):
        info = await detect_captcha(tab)
        if info:
            _captcha["encountered"] = True
            result = await solve_captcha(tab, settings)
            if result:
                _captcha["cost"] += result.get("cost", 0.0)
                _captcha["solved"] = _captcha["solved"] or result["solved"]

    if not request.profile_path and not request.session_cookies_json:
        return FulfillResult(
            success=False,
            failure_category="ProfileMissing",
            failure_reason="No linked TikTok profile or session was provided for this top-up",
        )

    await core_client.update_order(request.order_id, {
        "fulfillmentPhase": "LaunchingBrowser",
    })

    browser, profile, is_ephemeral = await launch_from_cookies_or_profile(
        settings, request.order_id, request.profile_path, request.session_cookies_json,
        disable_images=True, proxy_url=request.proxy_url,
    )
    refresh_cookies = False

    try:
        tab = await browser.get(SELECTORS["recharge_url"])
        await human_sleep(3, 5)

        logged_in = await check_logged_in(tab)
        if not logged_in:
            logger.warning(f"Top-up {request.order_id}: session expired for profile {request.tiktok_profile_id or profile}")
            if request.tiktok_profile_id:
                await core_client.update_tiktok_profile(request.tiktok_profile_id, {"sessionValid": False})
            return FulfillResult(
                success=False,
                failure_category="SessionExpired",
                failure_reason="TikTok session expired — please log in to this account again",
            )
        refresh_cookies = True

        await core_client.update_order(request.order_id, {
            "fulfillmentPhase": "PurchasingCoins",
        })

        tab = await browser.get(SELECTORS["recharge_url"])
        await human_sleep(3, 5)
        await _check_captcha(tab)

        # Verify we're actually on the coin/wallet page
        current_url = ""
        for attempt in range(3):
            try:
                current_url = await tab.evaluate("location.href")
                current_url = parse_eval(current_url) if not isinstance(current_url, str) else current_url
                logger.info(f"Current URL after navigation (attempt {attempt + 1}): {current_url}")
                if "/coin" in current_url or "/wallet" in current_url:
                    break
                logger.warning(f"Not on coin/wallet page (URL: {current_url}) — retrying navigation")
            except Exception as e:
                logger.warning(f"URL check failed: {e}")
            await human_sleep(1, 2)
            tab = await browser.get(SELECTORS["recharge_url"])
            await human_sleep(3, 5)
            await _check_captcha(tab)
        else:
            screenshot = await take_screenshot(tab, settings.screenshot_dir, request.order_id)
            return FulfillResult(
                success=False,
                failure_category="NavigationFailed",
                failure_reason=f"Could not navigate to coin page after 3 attempts (stuck at {current_url})",
                screenshot_path=screenshot,
            )

        await human_sleep(2, 4)
        await uncheck_invite_reward(tab)
        selected = await select_custom_package(tab, request.coin_amount)
        if not selected:
            screenshot = await take_screenshot(tab, settings.screenshot_dir, request.order_id)
            return FulfillResult(
                success=False,
                failure_category="UiElementNotFound",
                failure_reason=f"Could not select custom package for {request.coin_amount} coins",
                screenshot_path=screenshot,
            )

        recharged = await click_recharge(tab)
        if not recharged:
            screenshot = await take_screenshot(tab, settings.screenshot_dir, request.order_id)
            return FulfillResult(
                success=False,
                failure_category="UiElementNotFound",
                failure_reason="Could not click Recharge button",
                screenshot_path=screenshot,
                captcha_encountered=_captcha["encountered"],
                captcha_solved=_captcha["solved"],
                captcha_cost_usd=_captcha["cost"],
            )

        await _check_captcha(tab)

        add_card_ok = await select_add_card(tab)
        if not add_card_ok:
            screenshot = await take_screenshot(tab, settings.screenshot_dir, request.order_id)
            return FulfillResult(
                success=False,
                failure_category="UiElementNotFound",
                failure_reason="Could not select Add Card payment method",
                screenshot_path=screenshot,
            )

        save_card_safe = await skip_link_card_prompt(tab)
        if not save_card_safe:
            screenshot = await take_screenshot(tab, settings.screenshot_dir, request.order_id)
            return FulfillResult(
                success=False,
                failure_category="SaveCardToggleFailure",
                failure_reason="SAFETY ABORT: Could not confirm 'Save card' toggle is unchecked — refusing to risk saving system card to customer account",
                screenshot_path=screenshot,
            )

        iframe_visible = await wait_for_element(tab, 'iframe[src*="pipopay"]', timeout=30)
        if not iframe_visible:
                screenshot = await take_screenshot(tab, settings.screenshot_dir, request.order_id)
                return FulfillResult(
                    success=False,
                    failure_category="UiElementNotFound",
                    failure_reason="Pipopay iframe not visible after selecting Add Card",
                    screenshot_path=screenshot,
                )

        await core_client.update_order(request.order_id, {
            "fulfillmentPhase": "PaymentInProgress",
        })

        filled = await fill_card_form(
            browser, tab,
            card_number=request.card_number,
            card_cvv=request.card_cvv,
            card_expiry=request.card_expiry,
            card_holder=request.card_holder_name,
        )
        if not filled:
            screenshot = await take_screenshot(tab, settings.screenshot_dir, request.order_id)
            return FulfillResult(
                success=False,
                failure_category="UiElementNotFound",
                failure_reason="Could not fill card form",
                screenshot_path=screenshot,
            )

        await human_sleep(1, 3)

        paid = await click_pay_now(tab)
        if not paid:
            screenshot = await take_screenshot(tab, settings.screenshot_dir, request.order_id)
            return FulfillResult(
                success=False,
                failure_category="UiElementNotFound",
                failure_reason="Could not click Pay now button",
                screenshot_path=screenshot,
            )

        # After Pay Now, TikTok may redirect to a 3D Secure page where the
        # card holder confirms the transaction on their banking app (up to 5 min).
        logger.info("Checking for 3DS redirect after Pay Now...")
        if await detect_post_recharge_redirect(tab, timeout=30):
            logger.bind(hit_3ds=True).info("3DS redirect detected — waiting for banking app confirmation")
            await core_client.update_order(request.order_id, {
                "fulfillmentPhase": "WaitingForPaymentConfirm",
            })
            returned = await wait_for_post_recharge_return(
                tab,
                timeout_minutes=request.payment_confirm_timeout_minutes,
                callback_client=core_client,
                order_id=request.order_id,
            )
            if not returned:
                screenshot = await take_screenshot(tab, settings.screenshot_dir, request.order_id)
                return FulfillResult(
                    success=False,
                    failure_category="PaymentConfirmTimeout",
                    failure_reason="Timeout waiting for banking app confirmation after Pay Now",
                    screenshot_path=screenshot,
                )
            logger.info("Banking app confirmed — checking payment result")
            await core_client.update_order(request.order_id, {
                "fulfillmentPhase": "PurchasingCoins",
            })

        result = await wait_for_payment_result(browser, tab, timeout_seconds=60)
        screenshot = await take_screenshot(tab, settings.screenshot_dir, request.order_id)

        if is_payment_success(result):
            logger.info(f"Payment SUCCESS for order {request.order_id}")
            if request.tiktok_profile_id:
                await core_client.update_tiktok_profile(request.tiktok_profile_id, {"sessionValid": True, "markUsed": True})
            return FulfillResult(
                success=True,
                screenshot_path=screenshot,
                fulfillment_phase="Done",
                captcha_encountered=_captcha["encountered"],
                captcha_solved=_captcha["solved"],
                captcha_cost_usd=_captcha["cost"],
            )
        else:
            error_code = result.get("error_code", "")
            if "3DS" in error_code.upper():
                category = "OtpRequired"
                reason = f"3DS verification failed: {result.get('message', '')}"
            elif "RISK" in error_code.upper():
                category = "PaymentFailed"
                reason = f"Payment rejected: {error_code} - {result.get('message', '')}"
            else:
                category = "PaymentFailed"
                detail = result.get("message", "")
                reason = f"Payment failed: {error_code or result.get('payment_status', 'unknown')}"
                if detail:
                    # e.g. CARD_ERROR alone says nothing — keep the on-screen message
                    reason += f" - {detail}"

            logger.warning(f"Payment FAILED for order {request.order_id}: {reason}")
            if request.tiktok_profile_id:
                await core_client.update_tiktok_profile(request.tiktok_profile_id, {"sessionValid": True, "markUsed": True})
            return FulfillResult(
                success=False,
                failure_category=category,
                failure_reason=reason,
                screenshot_path=screenshot,
                fulfillment_phase="Done",
                captcha_encountered=_captcha["encountered"],
                captcha_solved=_captcha["solved"],
                captcha_cost_usd=_captcha["cost"],
            )

    finally:
        await teardown_session_browser(browser, profile, request.tiktok_profile_id, core_client, is_ephemeral, refresh_cookies)


async def _do_login_only(request: FulfillRequest, core_client: CoreClient, settings) -> FulfillResult:
    """QR login only — used both to add a brand-new account (then identify + link
    it) and to re-establish a stale session on an existing profile. No purchase."""
    is_new_account = not request.profile_path
    flow_started_at = time.monotonic()
    profile_id = request.tiktok_profile_id or ""

    await core_client.update_account_link(request.order_id, {
        "fulfillmentPhase": "LaunchingBrowser",
    })

    warm = None
    is_ephemeral = False
    refresh_cookies = False
    if is_new_account and settings.warm_pool_size > 0:
        warm = await get_warm_pool(settings).acquire()

    if warm is not None:
        browser, profile = warm
    elif not is_new_account and request.session_cookies_json:
        browser, profile, is_ephemeral = await launch_from_cookies_or_profile(
            settings, request.order_id, request.profile_path, request.session_cookies_json,
        )
    else:
        profile = request.profile_path or profile_path(settings.profile_dir, request.order_id)
        browser = await launch_browser(profile, sadcaptcha_api_key=settings.sadcaptcha_api_key)

    browser_ready_seconds = round(time.monotonic() - flow_started_at, 2)
    logger.bind(browser_ready_seconds=browser_ready_seconds, warm_pool_hit=warm is not None).info(
        f"[QR Timing] Browser ready in {browser_ready_seconds}s (warm_pool_hit={warm is not None})"
    )

    try:
        if is_new_account:
            # A brand-new profile has no session to check — the recharge_url
            # round-trip below (navigate + sleep + check_logged_in) always
            # resolves to "not logged in" here, so skip straight to QR login.
            # Navigate straight to the QR sub-page instead of the login page's
            # channel list — TikTok serves the QR canvas directly there, so
            # there's no channel-item render + click round-trip to wait on.
            tab = await get_with_retry(browser, SELECTORS["qr_login_url"])
            logged_in = await qr_login(tab, core_client, request.order_id, settings.qr_timeout_minutes, flow_started_at=flow_started_at)
        else:
            tab = await get_with_retry(browser, SELECTORS["recharge_url"])
            await human_sleep(3, 5)

            logged_in = await check_logged_in(tab)
            if not logged_in:
                tab = await get_with_retry(browser, SELECTORS["qr_login_url"])
                logged_in = await qr_login(tab, core_client, request.order_id, settings.qr_timeout_minutes, flow_started_at=flow_started_at)

        if not logged_in:
            screenshot = await take_screenshot(tab, settings.screenshot_dir, request.order_id)
            return FulfillResult(
                success=False,
                failure_category="QrScanTimeout",
                failure_reason="QR scan timeout",
                screenshot_path=screenshot,
            )
        refresh_cookies = True

        if is_new_account:
            logger.info("Login succeeded — waiting for page to settle before fetching identity")
            await human_sleep(5, 8)
            tab = await get_with_retry(browser, SELECTORS["recharge_url"])
            await human_sleep(5, 8)
            identity = await fetch_identity(tab)
            username = identity.get("display_name") or f"tiktok-{request.order_id[:8]}"

            new_account_cookies_json = ""
            try:
                new_account_cookies_json = json.dumps(await export_cookies(browser))
            except Exception as e:
                logger.warning(f"Could not export cookies for new account {request.order_id}: {e}")

            if warm is not None:
                # Chrome must release its handles on the profile dir before it
                # can be moved — close it now instead of waiting for the
                # outer `finally` (which still runs afterward, harmlessly, on
                # an already-closed browser).
                await close_browser(browser)
                profile = graduate_profile(profile, settings.profile_dir, request.order_id)

            profile_record = await core_client.create_tiktok_profile(request.user_id, username, profile, new_account_cookies_json)
            profile_id = profile_record.get("id")
            await core_client.update_tiktok_profile(profile_id, {
                "sessionValid": True,
                "avatarUrl": identity.get("avatar_url"),
                "displayName": identity.get("display_name"),
            })
        else:
            profile_id = request.tiktok_profile_id
            await core_client.update_tiktok_profile(profile_id, {"sessionValid": True})

        return FulfillResult(success=True, fulfillment_phase="Done", tiktok_profile_id=profile_id or "")

    except Exception as e:
        import traceback
        logger.error(f"Add-account/re-login error for {request.order_id}: {e}")
        logger.error(traceback.format_exc())
        return FulfillResult(success=False, failure_category="Unknown", failure_reason=str(e))

    finally:
        await teardown_session_browser(browser, profile, profile_id, core_client, is_ephemeral, refresh_cookies)
