import asyncio
from loguru import logger

from .browser import wait_for_element, click_element_js, parse_eval, human_sleep
from .selectors import SELECTORS


async def select_custom_package(tab, coin_amount: int) -> bool:
    """Select the Custom package option and enter a custom coin amount (min 30)."""
    custom_selector = SELECTORS["wallet_package_custom"]
    if not await wait_for_element(tab, custom_selector, timeout=10):
        logger.warning("Custom package option not found")
        return False

    ok = await click_element_js(tab, custom_selector)
    if not ok:
        logger.warning("Could not click Custom package option")
        return False

    await human_sleep(1, 3)

    js_fill = f"""
    (() => {{
        const input = document.querySelector('[data-e2e="wallet-package-coin-custom-input-box"]');
        if (!input) return 'input not found';
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(input, '{coin_amount}');
        input.dispatchEvent(new Event('input', {{bubbles: true}}));
        input.dispatchEvent(new Event('change', {{bubbles: true}}));
        input.dispatchEvent(new Event('blur', {{bubbles: true}}));
        return 'filled: ' + input.value;
    }})()
    """
    result = await tab.evaluate(js_fill)
    logger.info(f"Custom package filled: {result}")

    if "input not found" in str(result):
        return False

    await human_sleep(2, 4)

    js_check = """
    (() => {
        const btn = document.querySelector('[data-e2e="wallet-buy-now-button"]');
        if (!btn) return { found: false };
        const body = document.body.innerText || '';
        const minMatch = body.match(/Minimum:\\s*(\\d+)/i);
        return {
            found: true,
            disabled: btn.disabled,
            minError: minMatch ? minMatch[0] : null
        };
    })()
    """
    raw = await tab.evaluate(js_check)
    logger.info(f"Recharge button check: {raw}")

    check = parse_eval(raw) or {}

    if check.get("minError"):
        logger.error(f"Coin amount below minimum: {check['minError']}")
        return False

    if check.get("disabled"):
        logger.warning("Recharge button still disabled after custom amount")
        return False

    logger.info(f"Custom package selected: {coin_amount} coins")
    return True


async def click_recharge(tab) -> bool:
    selector = SELECTORS["wallet_buy_now_button"]
    if not await wait_for_element(tab, selector, timeout=10):
        logger.warning("Recharge button not found")
        return False

    is_disabled = await tab.evaluate("""
        (() => {
            const b = document.querySelector('[data-e2e="wallet-buy-now-button"]');
            return b ? b.disabled : true;
        })()
    """)
    if is_disabled:
        logger.warning("Recharge button is disabled")
        return False

    ok = await click_element_js(tab, selector)
    await human_sleep(3, 5)
    logger.info(f"Recharge button clicked: {ok}")
    return ok


async def select_add_card(tab) -> bool:
    selector = SELECTORS["payment_method_item_ccdc"]
    if not await wait_for_element(tab, selector, timeout=10):
        logger.warning("Add card option not found")
        return False

    js = """
    (() => {
        const el = document.querySelector('[data-e2e="payment-method-item-ccdc"]');
        if (!el) return false;
        el.scrollIntoView({block: 'center'});
        el.click();
        const r = el.querySelector('input[type="radio"]');
        if (r) r.click();
        return true;
    })()
    """
    result = await tab.evaluate(js)
    logger.info(f"Add card selection: {result}")
    await human_sleep(2, 4)
    return bool(result)


async def skip_link_card_prompt(tab) -> bool:
    """
    Ensure the 'Link this card for faster checkout' toggle is UNCHECKED — but only
    when the selected payment method is CC/DC (data-e2e='payment-method-item-ccdc').

    STRICT: if anything cannot be confirmed, return False (fail order).
    Never proceed unless we are 100% sure the toggle is unchecked.
    """
    ccdc_selector = '[data-e2e="payment-method-item-ccdc"]'

    if not await wait_for_element(tab, ccdc_selector, timeout=5):
        logger.error("[SaveCard] CC/DC payment method not found — FAIL")
        return False

    ccdc_active_js = """
    (() => {
        const el = document.querySelector('[data-e2e="payment-method-item-ccdc"]');
        if (!el) return false;
        const radio = el.querySelector('input[type="radio"]');
        return radio ? radio.checked : false;
    })()
    """
    try:
        ccdc_active = await tab.evaluate(ccdc_active_js)
    except Exception as e:
        logger.error(f"[SaveCard] Failed to check CC/DC active state: {e} — FAIL")
        return False

    if not ccdc_active:
        logger.error("[SaveCard] CC/DC not the selected payment method — FAIL")
        return False

    selector = '[data-e2e="payment-method-save-button"]'

    if not await wait_for_element(tab, selector, timeout=5):
        logger.error("[SaveCard] Save card toggle not found — FAIL")
        return False

    check_js = """
    (() => {
        const el = document.querySelector('[data-e2e="payment-method-save-button"]');
        if (!el) return { found: false, checked: null };
        const input = el.querySelector('input[type="checkbox"]');
        return { found: true, checked: input ? input.checked : null };
    })()
    """

    uncheck_js = """
    (() => {
        const el = document.querySelector('[data-e2e="payment-method-save-button"]');
        if (!el) return 'not-found';
        const input = el.querySelector('input[type="checkbox"]');
        if (input) { input.click(); return 'clicked-input'; }
        el.click(); return 'clicked-div';
    })()
    """

    for attempt in range(1, 4):
        await human_sleep(1, 3)
        try:
            result = parse_eval(await tab.evaluate(check_js)) or {}
        except Exception as e:
            logger.error(f"[SaveCard] Attempt {attempt}: evaluate failed: {e} — FAIL")
            return False

        if not result.get('found'):
            logger.error(f"[SaveCard] Attempt {attempt}: element not found — FAIL")
            return False

        checked = result.get('checked')
        logger.info(f"[SaveCard] Attempt {attempt}: checked={checked}")

        if checked is None:
            logger.error(f"[SaveCard] Attempt {attempt}: cannot determine checked state — FAIL")
            return False

        if not checked:
            logger.info(f"[SaveCard] Confirmed UNCHECKED on attempt {attempt}")
            return True

        logger.warning(f"[SaveCard] Toggle is CHECKED on attempt {attempt} — clicking to uncheck...")
        try:
            click_result = await tab.evaluate(uncheck_js)
        except Exception as e:
            logger.error(f"[SaveCard] Attempt {attempt}: uncheck click failed: {e} — FAIL")
            return False
        logger.info(f"[SaveCard] Click result: {click_result}")
        await human_sleep(1, 2)

    try:
        final = parse_eval(await tab.evaluate(check_js)) or {}
    except Exception as e:
        logger.error(f"[SaveCard] Final check evaluate failed: {e} — FAIL")
        return False

    if not final.get('found'):
        logger.error("[SaveCard] Final check: element not found — FAIL")
        return False

    if final.get('checked') is None:
        logger.error("[SaveCard] Final check: cannot determine checked state — FAIL")
        return False

    if not final.get('checked'):
        logger.info("[SaveCard] Confirmed UNCHECKED after retries")
        return True

    logger.error("[SaveCard] FAILED to uncheck after 3 attempts — ABORTING to protect customer account")
    return False


async def verify_iframe_visible(tab) -> bool:
    js = """
    (() => {
        const f = document.querySelector('iframe[src*="pipopay"]');
        if (!f) return false;
        const b = f.getBoundingClientRect();
        return b.width > 50 && b.height > 50;
    })()
    """
    result = await tab.evaluate(js)
    return bool(result)
