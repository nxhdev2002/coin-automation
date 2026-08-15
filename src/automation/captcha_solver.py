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

SLIDER_HANDLE_SELECTORS = [
    '#captcha_slide_button',
    'div[class*="secsdk-captcha-drag-icon"]',
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


async def _extract_captcha_images(tab) -> dict | None:
    """Extract TikTok captcha background + puzzle piece images from DOM."""
    js = """
    (() => {
        const imgs = document.querySelectorAll('img[alt="Captcha"]');
        if (imgs.length < 1) return null;
        const result = {images: []};
        for (const img of imgs) {
            const r = img.getBoundingClientRect();
            result.images.push({
                src: img.src,
                x: r.x, y: r.y, w: r.width, h: r.height,
                naturalW: img.naturalWidth, naturalH: img.naturalHeight,
            });
        }
        // Sort by size — largest first (background is bigger)
        result.images.sort((a, b) => (b.w * b.h) - (a.w * a.h));
        result.background = result.images[0] || null;
        result.puzzle = result.images[1] || null;
        return result;
    })()
    """
    try:
        result = await tab.evaluate(js)
        result = parse_eval(result)
        if isinstance(result, dict) and result.get("background"):
            bg = result["background"]
            pz = result.get("puzzle")
            logger.info(
                f"[CAPTCHA] images: background {bg.get('w')}x{bg.get('h')} "
                f"at ({bg.get('x')},{bg.get('y')}), "
                f"puzzle {pz.get('w')}x{pz.get('h')} at ({pz.get('x')},{pz.get('y')})" if pz
                else f"[CAPTCHA] images: background {bg.get('w')}x{bg.get('h')}"
            )
            return result
    except Exception as e:
        logger.warning(f"[CAPTCHA] extract images error: {e}")
    return None


async def _detect_gap_via_edge_detection(tab) -> dict | None:
    """Detect slider gap position using edge detection in browser canvas."""
    js = """
    (async () => {
        const imgs = document.querySelectorAll('img[alt="Captcha"]');
        if (imgs.length < 1) return null;
        const sorted = Array.from(imgs).sort((a, b) => {
            const ar = a.getBoundingClientRect();
            const br = b.getBoundingClientRect();
            return (br.width * br.height) - (ar.width * ar.height);
        });
        const bg = sorted[0];
        const pz = sorted[1];
        const bgR = bg.getBoundingClientRect();
        const pzR = pz ? pz.getBoundingClientRect() : null;
        const slideBtn = document.querySelector('#captcha_slide_button')
            || document.querySelector('.secsdk-captcha-drag-icon');
        if (!slideBtn) return null;
        const slideR = slideBtn.getBoundingClientRect();

        return new Promise((resolve) => {
            const img = new Image();
            img.onload = function() {
                const canvas = document.createElement('canvas');
                canvas.width = img.naturalWidth;
                canvas.height = img.naturalHeight;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0);
                const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
                const w = canvas.width, h = canvas.height;

                // Edge detection: horizontal gradient per column
                const colEdge = new Array(w).fill(0);
                for (let x = 1; x < w - 1; x++) {
                    let edgeSum = 0;
                    for (let y = 0; y < h; y++) {
                        for (let c = 0; c < 3; c++) {
                            const left = data[((y * w + (x-1)) * 4) + c];
                            const right = data[((y * w + (x+1)) * 4) + c];
                            edgeSum += Math.abs(right - left);
                        }
                    }
                    colEdge[x] = edgeSum;
                }

                // Brightness per column
                const colBrightness = new Array(w);
                for (let x = 0; x < w; x++) {
                    let sum = 0;
                    for (let y = 0; y < h; y++) {
                        const idx = (y * w + x) * 4;
                        sum += (data[idx] + data[idx+1] + data[idx+2]) / 3;
                    }
                    colBrightness[x] = sum / h;
                }

                const overallMean = colBrightness.reduce((a, b) => a + b, 0) / w;
                const threshold = overallMean - 15;
                const third = Math.floor(w / 3);

                // Brightness gap
                let gapLeftB = -1, gapRightB = -1;
                for (let x = third; x < w; x++) {
                    if (colBrightness[x] < threshold) {
                        if (gapLeftB === -1) gapLeftB = x;
                        gapRightB = x;
                    }
                }
                let gapXB = gapLeftB >= 0 ? Math.floor((gapLeftB + gapRightB) / 2) : -1;

                // Edge detection: find two strong edges 30-100px apart
                const rightEdges = [];
                for (let x = third; x < w; x++) {
                    rightEdges.push({x: x, edge: colEdge[x]});
                }
                rightEdges.sort((a, b) => b.edge - a.edge);
                const topEdges = rightEdges.slice(0, 50).map(e => e.x).sort((a, b) => a - b);

                let gapXE = -1;
                for (let i = 0; i < topEdges.length - 1; i++) {
                    for (let j = i + 1; j < topEdges.length; j++) {
                        const dist = topEdges[j] - topEdges[i];
                        if (dist >= 30 && dist <= 100) {
                            gapXE = Math.floor((topEdges[i] + topEdges[j]) / 2);
                            break;
                        }
                    }
                    if (gapXE > 0) break;
                }

                const gapX = gapXE > 0 ? gapXE : gapXB;
                if (gapX < 0) { resolve(null); return; }

                const scaleX = bgR.width / bg.naturalWidth;
                const scaledGapX = gapX * scaleX;
                const pzOffset = pzR ? (pzR.x - bgR.x) : 0;
                const dragDistance = scaledGapX - pzOffset;
                const handleX = slideR.x + slideR.width / 2;
                const handleY = slideR.y + slideR.height / 2;

                resolve({
                    gapX: gapX,
                    gapXE: gapXE,
                    gapXB: gapXB,
                    scaledGapX: scaledGapX,
                    dragDistance: dragDistance,
                    handleX: handleX,
                    handleY: handleY,
                    endX: handleX + dragDistance,
                    pzOffset: pzOffset,
                    method: gapXE > 0 ? 'edge' : 'brightness',
                });
            };
            img.onerror = () => resolve(null);
            img.src = bg.src;
        });
    })()
    """
    try:
        result = await tab.evaluate(js)
        result = parse_eval(result)
        if isinstance(result, dict) and result.get("gapX") is not None:
            logger.info(
                f"[CAPTCHA] gap detection: method={result.get('method')}, "
                f"gapX={result.get('gapX')}, "
                f"drag={result.get('dragDistance')}px"
            )
            return result
    except Exception as e:
        logger.warning(f"[CAPTCHA] edge detection error: {e}")
    return None


async def _webp_to_png_b64(webp_b64: str) -> str | None:
    """Convert webp base64 to PNG base64 for 2captcha compatibility."""
    try:
        from io import BytesIO
        from PIL import Image
        img_data = base64.b64decode(webp_b64)
        img = Image.open(BytesIO(img_data))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.warning(f"[CAPTCHA] webp->png conversion failed: {e}")
        return None


async def solve_slider_captcha(tab, solver, rect: dict, screenshot_dir: str, order_id: str) -> dict:
    # Method 1: Edge detection (primary — no API cost, faster)
    gap_result = await _detect_gap_via_edge_detection(tab)

    if gap_result:
        drag_distance = gap_result["dragDistance"]
        start_x = gap_result["handleX"]
        start_y = gap_result["handleY"]
        end_x = gap_result["endX"]

        logger.info(
            f"[CAPTCHA] edge detection: gapX={gap_result['gapX']}, "
            f"method={gap_result['method']}, drag={drag_distance}px"
        )

        await _drag_slider(tab, start_x, start_y, end_x, start_y)

        await asyncio.sleep(3)
        still = await _captcha_still_present(tab)
        if not still:
            logger.info("[CAPTCHA] slider solved via edge detection!")
            return {"solved": True, "cost": 0.0, "type": "slider", "method": gap_result["method"]}

        logger.warning("[CAPTCHA] edge detection drag failed, trying 2captcha fallback...")

    # Method 2: 2captcha (fallback)
    images = await _extract_captcha_images(tab)

    if images and images.get("background"):
        bg = images["background"]
        bg_src = bg.get("src", "")
        if bg_src.startswith("data:image"):
            bg_b64 = bg_src.split(",", 1)[1] if "," in bg_src else bg_src
        else:
            bg_b64 = bg_src

        if bg_src.startswith("data:image/webp"):
            png_b64 = await _webp_to_png_b64(bg_b64)
            if png_b64:
                bg_b64 = png_b64
                logger.info("[CAPTCHA] converted webp->png for 2captcha")

        logger.info(f"[CAPTCHA] using DOM-extracted background image ({len(bg_b64)} bytes base64)")

        coords = await _solve_coordinates(
            solver, bg_b64,
            "Click on the point where the puzzle piece should be placed"
        )

        if coords:
            bg_x = bg.get("x", 0)
            bg_w = bg.get("w", 0)
            bg_nat_w = bg.get("naturalW", bg_w)

            scale_x = bg_w / bg_nat_w if bg_nat_w > 0 else 1.0
            target_x_in_image = coords["x"] * scale_x

            target_x_abs = bg_x + target_x_in_image

            pz = images.get("puzzle")
            if pz:
                pz_x = pz.get("x", 0)
                drag_distance = target_x_in_image - (pz_x - bg_x)
            else:
                drag_distance = target_x_in_image

            logger.info(
                f"[CAPTCHA] 2captcha target: image_x={coords['x']}, "
                f"scaled_x={target_x_in_image}, abs_x={target_x_abs}, "
                f"drag_distance={drag_distance}px, cost=${coords['cost']}"
            )
    else:
        logger.warning("[CAPTCHA] could not extract DOM images, falling back to screenshot")
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

        target_x_abs = rect.get("x", 0) + coords["x"]
        drag_distance = coords["x"]

    if not coords:
        logger.error("[CAPTCHA] 2captcha returned no coordinates for slider")
        return {"solved": False, "cost": 0.0, "type": "slider"}

    handle = await _find_slider_handle(tab)
    if not handle:
        logger.error("[CAPTCHA] slider handle not found — cannot drag")
        return {"solved": False, "cost": coords.get("cost", 0.0), "type": "slider"}

    start_x = handle["x"]
    start_y = handle["y"]
    end_x = start_x + drag_distance

    logger.info(f"[CAPTCHA] drag: from ({start_x},{start_y}) distance={drag_distance}px")

    await _drag_slider(tab, start_x, start_y, end_x, start_y)

    await asyncio.sleep(3)
    still = await _captcha_still_present(tab)
    if still:
        logger.warning("[CAPTCHA] slider still present after drag — solve failed")
        return {"solved": False, "cost": coords.get("cost", 0.0), "type": "slider"}

    logger.info("[CAPTCHA] slider solved via 2captcha!")
    return {"solved": True, "cost": coords.get("cost", 0.0), "type": "slider"}


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
        logger.warning("[CAPTCHA] SadCaptcha extension timeout — falling back to manual solve")

    # Manual solving fallback (edge detection + 2captcha)
    rect = {
        "x": captcha_info.get("x", 0),
        "y": captcha_info.get("y", 0),
        "w": captcha_info.get("w", 0),
        "h": captcha_info.get("h", 0),
    }

    api_key = settings.two_captcha_api_key
    if api_key:
        from twocaptcha import AsyncTwoCaptcha
        solver = AsyncTwoCaptcha(api_key, pollingInterval=5, defaultTimeout=300)
    else:
        solver = None
        logger.info("[CAPTCHA] no 2captcha API key — using edge detection only")

    max_retries = settings.captcha_max_retries
    total_cost = 0.0

    for attempt in range(1, max_retries + 1):
        logger.info(f"[CAPTCHA] solve attempt {attempt}/{max_retries}: type={captcha_type}")

        if captcha_type == "slider":
            result = await solve_slider_captcha(tab, solver, rect, settings.screenshot_dir, "")
        elif captcha_type == "geetest":
            if not solver:
                logger.error("[CAPTCHA] GeeTest requires 2captcha API key")
                return {"solved": False, "cost": 0.0, "type": "geetest", "attempts": 0}
            result = await solve_geetest_captcha(tab, solver, rect, settings.screenshot_dir, "")
        else:
            if not solver:
                logger.error(f"[CAPTCHA] {captcha_type} requires 2captcha API key")
                return {"solved": False, "cost": 0.0, "type": captcha_type, "attempts": 0}
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
