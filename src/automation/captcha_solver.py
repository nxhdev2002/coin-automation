import asyncio
import base64
import json
import os
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

import nodriver as uc

from .browser import parse_eval, human_sleep


CAPTCHA_SELECTORS = [
    {"sel": 'div[class*="captcha"]', "type": "unknown"},
    {"sel": 'div[class*="slider"]', "type": "slider"},
    {"sel": 'div[class*="verify-image"]', "type": "slider"},
    {"sel": 'div[class*="geetest"]', "type": "geetest"},
    {"sel": '[data-e2e="captcha"]', "type": "unknown"},
    {"sel": 'iframe[src*="captcha"]', "type": "unknown"},
    {"sel": 'div[class*="secsdk"]', "type": "unknown"},
    {"sel": 'div[class*="tcaptcha"]', "type": "unknown"},
]

SLIDER_HANDLE_SELECTORS = [
    'div[class*="slider-thumb"]',
    'div[class*="slide-btn"]',
    'div[class*="slider-button"]',
    'div[class*="slider-handle"]',
    'div[class*="captcha-slider"]',
    'span[class*="slider"]',
    'div[role="slider"]',
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


async def _capture_region_screenshot(tab, rect: dict, screenshot_dir: str, order_id: str) -> str:
    x = rect.get("x", 0)
    y = rect.get("y", 0)
    w = rect.get("w", 0)
    h = rect.get("h", 0)

    try:
        clip = uc.cdp.page.Viewport(
            x=x, y=y, width=w, height=h, scale=1
        )
        result = await tab.send(
            uc.cdp.page.capture_screenshot(
                format_="png",
                clip=clip,
                capture_beyond_viewport=True,
            )
        )
        if isinstance(result, str):
            img_b64 = result
        else:
            img_b64 = result if isinstance(result, str) else str(result)
    except Exception as e:
        logger.warning(f"[CAPTCHA] CDP screenshot failed: {e}, trying full page")
        try:
            await tab.save_screenshot(str(Path(screenshot_dir) / f"captcha_full_{order_id}_{int(time.time())}.png"))
        except Exception:
            pass
        return ""

    path = Path(screenshot_dir) / f"captcha_{order_id}_{int(time.time())}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(base64.b64decode(img_b64))
        logger.info(f"[CAPTCHA] screenshot saved: {path}")
    except Exception:
        pass
    return img_b64


async def _capture_full_screenshot(tab, screenshot_dir: str, order_id: str) -> str:
    try:
        result = await tab.send(
            uc.cdp.page.capture_screenshot(
                format_="png",
                capture_beyond_viewport=True,
            )
        )
        if isinstance(result, str):
            img_b64 = result
        else:
            img_b64 = str(result)
    except Exception as e:
        logger.warning(f"[CAPTCHA] full screenshot failed: {e}")
        return ""

    path = Path(screenshot_dir) / f"captcha_full_{order_id}_{int(time.time())}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(base64.b64decode(img_b64))
        logger.info(f"[CAPTCHA] full screenshot saved: {path}")
    except Exception:
        pass
    return img_b64


async def _find_slider_handle(tab) -> dict | None:
    handle_js = """
    (() => {
        const selectors = %s;
        for (const sel of selectors) {
            const el = document.querySelector(sel);
            if (el) {
                const r = el.getBoundingClientRect();
                if (r.width > 5 && r.height > 5) {
                    return {x: r.x + r.width/2, y: r.y + r.height/2, w: r.width, h: r.height, selector: sel};
                }
            }
        }
        return null;
    })()
    """ % json.dumps(SLIDER_HANDLE_SELECTORS)
    try:
        result = await tab.evaluate(handle_js)
        result = parse_eval(result)
        if isinstance(result, dict) and result.get("x") is not None:
            logger.info(f"[CAPTCHA] slider handle found: {result}")
            return result
    except Exception:
        pass
    return None


async def _solve_coordinates(solver, img_b64: str, comment: str) -> dict | None:
    try:
        result = await solver.coordinates(img_b64, comment=comment)
        logger.info(f"[CAPTCHA] 2captcha coordinates result: {result}")
        coords = result.get("coordinates", [])
        if coords and len(coords) > 0:
            pt = coords[0]
            return {"x": pt.get("x", 0), "y": pt.get("y", 0), "cost": float(result.get("cost", 0))}
    except Exception as e:
        logger.error(f"[CAPTCHA] 2captcha coordinates error: {e}")
    return None


async def _solve_geetest(solver, gt: str, challenge: str, url: str) -> dict | None:
    try:
        result = await solver.geetest(gt=gt, challenge=challenge, url=url)
        logger.info(f"[CAPTCHA] 2captcha geetest result: {result}")
        solution = result.get("solution", result)
        return {
            "challenge": solution.get("challenge", ""),
            "validate": solution.get("validate", ""),
            "seccode": solution.get("seccode", ""),
            "cost": float(result.get("cost", 0)),
        }
    except Exception as e:
        logger.error(f"[CAPTCHA] 2captcha geetest error: {e}")
    return None


async def _extract_geetest_params(tab) -> dict | None:
    js = """
    (() => {
        // Try common GeeTest globals
        if (typeof geetest !== 'undefined' && geetest.gt) {
            return {gt: geetest.gt, challenge: geetest.challenge || ''};
        }
        // Scan scripts for gt/challenge
        const scripts = document.querySelectorAll('script[src*="geetest"]');
        for (const s of scripts) {
            const src = s.src || '';
            const gtMatch = src.match(/[?&]gt=([^&]+)/);
            const chMatch = src.match(/[?&]challenge=([^&]+)/);
            if (gtMatch) {
                return {gt: gtMatch[1], challenge: chMatch ? chMatch[1] : ''};
            }
        }
        // Try window.geetestData
        if (typeof window.geetestData === 'object') {
            return {gt: window.geetestData.gt || '', challenge: window.geetestData.challenge || ''};
        }
        return null;
    })()
    """
    try:
        result = await tab.evaluate(js)
        result = parse_eval(result)
        if isinstance(result, dict) and result.get("gt"):
            logger.info(f"[CAPTCHA] GeeTest params: gt={result['gt']}, challenge={result.get('challenge', '')[:20]}")
            return result
    except Exception as e:
        logger.warning(f"[CAPTCHA] extract geetest params error: {e}")
    return None


async def _inject_geetest_solution(tab, solution: dict) -> bool:
    js = """
    (() => {
        // Try common GeeTest callback patterns
        const callbacks = ['geetest_success', 'geetest_callback', 'onGeetestSuccess'];
        for (const cb of callbacks) {
            if (typeof window[cb] === 'function') {
                window[cb](%s);
                return true;
            }
        }
        // Try geetest object
        if (typeof geetest !== 'undefined' && typeof geetest.success === 'function') {
            geetest.success(%s);
            return true;
        }
        return false;
    })()
    """ % (json.dumps(solution), json.dumps(solution))
    try:
        result = await tab.evaluate(js)
        result = parse_eval(result)
        return bool(result)
    except Exception:
        return False


async def solve_slider_captcha(tab, solver, rect: dict, screenshot_dir: str, order_id: str) -> dict:
    img_b64 = await _capture_region_screenshot(tab, rect, screenshot_dir, order_id)
    if not img_b64:
        logger.error("[CAPTCHA] could not capture slider screenshot")
        return {"solved": False, "cost": 0.0, "type": "slider"}

    coords = await _solve_coordinates(
        solver, img_b64,
        "Click on the point where the puzzle piece should be placed"
    )
    if not coords:
        logger.error("[CAPTCHA] 2captcha returned no coordinates for slider")
        return {"solved": False, "cost": 0.0, "type": "slider"}

    target_x = rect.get("x", 0) + coords["x"]
    target_y = rect.get("y", 0) + coords["y"]
    logger.info(f"[CAPTCHA] 2captcha target: x={target_x}, y={target_y}, cost=${coords['cost']}")

    handle = await _find_slider_handle(tab)
    if not handle:
        logger.error("[CAPTCHA] slider handle not found — cannot drag")
        return {"solved": False, "cost": coords["cost"], "type": "slider"}

    start_x = handle["x"]
    start_y = handle["y"]
    drag_distance = target_x - start_x
    logger.info(f"[CAPTCHA] drag: from ({start_x},{start_y}) distance={drag_distance}px")

    await _drag_slider(tab, start_x, start_y, start_x + drag_distance, start_y)

    await asyncio.sleep(3)
    still = await _captcha_still_present(tab)
    if still:
        logger.warning("[CAPTCHA] slider still present after drag — solve failed")
        return {"solved": False, "cost": coords["cost"], "type": "slider"}

    logger.info("[CAPTCHA] slider solved!")
    return {"solved": True, "cost": coords["cost"], "type": "slider"}


async def _drag_slider(tab, start_x: float, start_y: float, end_x: float, end_y: float):
    try:
        await tab.send(
            uc.cdp.input_.dispatch_mouse_event(
                type_="mousePressed",
                x=start_x,
                y=start_y,
                button=uc.cdp.input_.MouseButton.LEFT,
                click_count=1,
            )
        )
    except Exception as e:
        logger.error(f"[CAPTCHA] mousePressed error: {e}")
        return

    steps = 25
    for i in range(1, steps + 1):
        cur_x = start_x + (end_x - start_x) * (i / steps)
        cur_y = start_y + (end_y - start_y) * (i / steps)
        try:
            await tab.send(
                uc.cdp.input_.dispatch_mouse_event(
                    type_="mouseMoved",
                    x=cur_x,
                    y=cur_y,
                    button=uc.cdp.input_.MouseButton.LEFT,
                    buttons=1,
                )
            )
        except Exception:
            pass
        await human_sleep(0.02, 0.08)

    try:
        await tab.send(
            uc.cdp.input_.dispatch_mouse_event(
                type_="mouseReleased",
                x=end_x,
                y=end_y,
                button=uc.cdp.input_.MouseButton.LEFT,
                click_count=1,
            )
        )
    except Exception as e:
        logger.warning(f"[CAPTCHA] mouseReleased error: {e}")


async def solve_geetest_captcha(tab, solver, rect: dict, screenshot_dir: str, order_id: str) -> dict:
    params = await _extract_geetest_params(tab)
    if not params:
        logger.warning("[CAPTCHA] could not extract GeeTest params, trying generic solve")
        return await solve_generic_captcha(tab, solver, rect, screenshot_dir, order_id, captcha_type="geetest")

    url = ""
    try:
        url = await tab.evaluate("location.href")
        if isinstance(url, list):
            url = parse_eval(url) or ""
    except Exception:
        url = ""

    solution = await _solve_geetest(solver, params["gt"], params.get("challenge", ""), url)
    if not solution:
        logger.error("[CAPTCHA] 2captcha geetest solve failed")
        return {"solved": False, "cost": 0.0, "type": "geetest"}

    injected = await _inject_geetest_solution(tab, solution)
    if not injected:
        logger.warning("[CAPTCHA] could not inject GeeTest solution — trying generic fallback")
        return await solve_generic_captcha(tab, solver, rect, screenshot_dir, order_id, captcha_type="geetest")

    await asyncio.sleep(3)
    still = await _captcha_still_present(tab)
    if still:
        logger.warning("[CAPTCHA] GeeTest still present after inject — solve failed")
        return {"solved": False, "cost": solution.get("cost", 0.0), "type": "geetest"}

    logger.info("[CAPTCHA] GeeTest solved!")
    return {"solved": True, "cost": solution.get("cost", 0.0), "type": "geetest"}


async def solve_generic_captcha(tab, solver, rect: dict, screenshot_dir: str, order_id: str, captcha_type: str = "unknown") -> dict:
    img_b64 = await _capture_region_screenshot(tab, rect, screenshot_dir, order_id)
    if not img_b64:
        logger.error("[CAPTCHA] could not capture screenshot for generic solve")
        return {"solved": False, "cost": 0.0, "type": captcha_type}

    coords = await _solve_coordinates(
        solver, img_b64,
        "Solve the captcha by clicking on the correct position"
    )
    if not coords:
        logger.error("[CAPTCHA] 2captcha returned no coordinates for generic solve")
        return {"solved": False, "cost": 0.0, "type": captcha_type}

    target_x = rect.get("x", 0) + coords["x"]
    target_y = rect.get("y", 0) + coords["y"]
    logger.info(f"[CAPTCHA] generic click target: x={target_x}, y={target_y}, cost=${coords['cost']}")

    try:
        await tab.send(
            uc.cdp.input_.dispatch_mouse_event(
                type_="mousePressed",
                x=target_x,
                y=target_y,
                button=uc.cdp.input_.MouseButton.LEFT,
                click_count=1,
            )
        )
        await human_sleep(0.05, 0.15)
        await tab.send(
            uc.cdp.input_.dispatch_mouse_event(
                type_="mouseReleased",
                x=target_x,
                y=target_y,
                button=uc.cdp.input_.MouseButton.LEFT,
                click_count=1,
            )
        )
    except Exception as e:
        logger.error(f"[CAPTCHA] generic click error: {e}")
        return {"solved": False, "cost": coords["cost"], "type": captcha_type}

    await asyncio.sleep(3)
    still = await _captcha_still_present(tab)
    if still:
        logger.warning("[CAPTCHA] captcha still present after generic solve — failed")
        return {"solved": False, "cost": coords["cost"], "type": captcha_type}

    logger.info("[CAPTCHA] generic captcha solved!")
    return {"solved": True, "cost": coords["cost"], "type": captcha_type}


async def solve_captcha(tab, settings) -> dict | None:
    api_key = settings.two_captcha_api_key
    if not api_key:
        logger.warning("[CAPTCHA] two_captcha_api_key not set — skipping captcha solving")
        return None

    captcha_info = await detect_captcha(tab)
    if not captcha_info:
        return None

    rect = {
        "x": captcha_info.get("x", 0),
        "y": captcha_info.get("y", 0),
        "w": captcha_info.get("w", 0),
        "h": captcha_info.get("h", 0),
    }
    captcha_type = captcha_info.get("type", "unknown")

    from twocaptcha import AsyncTwoCaptcha
    solver = AsyncTwoCaptcha(api_key, pollingInterval=5)

    max_retries = settings.captcha_max_retries
    total_cost = 0.0

    for attempt in range(1, max_retries + 1):
        logger.info(f"[CAPTCHA] solve attempt {attempt}/{max_retries}: type={captcha_type}")

        if captcha_type == "slider":
            result = await solve_slider_captcha(tab, solver, rect, settings.screenshot_dir, "")
        elif captcha_type == "geetest":
            result = await solve_geetest_captcha(tab, solver, rect, settings.screenshot_dir, "")
        else:
            result = await solve_generic_captcha(tab, solver, rect, settings.screenshot_dir, "", captcha_type)

        total_cost += result.get("cost", 0.0)

        if result["solved"]:
            logger.info(f"[CAPTCHA] SOLVED: type={captcha_type}, cost=${total_cost:.4f}, attempts={attempt}")
            return {"solved": True, "cost": total_cost, "type": captcha_type, "attempts": attempt}

        logger.warning(f"[CAPTCHA] attempt {attempt} failed, retrying...")
        await human_sleep(1, 3)

        captcha_info = await detect_captcha(tab)
        if not captcha_info:
            logger.info("[CAPTCHA] captcha disappeared between retries — solved externally")
            return {"solved": True, "cost": total_cost, "type": captcha_type, "attempts": attempt}

    logger.error(f"[CAPTCHA] FAILED after {max_retries} retries, type={captcha_type}, cost=${total_cost:.4f}")
    return {"solved": False, "cost": total_cost, "type": captcha_type, "attempts": max_retries}
