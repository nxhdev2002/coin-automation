import asyncio
from loguru import logger

from .browser import wait_for_element, click_element_js, parse_eval
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

    await asyncio.sleep(1)

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

    await asyncio.sleep(2)

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
    await asyncio.sleep(4)
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
    await asyncio.sleep(3)
    return bool(result)


async def skip_link_card_prompt(tab) -> bool:
    """
    Ensure the 'Link this card for faster checkout' toggle (data-e2e='payment-method-save-button')
    is UNCHECKED. This prevents the system card from being saved to the customer's TikTok account.
    Triple-checks to guarantee safety.
    """
    selector = '[data-e2e="payment-method-save-button"]'

    if not await wait_for_element(tab, selector, timeout=5):
        logger.info("[SaveCard] Toggle not found — no save-card option on this page")
        return True

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
        await asyncio.sleep(1)
        result = parse_eval(await tab.evaluate(check_js)) or {}

        if not result.get('found'):
            logger.info(f"[SaveCard] Attempt {attempt}: element not found — safe")
            return True

        checked = result.get('checked')
        logger.info(f"[SaveCard] Attempt {attempt}: checked={checked}")

        if not checked:
            logger.info(f"[SaveCard] Confirmed UNCHECKED on attempt {attempt}")
            return True

        logger.warning(f"[SaveCard] Toggle is CHECKED on attempt {attempt} — clicking to uncheck...")
        click_result = await tab.evaluate(uncheck_js)
        logger.info(f"[SaveCard] Click result: {click_result}")
        await asyncio.sleep(0.5)

    final = parse_eval(await tab.evaluate(check_js)) or {}
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
