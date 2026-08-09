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
    """Dismiss any 'Link card to account' / 'Save card' prompt that appears after selecting Add Card."""
    await asyncio.sleep(2)
    js = """
    (() => {
        const clickByText = (keywords) => {
            const btns = document.querySelectorAll('button, a, [role="button"]');
            for (const b of btns) {
                const txt = (b.textContent || '').toLowerCase().trim();
                for (const kw of keywords) {
                    if (txt.includes(kw)) { b.click(); return kw; }
                }
            }
            return null;
        };
        let clicked = clickByText(['not now', 'skip', 'cancel', 'close', 'later', 'không']);
        if (clicked) return 'clicked: ' + clicked;
        const closeBtn = document.querySelector('[data-e2e="modal-close"], [aria-label="Close"], [class*="close"]');
        if (closeBtn) { closeBtn.click(); return 'clicked: close-btn'; }
        const checkbox = document.querySelector('input[type="checkbox"][class*="save"], input[type="checkbox"][class*="link"]');
        if (checkbox && checkbox.checked) { checkbox.click(); return 'unchecked save-card'; }
        return 'no prompt found';
    })()
    """
    result = await tab.evaluate(js)
    logger.info(f"Skip link card prompt: {result}")
    await asyncio.sleep(1)
    return True


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
