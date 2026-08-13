import asyncio

from loguru import logger

from .automation.browser import launch_browser, wait_for_element, human_sleep
from .automation.tiktok_login import qr_login, check_logged_in
from .automation.tiktok_recharge import (
    select_custom_package, click_recharge, select_add_card,
    skip_link_card_prompt,
)
from .automation.tiktok_payment import (
    fill_card_form, click_pay_now, wait_for_payment_result,
    take_screenshot, parse_end_result_url, is_payment_success,
)
from .automation.selectors import SELECTORS
from .callback.core_client import CoreClient
from .config import get_settings
from .concurrency.lock_manager import get_lock_manager
from .models.fulfill import FulfillRequest, FulfillResult
from .profile.paths import profile_name, profile_path


async def process_order(request: FulfillRequest, core_client: CoreClient) -> FulfillResult:
    settings = get_settings()
    lock_mgr = get_lock_manager()

    lock_key = profile_name(request.user_name, request.tiktok_username)
    await lock_mgr.acquire(lock_key)
    try:
        return await _do_fulfill(request, core_client, settings)
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


async def _save_profile(core_client: CoreClient, request: FulfillRequest, path: str):
    try:
        existing = await core_client.get_tiktok_profile(request.user_id, request.tiktok_username)
        if existing:
            logger.info(f"Profile already exists for {request.user_name}-{request.tiktok_username}")
            return
        await core_client.create_tiktok_profile(request.user_id, request.tiktok_username, path)
        logger.info(f"Profile created for {request.user_name}-{request.tiktok_username}")
    except Exception as e:
        logger.warning(f"Failed to save profile: {e}")


async def _do_fulfill(request: FulfillRequest, core_client: CoreClient, settings) -> FulfillResult:
    profile = profile_path(
        settings.profile_dir,
        profile_name(request.user_name, request.tiktok_username),
    )

    await core_client.update_order(request.order_id, {
        "fulfillmentPhase": "LaunchingBrowser",
    })

    browser = await launch_browser(profile)

    try:
        tab = await browser.get(SELECTORS["login_url"])

        logged_in = await check_logged_in(tab)
        if not logged_in:
            logged_in = await qr_login(tab, core_client, request.order_id, settings.qr_timeout_minutes)
            if not logged_in:
                screenshot = await take_screenshot(tab, settings.screenshot_dir, request.order_id)
                return FulfillResult(
                    success=False,
                    failure_category="QrScanTimeout",
                    failure_reason="QR scan timeout",
                    screenshot_path=screenshot,
                )

        await _save_profile(core_client, request, profile)

        await core_client.update_order(request.order_id, {
            "fulfillmentPhase": "PurchasingCoins",
        })

        tab = await browser.get(SELECTORS["recharge_url"])
        await human_sleep(3, 5)

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
            )

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

        iframe_visible = await wait_for_element(tab, 'iframe[src*="pipopay"]', timeout=10)
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

        result = await wait_for_payment_result(browser, tab, timeout_seconds=60)
        screenshot = await take_screenshot(tab, settings.screenshot_dir, request.order_id)

        if is_payment_success(result):
            logger.info(f"Payment SUCCESS for order {request.order_id}")
            return FulfillResult(
                success=True,
                screenshot_path=screenshot,
                fulfillment_phase="Done",
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
                reason = f"Payment failed: {error_code or result.get('payment_status', 'unknown')}"

            logger.warning(f"Payment FAILED for order {request.order_id}: {reason}")
            return FulfillResult(
                success=False,
                failure_category=category,
                failure_reason=reason,
                screenshot_path=screenshot,
                fulfillment_phase="Done",
            )

    finally:
        try:
            browser.stop()
        except Exception:
            pass
