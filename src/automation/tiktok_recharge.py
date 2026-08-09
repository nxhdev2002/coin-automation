import asyncio
from loguru import logger

from .browser import wait_for_element, click_element_js
from .selectors import SELECTORS


PACKAGE_MAP = {
    30: 0, 40: 1, 50: 2, 70: 3,
    90: 4, 150: 5, 200: 6,
}


async def select_package(tab, coin_amount: int) -> bool:
    idx = PACKAGE_MAP.get(coin_amount)
    if idx is None:
        logger.error(f"Unknown coin amount: {coin_amount}")
        return False

    selector = f'[data-e2e="wallet-package-{idx}"]'
    if not await wait_for_element(tab, selector, timeout=10):
        logger.warning(f"Package {idx} not found, trying selected")
        selector = SELECTORS["wallet_package_selected"]

    ok = await click_element_js(tab, selector)
    if not ok:
        ok = await click_element_js(tab, SELECTORS["wallet_package_selected"])

    await asyncio.sleep(1)
    logger.info(f"Selected package {coin_amount} coins (index {idx})")
    return ok


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
    Ensure the 'Save card' toggle (data-e2e='payment-method-save-button') is UNCHECKED.
    This prevents the system card from being saved to the customer's TikTok account.
    Triple-checks to guarantee safety.
    """
    selector = '[data-e2e="payment-method-save-button"]'

    def build_check_js():
        return """
        (() => {
            const el = document.querySelector('[data-e2e="payment-method-save-button"]');
            if (!el) return { found: false, checked: null };

            // Determine checked state via multiple signals
            const ariaChecked = el.getAttribute('aria-checked');
            const checkbox = el.querySelector('input[type="checkbox"]');
            const inputChecked = checkbox ? checkbox.checked : null;
            const classChecked = el.className.toLowerCase().includes('checked')
                || el.className.toLowerCase().includes('selected')
                || el.className.toLowerCase().includes('active');

            const isChecked = ariaChecked === 'true'
                || inputChecked === true
                || (ariaChecked === null && inputChecked === null && classChecked);

            return { found: true, checked: isChecked, ariaChecked, inputChecked, classChecked, classes: el.className };
        })()
        """

    def build_uncheck_js():
        return """
        (() => {
            const el = document.querySelector('[data-e2e="payment-method-save-button"]');
            if (!el) return 'not-found';
            el.scrollIntoView({block: 'center'});
            el.click();
            return 'clicked';
        })()
        """

    for attempt in range(1, 4):
        await asyncio.sleep(1)
        result = await tab.evaluate(build_check_js())

        if not result or not result.get('found'):
            logger.info(f"[SaveCard] Attempt {attempt}: element not found — likely no save-card toggle on this page")
            return True

        checked = result.get('checked', True)
        classes = result.get('classes', '')
        logger.info(f"[SaveCard] Attempt {attempt}: checked={checked} classes={classes}")

        if not checked:
            logger.info(f"[SaveCard] Confirmed UNCHECKED on attempt {attempt}")
            return True

        logger.warning(f"[SaveCard] Save card toggle is CHECKED on attempt {attempt} — unchecking...")
        click_result = await tab.evaluate(build_uncheck_js())
        logger.info(f"[SaveCard] Click result: {click_result}")
        await asyncio.sleep(0.5)

    # Final verification after 3 attempts
    result = await tab.evaluate(build_check_js())
    if result and result.get('found') and not result.get('checked'):
        logger.info("[SaveCard] Confirmed UNCHECKED after retries")
        return True

    logger.error("[SaveCard] FAILED to uncheck save-card toggle after 3 attempts — ABORTING to protect customer account")
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
