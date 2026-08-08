import asyncio
import time
from datetime import datetime
from pathlib import Path

from loguru import logger
import nodriver as uc

from .browser import wait_for_element, click_element_js
from .selectors import SELECTORS


async def _find_pipopay_session(browser):
    """Find pipopay iframe target and attach to it. Returns session_id or None."""
    targets = await browser.send(uc.cdp.target.get_targets())
    pipopay = [t for t in targets if t.url and "pipopay" in t.url]
    if not pipopay:
        return None
    session_id = await browser.send(uc.cdp.target.attach_to_target(
        target_id=pipopay[0].target_id,
        flatten=True,
    ))
    return str(session_id)


async def fill_card_form(browser, tab, card_number: str, card_cvv: str,
                          card_expiry: str, card_holder: str) -> bool:
    """Fill the card form inside the pipopay iframe using CDP Target API."""
    session_id = await _find_pipopay_session(browser)
    if not session_id:
        logger.warning("Pipopay iframe target not found")
        return False
    logger.info(f"Pipopay session attached: {session_id}")

    js_fill = f"""
    (() => {{
        const setVal = (el, val) => {{
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            setter.call(el, val);
            el.dispatchEvent(new Event('input', {{bubbles: true}}));
            el.dispatchEvent(new Event('change', {{bubbles: true}}));
            el.dispatchEvent(new Event('blur', {{bubbles: true}}));
        }};
        const inputs = document.querySelectorAll('input');
        let filled = [];
        for (const inp of inputs) {{
            const name = (inp.name || '').toLowerCase();
            const ph = (inp.placeholder || '').toLowerCase();
            if (name.includes('card_number') || ph.includes('card number')) {{
                setVal(inp, '{card_number}');
                filled.push('card_number');
            }} else if (name.includes('cvv') || ph.includes('cvv') || ph.includes('cvc')) {{
                setVal(inp, '{card_cvv}');
                filled.push('cvv');
            }} else if (name.includes('holder') || ph.includes('cardholder')) {{
                setVal(inp, '{card_holder}');
                filled.push('holder_name');
            }} else if (name.includes('expiration') || ph.includes('mm/yy')) {{
                setVal(inp, '{card_expiry}');
                filled.push('expiry');
            }}
        }}
        return 'filled: ' + filled.join(',');
    }})()
    """
    result = await browser.send(uc.cdp.runtime.evaluate(
        expression=js_fill,
        return_by_value=True,
    ), sessionId=session_id)
    remote_obj, _ = result
    value = remote_obj.value if remote_obj else None
    logger.info(f"Card form fill result: {value}")
    return "filled:" in str(value) and "card_number" in str(value)


async def click_pay_now(tab) -> bool:
    selector = SELECTORS["cashier_footer_button"]
    if not await wait_for_element(tab, selector, timeout=10):
        logger.warning("Pay now button not found")
        return False

    is_disabled = await tab.evaluate("""
        (() => {
            const b = document.querySelector('[data-e2e="cashier-footer-button"]');
            return b ? b.disabled : true;
        })()
    """)
    if is_disabled:
        logger.warning("Pay now button is disabled")
        return False

    ok = await click_element_js(tab, selector)
    logger.info(f"Pay now clicked: {ok}")
    return ok


async def wait_for_payment_result(browser, tab, timeout_seconds: int = 30) -> dict:
    """Wait for payment result. Check URL redirect + error messages in pipopay iframe + main page."""
    logger.info(f"Waiting for payment result (timeout {timeout_seconds}s)")
    start = time.time()

    while time.time() - start < timeout_seconds:
        await asyncio.sleep(2)

        # 1. Check URL redirect to /coin/end-result
        url = await tab.evaluate("location.href")
        if isinstance(url, str) and "/coin/end-result" in url:
            logger.info("Payment result URL detected")
            return parse_end_result_url(url)

        # 2. Check for error messages on the main page (cashier modal)
        main_error = await tab.evaluate("""
            (() => {
                const body = document.body.innerText || '';
                const keywords = ['not supported', 'invalid', 'declined', 'failed',
                                   'error', 'rejected', 'insufficient', 'expired'];
                for (const kw of keywords) {
                    if (body.toLowerCase().includes(kw)) {
                        const lines = body.split('\\n').filter(l => 
                            l.toLowerCase().includes(kw));
                        if (lines.length > 0) return lines[0].trim().slice(0, 200);
                    }
                }
                return null;
            })()
        """)
        if main_error:
            logger.info(f"Main page error detected: {main_error}")
            return {"payment_status": "failed", "error_code": "CARD_ERROR",
                    "message": str(main_error)}

        # 3. Check for error messages inside pipopay iframe via CDP
        session_id = await _find_pipopay_session(browser)
        if session_id:
            iframe_error = await browser.send(uc.cdp.runtime.evaluate(
                expression="""
                (() => {
                    const body = document.body.innerText || '';
                    const keywords = ['not supported', 'invalid', 'declined', 'failed',
                                       'error', 'rejected', 'insufficient', 'expired',
                                       'security reasons'];
                    for (const kw of keywords) {
                        if (body.toLowerCase().includes(kw)) {
                            const lines = body.split('\\n').filter(l =>
                                l.toLowerCase().includes(kw));
                            if (lines.length > 0) return lines[0].trim().slice(0, 200);
                        }
                    }
                    return null;
                })()
                """,
                return_by_value=True,
            ), sessionId=session_id)
            remote_obj, _ = iframe_error if iframe_error else (None, None)
            iframe_msg = remote_obj.value if remote_obj else None
            if iframe_msg:
                logger.info(f"Pipopay iframe error detected: {iframe_msg}")
                return {"payment_status": "failed", "error_code": "CARD_ERROR",
                        "message": str(iframe_msg)}

    logger.warning("Payment result timeout")
    return {"payment_status": "timeout", "error_code": "TIMEOUT"}


def parse_end_result_url(url: str) -> dict:
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    return {
        "payment_status": params.get("payment_status", ["unknown"])[0],
        "error_code": params.get("error_code", [""])[0],
        "message": params.get("message", [""])[0],
        "order_id": params.get("order_id", [""])[0],
        "charge_id": params.get("charge_id", [""])[0],
        "pay_method": params.get("pay_method", [""])[0],
        "is_redirect": params.get("is_redirect", ["0"])[0],
    }


async def take_screenshot(tab, screenshot_dir: str, order_id: str) -> str:
    path = Path(screenshot_dir) / f"{order_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        await tab.save_screenshot(str(path))
        logger.info(f"Screenshot saved: {path}")
        return str(path)
    except Exception as e:
        logger.warning(f"Screenshot failed: {e}")
        return ""
