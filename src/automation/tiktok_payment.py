import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

from loguru import logger
import nodriver as uc

from .browser import wait_for_element, click_element_js, human_sleep
from .selectors import SELECTORS

REQUIRED_CARD_FIELDS = {"card_number", "cvv", "holder_name", "expiry"}


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


async def _pipopay_card_input_ready(browser, session_id) -> bool:
    """Check whether the card number input has rendered inside the pipopay session."""
    result = await browser.send(uc.cdp.runtime.evaluate(
        expression="""
        (() => {
            const inputs = document.querySelectorAll('input');
            for (const inp of inputs) {
                const name = (inp.name || '').toLowerCase();
                const ph = (inp.placeholder || '').toLowerCase();
                if (name.includes('card_number') || ph.includes('card number')) return true;
            }
            return false;
        })()
        """,
        return_by_value=True,
    ), sessionId=session_id)
    remote_obj, _ = result
    return bool(remote_obj.value) if remote_obj else False


async def _wait_for_pipopay_ready(browser, timeout_seconds: float = 15, poll_interval: float = 0.5):
    """Poll until the pipopay target exists AND its card_number input has actually rendered.

    The outer <iframe> element can report a non-zero bounding box before the
    cross-origin pipopay document has finished loading, so a one-shot lookup
    here is racy and intermittently fails to find anything to fill.
    """
    elapsed = 0.0
    while elapsed < timeout_seconds:
        session_id = await _find_pipopay_session(browser)
        if session_id:
            try:
                if await _pipopay_card_input_ready(browser, session_id):
                    return session_id
            except Exception:
                pass
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return None


async def fill_card_form(browser, tab, card_number: str, card_cvv: str,
                          card_expiry: str, card_holder: str) -> bool:
    """Fill the card form inside the pipopay iframe using CDP Target API."""
    session_id = await _wait_for_pipopay_ready(browser)
    if not session_id:
        logger.warning("Pipopay iframe target/card inputs not found")
        return False
    logger.info(f"Pipopay session attached: {session_id}")

    values_json = json.dumps({
        "card_number": card_number,
        "cvv": card_cvv,
        "holder_name": card_holder,
        "expiry": card_expiry,
    })
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
        const values = {values_json};
        const inputs = document.querySelectorAll('input');
        let filled = [];
        for (const inp of inputs) {{
            const name = (inp.name || '').toLowerCase();
            const ph = (inp.placeholder || '').toLowerCase();
            if (name.includes('card_number') || ph.includes('card number')) {{
                setVal(inp, values.card_number);
                filled.push('card_number');
            }} else if (name.includes('cvv') || ph.includes('cvv') || ph.includes('cvc')) {{
                setVal(inp, values.cvv);
                filled.push('cvv');
            }} else if (name.includes('holder') || ph.includes('cardholder')) {{
                setVal(inp, values.holder_name);
                filled.push('holder_name');
            }} else if (name.includes('expiration') || ph.includes('mm/yy')) {{
                setVal(inp, values.expiry);
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

    await human_sleep(1, 3)

    prefix = "filled: "
    filled_fields = set(str(value)[len(prefix):].split(",")) if str(value).startswith(prefix) else set()
    missing = REQUIRED_CARD_FIELDS - filled_fields
    if missing:
        logger.warning(f"Card form incomplete — missing fields: {sorted(missing)}")
        return False
    return True


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


# TikTok's cashier reports a completed charge as `payment_status=succeed` on the
# end-result URL — not "success". Accept every spelling seen so a card that was
# actually charged is never recorded as a failed order.
PAYMENT_SUCCESS_STATUSES = frozenset({"success", "succeed", "succeeded"})


def is_payment_success(result: dict) -> bool:
    return str(result.get("payment_status", "")).strip().lower() in PAYMENT_SUCCESS_STATUSES


async def wait_for_payment_result(browser, tab, timeout_seconds: int = 60) -> dict:
    """Wait for payment result. Check URL redirect + error messages in pipopay iframe + main page."""
    logger.info(f"Waiting for payment result (timeout {timeout_seconds}s)")
    start = time.time()

    while time.time() - start < timeout_seconds:
        await asyncio.sleep(2)

        # 1. Check URL redirect to /coin/end-result
        url = await tab.evaluate("location.href")
        if isinstance(url, str) and "/coin/end-result" in url:
            parsed = parse_end_result_url(url)
            logger.info(f"Payment result URL detected: {parsed}")
            return parsed

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
