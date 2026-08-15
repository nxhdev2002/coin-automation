import asyncio
import re
from loguru import logger

from .browser import human_sleep, parse_eval
from .selectors import SELECTORS


async def detect_verification_prompt(tab) -> str | None:
    """Check if TikTok shows the 'Verify it's really you' dialog after QR scan.

    Returns the masked verification target (e.g. 'x***c@gmail.com') or None.
    """
    js = """
    (() => {
        const dialog = document.querySelector('[data-testid="tux-web-modal"]');
        if (!dialog) return null;
        return dialog.innerText || '';
    })()
    """
    try:
        result = await tab.evaluate(js)
        result = parse_eval(result)
        if not result:
            return None

        text = str(result).strip()
        logger.info(f"[Verify] Dialog text: {text[:200]}")

        email_match = re.search(r'([a-zA-Z0-9.*_-]+@[a-zA-Z0-9.*_-]+)', text)
        if email_match:
            target = email_match.group(1)
            logger.info(f"[Verify] Detected email: {target}")
            return target

        phone_match = re.search(r'\*+\d{2,4}', text)
        if phone_match:
            target = phone_match.group(0)
            logger.info(f"[Verify] Detected phone: {target}")
            return target

        if 'verify' in text.lower() or 'identity' in text.lower():
            logger.warning(f"[Verify] Dialog found but no email/phone extracted: {text[:200]}")
            return 'unknown'

        return None
    except Exception as e:
        logger.warning(f"detect_verification_prompt error: {e}")
        return None


async def click_verification_option(tab, target: str | None = None) -> bool:
    """Click the email/phone option in the verify dialog to reveal the code input.

    If `target` is provided, clicks the option whose text contains that target
    (e.g. 'x***c@gmail.com'). Otherwise clicks the first clickable option.
    """
    target_js = target.replace("'", "\\'") if target else ""
    js = f"""
    (() => {{
        const dialog = document.querySelector('[data-testid="tux-web-modal"]');
        if (!dialog) return false;
        const items = dialog.querySelectorAll('div[class*="pc-home-item-"]');
        const target = '{target_js}';
        for (const item of items) {{
            if (window.getComputedStyle(item).cursor !== 'pointer') continue;
            if (target && !item.innerText.includes(target)) continue;
            item.click();
            return true;
        }}
        return false;
    }})()
    """
    try:
        result = await tab.evaluate(js)
        result = parse_eval(result)
        logger.info(f"[Verify] Click option result: {result}")
        return bool(result)
    except Exception as e:
        logger.error(f"click_verification_option error: {e}")
        return False


async def fill_verification_code(tab, code: str) -> bool:
    """Fill the 6-digit verification code and click Next."""
    js_fill = f"""
    (() => {{
        const dialog = document.querySelector('[data-testid="tux-web-modal"]');
        if (!dialog) return 'no dialog';
        const input = dialog.querySelector('input[placeholder*="6-digit" i]');
        if (!input) return 'no input';
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;
        setter.call(input, '{code}');
        input.dispatchEvent(new Event('input', {{bubbles: true}}));
        input.dispatchEvent(new Event('change', {{bubbles: true}}));
        input.dispatchEvent(new Event('blur', {{bubbles: true}}));
        return 'filled';
    }})()
    """
    try:
        result = await tab.evaluate(js_fill)
        result = parse_eval(result)
        logger.info(f"[Verify] Fill code result: {result}")
        if result != 'filled':
            return False
    except Exception as e:
        logger.error(f"[Verify] Fill code error: {e}")
        return False

    await human_sleep(1, 2)

    js_click = """
    (() => {
        const dialog = document.querySelector('[data-testid="tux-web-modal"]');
        if (!dialog) return false;
        const buttons = dialog.querySelectorAll('button');
        for (const btn of buttons) {
            if (btn.innerText.trim() === 'Next' && !btn.disabled) {
                btn.click();
                return true;
            }
        }
        return false;
    })()
    """
    try:
        clicked = await tab.evaluate(js_click)
        clicked = parse_eval(clicked)
        logger.info(f"[Verify] Next button clicked: {clicked}")
        return bool(clicked)
    except Exception as e:
        logger.error(f"[Verify] Click Next error: {e}")
        return False


async def wait_for_verification_resolved(tab, timeout: float = 120.0) -> bool:
    """Wait until the verification dialog disappears (login succeeded)."""
    js = """
    (() => {
        const dialog = document.querySelector('[data-testid="tux-web-modal"]');
        if (!dialog) return 'no_dialog';
        const text = dialog.innerText || '';
        if (text.includes('incorrect') || text.includes('invalid') || text.includes('wrong') || text.includes('expired')) {
            return 'error:' + text.slice(0, 200);
        }
        return 'still_verifying';
    })()
    """
    elapsed = 0.0
    while elapsed < timeout:
        try:
            result = await tab.evaluate(js)
            result = parse_eval(result)
            if result == 'no_dialog':
                logger.info("[Verify] Dialog disappeared — verification succeeded")
                return True
            if isinstance(result, str) and result.startswith('error:'):
                logger.error(f"[Verify] Verification error: {result}")
                return False
        except Exception as e:
            logger.warning(f"[Verify] Poll error: {e}")
        await asyncio.sleep(1)
        elapsed += 1
    logger.warning("[Verify] Timeout waiting for dialog to close")
    return False
