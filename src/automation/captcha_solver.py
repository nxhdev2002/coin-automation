import asyncio
import json

from loguru import logger

from .browser import parse_eval


CAPTCHA_SELECTORS = [
    {"sel": '#captcha-verify-container-main-page', "type": "slider"},
    {"sel": '.captcha-verify-container', "type": "slider"},
    {"sel": '#captcha_slide_button', "type": "slider"},
    {"sel": '.secsdk-captcha-drag-icon', "type": "slider"},
    {"sel": 'div[class*="captcha-verify"]', "type": "slider"},
    {"sel": 'div[class*="secsdk-captcha"]', "type": "slider"},
    {"sel": 'button[class*="secsdk-captcha"]', "type": "slider"},
    {"sel": 'div[class*="captcha"]', "type": "unknown"},
    {"sel": 'div[class*="geetest"]', "type": "geetest"},
    {"sel": 'div[class*="slider"]', "type": "slider"},
    {"sel": 'iframe[src*="captcha"]', "type": "unknown"},
    {"sel": 'div[class*="tcaptcha"]', "type": "unknown"},
]

DETECT_JS = """
(() => {
    const checks = %s;
    for (const c of checks) {
        const el = document.querySelector(c.sel);
        if (el) {
            const r = el.getBoundingClientRect();
            if (r.width > 50 && r.height > 50) {
                return {type: c.type, x: r.x, y: r.y, w: r.width, h: r.height, selector: c.sel};
            }
        }
    }
    return null;
})()
""" % json.dumps(CAPTCHA_SELECTORS)

CAPTCHA_STILL_PRESENT_JS = """
(() => {
    const checks = %s;
    for (const c of checks) {
        const el = document.querySelector(c.sel);
        if (el) {
            const r = el.getBoundingClientRect();
            if (r.width > 50 && r.height > 50) return true;
        }
    }
    return false;
})()
""" % json.dumps(CAPTCHA_SELECTORS)


async def detect_captcha(tab) -> dict | None:
    try:
        result = await tab.evaluate(DETECT_JS)
        result = parse_eval(result)
        if not result:
            return None
        if isinstance(result, dict) and result.get("type"):
            logger.info(
                f"[CAPTCHA] detect: type={result['type']}, "
                f"selector={result.get('selector')}, "
                f"rect={{x:{result.get('x')},y:{result.get('y')},"
                f"w:{result.get('w')},h:{result.get('h')}}}"
            )
            return result
        return None
    except Exception as e:
        logger.warning(f"[CAPTCHA] detect error: {e}")
        return None


async def _captcha_still_present(tab) -> bool:
    try:
        result = await tab.evaluate(CAPTCHA_STILL_PRESENT_JS)
        return bool(result)
    except Exception:
        return False


async def solve_captcha(tab, settings) -> dict | None:
    captcha_info = await detect_captcha(tab)
    if not captcha_info:
        return None

    captcha_type = captcha_info.get("type", "unknown")

    # If SadCaptcha extension is loaded, just wait for it to auto-solve
    sadcaptcha_key = getattr(settings, "sadcaptcha_api_key", "")
    if sadcaptcha_key:
        logger.info(f"[CAPTCHA] SadCaptcha extension active — waiting for auto-solve (timeout=120s)")
        for wait in range(120):
            await asyncio.sleep(1)
            still = await _captcha_still_present(tab)
            if not still:
                logger.info(f"[CAPTCHA] SOLVED by SadCaptcha extension after {wait+1}s")
                return {"solved": True, "cost": 0.0, "type": captcha_type, "attempts": 1, "method": "sadcaptcha"}
        logger.warning("[CAPTCHA] SadCaptcha extension timeout — captcha not solved")
        return {"solved": False, "cost": 0.0, "type": captcha_type, "attempts": 1, "method": "sadcaptcha"}

    logger.warning("[CAPTCHA] No SadCaptcha key — captcha cannot be solved")
    return {"solved": False, "cost": 0.0, "type": captcha_type, "attempts": 0, "method": "none"}
