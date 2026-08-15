import asyncio
import json
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


async def detect_verification_options(tab) -> list[str] | None:
    """Extract all available verification options from the TikTok dialog.

    Returns a list of option labels (e.g. ['+84****9741', 'Password']) or None.
    Uses semantic class prefixes (pc-home-item-desc, pc-home-item-sub-desc) which
    are stable across TikTok rebuilds, unlike the hashed suffixes.
    """
    js = """
    (() => {
        const dialog = document.querySelector('[data-testid="tux-web-modal"]');
        if (!dialog) return null;
        const descs = dialog.querySelectorAll('[class*="pc-home-item-desc-"]:not([class*="sub-desc"])');
        const options = [];
        for (const desc of descs) {
            const label = (desc.innerText || '').trim();
            const content = desc.closest('[class*="pc-home-item-content-"]');
            const subDesc = content ? content.querySelector('[class*="pc-home-item-sub-desc-"]') : null;
            const value = subDesc ? (subDesc.innerText || '').trim() : '';
            const text = value || label;
            if (text) options.push(text);
        }
        return options.length ? JSON.stringify(options) : null;
    })()
    """
    try:
        result = await tab.evaluate(js)
        result = parse_eval(result)
        logger.info(f"[Verify] detect_verification_options raw result: {result}")
        if not result:
            return None

        if isinstance(result, str):
            raw_options = json.loads(result)
        elif isinstance(result, list):
            raw_options = result
        else:
            logger.warning(f"[Verify] Unexpected result type: {type(result)}")
            return None
        targets = []
        for text in raw_options:
            email_match = re.search(r'([a-zA-Z0-9.*_-]+@[a-zA-Z0-9.*_-]+)', text)
            if email_match:
                targets.append(email_match.group(1))
                continue
            phone_match = re.search(r'(\+?\d*\*+\d+)', text)
            if phone_match:
                targets.append(phone_match.group(1))
                continue
            if text:
                targets.append(text)

        if not targets:
            logger.warning(f"[Verify] Options found but no targets extracted: {raw_options}")
            return None

        logger.info(f"[Verify] Detected verification options: {targets}")
        return targets
    except Exception as e:
        logger.warning(f"detect_verification_options error: {e}")
        return None


async def click_verification_option(tab, target: str | None = None) -> bool:
    """Click the email/phone/password option in the verify dialog to reveal the code input.

    If `target` is provided, clicks the option whose text contains that target
    (e.g. '+84****9741', 'Password'). Otherwise clicks the first option.
    """
    target_js = target.replace("'", "\\'") if target else ""
    js = f"""
    (() => {{
        const dialog = document.querySelector('[data-testid="tux-web-modal"]');
        if (!dialog) return false;
        const descs = dialog.querySelectorAll('[class*="pc-home-item-desc-"]:not([class*="sub-desc"])');
        const target = '{target_js}';
        for (const desc of descs) {{
            let item = desc.parentElement;
            while (item && item !== dialog) {{
                const cls = item.className || '';
                if (cls.includes('pc-home-item-') && !cls.includes('content') && !cls.includes('icon') && !cls.includes('arrow')) {{
                    break;
                }}
                item = item.parentElement;
            }}
            if (!item || item === dialog) item = desc;
            const fullText = (item.innerText || '').trim();
            if (target && !fullText.includes(target)) continue;
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


async def fill_verification_code(tab, code: str, is_password: bool = False) -> bool:
    """Fill the verification code/password and click submit.

    If is_password=True, looks for a password input. Otherwise looks for a 6-digit OTP input.
    """
    input_selector = 'input[type="password"]' if is_password else 'input[placeholder*="6-digit" i]'
    submit_texts = ['Next', 'Log in', 'Submit', 'Continue'] if is_password else ['Next']
    code_escaped = code.replace("'", "\\'")
    js_fill = f"""
    (() => {{
        const dialog = document.querySelector('[data-testid="tux-web-modal"]');
        if (!dialog) return 'no dialog';
        const input = dialog.querySelector(`{input_selector}`);
        if (!input) return 'no input';
        const setter = Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype, 'value'
        ).set;
        setter.call(input, '{code_escaped}');
        input.dispatchEvent(new Event('input', {{bubbles: true}}));
        input.dispatchEvent(new Event('change', {{bubbles: true}}));
        input.dispatchEvent(new Event('blur', {{bubbles: true}}));
        return 'filled';
    }})()
    """
    try:
        result = await tab.evaluate(js_fill)
        result = parse_eval(result)
        logger.info(f"[Verify] Fill {'password' if is_password else 'code'} result: {result}")
        if result != 'filled':
            return False
    except Exception as e:
        logger.error(f"[Verify] Fill error: {e}")
        return False

    await human_sleep(1, 2)

    submit_array = json.dumps(submit_texts)
    js_click = f"""
    (() => {{
        const dialog = document.querySelector('[data-testid="tux-web-modal"]');
        if (!dialog) return false;
        const texts = {submit_array};
        const buttons = dialog.querySelectorAll('button');
        for (const btn of buttons) {{
            const text = btn.innerText.trim();
            for (const t of texts) {{
                if (text === t && !btn.disabled) {{
                    btn.click();
                    return true;
                }}
            }}
        }}
        return false;
    }})()
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
            if "no close frame" in str(e).lower() or "target closed" in str(e).lower() or "session closed" in str(e).lower():
                logger.error(f"[Verify] Tab/browser closed: {e}")
                return False
            logger.warning(f"[Verify] Poll error: {e}")
        await asyncio.sleep(1)
        elapsed += 1
    logger.warning("[Verify] Timeout waiting for dialog to close")
    return False
