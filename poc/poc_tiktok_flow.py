"""
PoC: TikTok QR Login + Recharge + Payment Flow Investigation
Uses nodriver (CDP-based stealth browser) — harder for TikTok to detect.

Tests:
  T0.1 — TikTok QR login availability + selectors
  T0.2 — Recharge page URL + coin packages + selectors
  T0.3 — Card payment flow + OTP/3DS detection
  T0.4 — CAPTCHA detection
  T0.5 — Session persistence (persistent browser profile)

Usage:
  python poc_tiktok_flow.py              # Full interactive flow
  python poc_tiktok_flow.py --qr-only     # Just QR login + capture selectors
  python poc_tiktok_flow.py --recharge    # Just recharge page (requires existing session)
  python poc_tiktok_flow.py --payment     # Just payment flow (requires existing session)

Findings saved to: poc_findings.json
Screenshots saved to: poc_screenshots/
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import nodriver as uc

# ─── Config ──────────────────────────────────────────────────────────────

SCREENSHOT_DIR = Path(__file__).parent / "poc_screenshots"
SESSION_DIR = Path(__file__).parent / "poc_session"
FINDINGS_FILE = Path(__file__).parent / "poc_findings.json"

TIKTOK_LOGIN_URL = "https://www.tiktok.com/login"
TIKTOK_HOME_URL = "https://www.tiktok.com"
TIKTOK_RECHARGE_URL_CANDIDATES = [
    "https://www.tiktok.com/coin",
    "https://www.tiktok.com/recharge",
    "https://www.tiktok.com/wallet",
    "https://www.tiktok.com/falcon/coin",
]

QR_TIMEOUT_SECONDS = 300
FIND_TIMEOUT = 5  # seconds

findings = {
    "started_at": datetime.now().isoformat(),
    "python_version": sys.version,
    "nodriver_version": getattr(uc, '__version__', 'unknown'),
    "steps": {},
}


def save_findings():
    findings["finished_at"] = datetime.now().isoformat()
    FINDINGS_FILE.write_text(
        json.dumps(findings, indent=2, ensure_ascii=False, default=str),
        encoding='utf-8'
    )
    print(f"\n[Findings] Saved to {FINDINGS_FILE}")


def screenshot_dir():
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return SCREENSHOT_DIR


async def take_screenshot(tab, name: str):
    """Take screenshot and save."""
    path = screenshot_dir() / f"{name}.png"
    try:
        await tab.save_screenshot(str(path))
        print(f"  [Screenshot] {path}")
    except Exception as e:
        print(f"  [Screenshot ERROR] {e}")
        # Try full page screenshot
        try:
            await tab.save_screenshot(str(path), full_page=True)
            print(f"  [Screenshot full] {path}")
        except Exception as e2:
            print(f"  [Screenshot FAILED] {e2}")
            return None
    if name not in findings["steps"]:
        findings["steps"][name] = {}
    findings["steps"][name]["screenshot"] = str(path)
    return str(path)


async def try_find(tab, selector: str, timeout: int = FIND_TIMEOUT):
    """Try to find an element. Returns element or None."""
    try:
        el = await tab.find(selector, timeout=timeout)
        return el
    except Exception:
        return None


async def try_find_all(tab, selector: str, timeout: int = FIND_TIMEOUT):
    """Try to find all elements matching selector."""
    try:
        els = await tab.find_all(selector, timeout=timeout)
        return list(els) if els else []
    except Exception:
        return []


async def get_url(tab) -> str:
    try:
        return tab.target.url
    except Exception:
        try:
            return tab.url
        except Exception:
            return str(tab.target)


async def get_text(el):
    try:
        return await el.apply("(el) => el.innerText")
    except Exception:
        try:
            return str(el)
        except Exception:
            return ""


async def get_attr(el, attr: str):
    try:
        return await el.apply(f"(el) => el.getAttribute('{attr}')")
    except Exception:
        return None


async def get_box(el):
    try:
        box = await el.apply("(el) => { const r = el.getBoundingClientRect(); return JSON.stringify({x:r.x,y:r.y,width:r.width,height:r.height}); }")
        return json.loads(box)
    except Exception:
        return None


# ─── T0.1 — QR Login ────────────────────────────────────────────────────

async def test_qr_login(browser):
    """Navigate to TikTok login, detect QR option, capture selectors."""
    print("\n" + "=" * 60)
    print("T0.1 -- TikTok QR Login Test (nodriver)")
    print("=" * 60)

    tab = await browser.get(TIKTOK_LOGIN_URL)
    await asyncio.sleep(4)
    await take_screenshot(tab, "01_login_page")

    current_url = await get_url(tab)
    findings["steps"]["qr_login"] = {"login_url": TIKTOK_LOGIN_URL, "actual_url": current_url}
    print(f"  URL: {current_url}")

    # Try to detect QR code — test multiple selectors
    qr_selectors_to_try = [
        ('[data-e2e="qr-code"]', "data-e2e qr-code"),
        ('img[src*="qrcode"]', "img with qrcode in src"),
        ('canvas[class*="qr"]', "canvas with qr in class"),
        ('div[class*="qrcode"]', "div with qrcode in class"),
        ('div[class*="qr-code"]', "div with qr-code in class"),
        ('canvas', "any canvas"),
        ('img[class*="qr"]', "img with qr in class"),
    ]

    qr_found = False
    qr_selector = None

    for selector, desc in qr_selectors_to_try:
        el = await try_find(tab, selector, 3)
        if el:
            box = await get_box(el)
            # Skip tiny elements (likely tracking pixels)
            if box and box.get("width", 0) < 50 and box.get("height", 0) < 50:
                print(f"  [SKIP] {selector} ({desc}) — too small: {box['width']:.0f}x{box['height']:.0f}px")
                continue
            print(f"  [FOUND] QR element: {selector} ({desc})")
            if box:
                print(f"  QR element size: {box['width']:.0f}x{box['height']:.0f}px")
                findings["steps"]["qr_login"]["qr_box"] = box
            qr_selector = selector
            qr_found = True
            findings["steps"]["qr_login"]["qr_selector"] = selector
            findings["steps"]["qr_login"]["qr_selector_desc"] = desc
            break

    if not qr_found:
        print("  [INFO] QR code not directly visible. Looking for QR tab/button...")
        # Look for QR login tab/button
        qr_tab_selectors = [
            ('text=QR', "text QR"),
            ('a[href*="qr"]', "link with qr in href"),
            ('[data-e2e="qr-login"]', "data-e2e qr-login"),
        ]
        for sel, desc in qr_tab_selectors:
            el = await try_find(tab, sel, 2)
            if el:
                print(f"  [FOUND] QR tab/button: {sel} ({desc}) -- clicking")
                try:
                    await el.click()
                except Exception as e:
                    print(f"  [CLICK ERROR] {e}")
                await asyncio.sleep(3)
                await take_screenshot(tab, "01d_after_qr_tab_click")

                # Re-check for QR code
                for qs, qd in qr_selectors_to_try:
                    el2 = await try_find(tab, qs, 3)
                    if el2:
                        box = await get_box(el2)
                        if box and box.get("width", 0) < 50:
                            continue
                        print(f"  [FOUND] QR element after tab click: {qs} ({qd})")
                        if box:
                            print(f"  QR element size: {box['width']:.0f}x{box['height']:.0f}px")
                            findings["steps"]["qr_login"]["qr_box"] = box
                        qr_selector = qs
                        qr_found = True
                        findings["steps"]["qr_login"]["qr_tab_selector"] = sel
                        findings["steps"]["qr_login"]["qr_selector"] = qs
                        findings["steps"]["qr_login"]["qr_selector_desc"] = qd
                        break
                if qr_found:
                    break

    await take_screenshot(tab, "01_qr_login_page")

    if not qr_found:
        # Save page HTML for manual inspection
        try:
            html = await tab.get_content()
            html_path = screenshot_dir() / "01_login_page.html"
            html_path.write_text(html, encoding='utf-8')
            print(f"  [Saved] Login page HTML: {html_path}")
            findings["steps"]["qr_login"]["html_path"] = str(html_path)
        except Exception as e:
            print(f"  [WARN] Could not save HTML: {e}")

        # Check for anti-bot / rate limit messages
        anti_bot_selectors = [
            ('text=too frequently', "too frequently text"),
            ('text=too many', "too many text"),
            ('text=thường xuyên', "thường xuyên text (VN)"),
            ('text=Verify you', "Verify you text"),
            ('div[class*="captcha"]', "captcha div"),
            ('div[class*="slider"]', "slider div"),
        ]
        for sel, desc in anti_bot_selectors:
            el = await try_find(tab, sel, 1)
            if el:
                text = await get_text(el)
                print(f"  [ANTI-BOT DETECTED] {sel} ({desc}): {text[:200] if text else '(no text)'}")
                findings["steps"]["qr_login"]["anti_bot_detected"] = True
                findings["steps"]["qr_login"]["anti_bot_selector"] = sel
                findings["steps"]["qr_login"]["anti_bot_text"] = text[:500] if text else ""
                await take_screenshot(tab, "01_anti_bot_detected")
                break

        print("\n  [FAIL] Could not find QR code on login page")
        findings["steps"]["qr_login"]["qr_found"] = False
        return tab, False

    print(f"\n  [OK] QR code found with selector: {qr_selector}")
    findings["steps"]["qr_login"]["qr_found"] = True

    # Wait for user to scan QR
    print("\n" + "-" * 60)
    print("  >> Please scan the QR code with your TikTok mobile app.")
    print(f"     Timeout: {QR_TIMEOUT_SECONDS} seconds ({QR_TIMEOUT_SECONDS // 60} minutes)")
    print("-" * 60)

    login_detected = False
    start = time.time()
    last_screenshot = 0

    while time.time() - start < QR_TIMEOUT_SECONDS:
        await asyncio.sleep(2)
        elapsed = int(time.time() - start)

        current_url = await get_url(tab)
        if "login" not in current_url and "tiktok.com" in current_url and "404" not in current_url:
            print(f"\n  [OK] Login detected! URL changed to: {current_url}")
            login_detected = True
            break

        # Check for logged-in indicators
        profile_el = await try_find(tab, '[data-e2e="user-profile"]', 1)
        if profile_el:
            print(f"\n  [OK] Login detected! Profile element found.")
            login_detected = True
            break

        profile_icon = await try_find(tab, '[data-e2e="profile-icon"]', 1)
        if profile_icon:
            print(f"\n  [OK] Login detected! Profile icon found.")
            login_detected = True
            break

        # Take screenshot every 30s
        if elapsed - last_screenshot >= 30:
            await take_screenshot(tab, f"01_qr_waiting_{elapsed}s")
            last_screenshot = elapsed

        remaining = QR_TIMEOUT_SECONDS - elapsed
        print(f"\r  Waiting for QR scan... {elapsed}s elapsed, {remaining}s remaining", end="", flush=True)

    print()
    findings["steps"]["qr_login"]["login_detected"] = login_detected
    findings["steps"]["qr_login"]["wait_time_seconds"] = int(time.time() - start)

    if login_detected:
        await take_screenshot(tab, "02_after_login")
        print("  [OK] QR login successful!")
        print(f"  [OK] Session saved in browser profile: {SESSION_DIR}")
        findings["steps"]["qr_login"]["session_dir"] = str(SESSION_DIR)
    else:
        print("  [FAIL] QR scan timeout")
        await take_screenshot(tab, "02_qr_timeout")

    return tab, login_detected


# ─── T0.2 — Recharge Page ───────────────────────────────────────────────

async def test_recharge_page(browser, tab):
    """Navigate to recharge page, detect coin packages."""
    print("\n" + "=" * 60)
    print("T0.2 -- TikTok Recharge Page Test")
    print("=" * 60)

    recharge_url = None
    for url in TIKTOK_RECHARGE_URL_CANDIDATES:
        print(f"\n  Trying: {url}")
        try:
            tab = await browser.get(url)
            await asyncio.sleep(3)
            current_url = await get_url(tab)
            print(f"  Redirected to: {current_url}")
            slug = url.split("/")[-1] or "root"
            await take_screenshot(tab, f"03_recharge_{slug}")

            if "login" in current_url:
                print("  [WARN] Redirected to login -- session may have expired")
                findings["steps"]["recharge"] = {"error": "redirected_to_login", "tried_url": url}
                break

            package_selectors = [
                ('[data-e2e="coin-package"]', "data-e2e coin-package"),
                ('div[class*="package"]', "div with package in class"),
                ('div[class*="coin-item"]', "div with coin-item in class"),
                ('button[class*="recharge"]', "button with recharge in class"),
                ('div[class*="recharge"]', "div with recharge in class"),
                ('[data-e2e*="coin"]', "data-e2e with coin"),
                ('[data-e2e*="recharge"]', "data-e2e with recharge"),
                ('div[class*="product"]', "div with product in class"),
            ]

            for sel, desc in package_selectors:
                els = await try_find_all(tab, sel, 3)
                if els and len(els) > 0:
                    print(f"  [FOUND] {len(els)} package elements: {sel} ({desc})")
                    recharge_url = url
                    findings["steps"]["recharge"] = {
                        "url": url,
                        "actual_url": current_url,
                        "package_selector": sel,
                        "package_count": len(els),
                    }
                    for i, el in enumerate(els[:5]):
                        text = await get_text(el)
                        if text:
                            print(f"    Package {i}: {text[:100]}")
                            findings["steps"]["recharge"][f"package_{i}_text"] = text[:200]
                    break

            if recharge_url:
                break

        except Exception as e:
            print(f"  [ERROR] {e}")
            findings["steps"].setdefault("recharge", {}).setdefault("errors", []).append({"url": url, "error": str(e)})

    if not recharge_url:
        print("\n  [WARN] Could not find recharge page automatically")
        print("  Trying from home page...")
        tab = await browser.get(TIKTOK_HOME_URL)
        await asyncio.sleep(3)
        await take_screenshot(tab, "03b_home_for_recharge")

        nav_selectors = [
            ('[data-e2e="wallet"]', "wallet"),
            ('[data-e2e="coin"]', "coin"),
            ('a[href*="coin"]', "link with coin"),
            ('a[href*="recharge"]', "link with recharge"),
            ('a[href*="wallet"]', "link with wallet"),
        ]
        for sel, desc in nav_selectors:
            el = await try_find(tab, sel, 2)
            if el:
                print(f"  [FOUND] Navigation: {sel} ({desc}) -- clicking")
                try:
                    await el.click()
                except Exception:
                    pass
                await asyncio.sleep(3)
                current_url = await get_url(tab)
                print(f"  Navigated to: {current_url}")
                await take_screenshot(tab, "03c_after_nav_click")
                findings["steps"].setdefault("recharge", {})["nav_selector"] = sel
                findings["steps"].setdefault("recharge", {})["nav_url"] = current_url
                recharge_url = current_url
                break

    if recharge_url:
        print(f"\n  [OK] Recharge page found: {recharge_url}")
        await take_screenshot(tab, "03_recharge_page")

        all_buttons = await try_find_all(tab, "button", 2)
        all_links = await try_find_all(tab, "a", 2)
        print(f"  Found {len(all_buttons)} buttons, {len(all_links)} links on page")
        findings["steps"].setdefault("recharge", {})["button_count"] = len(all_buttons)
        findings["steps"].setdefault("recharge", {})["link_count"] = len(all_links)

        try:
            html = await tab.get_content()
            html_path = screenshot_dir() / "03_recharge_page.html"
            html_path.write_text(html, encoding='utf-8')
            print(f"  [Saved] Page HTML: {html_path}")
            findings["steps"].setdefault("recharge", {})["html_path"] = str(html_path)
        except Exception as e:
            print(f"  [WARN] Could not save HTML: {e}")
    else:
        print("\n  [FAIL] Could not find recharge page")
        findings["steps"]["recharge"]["found"] = False

    return tab, recharge_url is not None


# ─── T0.3 — Payment Flow ─────────────────────────────────────────────────

async def test_payment_flow(browser, tab):
    """Detect payment form, check for OTP/3DS requirements."""
    print("\n" + "=" * 60)
    print("T0.3 -- Card Payment Flow Test")
    print("=" * 60)

    print("\n  Looking for coin package buttons to click...")
    print("  (If automated detection fails, manually click a package in the browser)")

    pkg_sel = findings.get("steps", {}).get("recharge", {}).get("package_selector", "div[class*='package']")
    els = await try_find_all(tab, pkg_sel, 3)
    if els:
        print(f"  Clicking first package element ({len(els)} found)")
        try:
            await els[0].click()
        except Exception as e:
            print(f"  [CLICK ERROR] {e}")
        await asyncio.sleep(3)
        await take_screenshot(tab, "04_after_package_click")
    else:
        print("  [WARN] No package elements found. Please click a package manually.")
        print("  Waiting 15 seconds for manual click...")
        await asyncio.sleep(15)
        await take_screenshot(tab, "04_after_manual_click")

    print("\n  Scanning for payment form elements...")
    payment_selectors = [
        ('input[name="cardNumber"]', "card number input"),
        ('input[name="card_number"]', "card_number input"),
        ('input[placeholder*="card"]', "card placeholder input"),
        ('input[autocomplete="cc-number"]', "cc-number autocomplete"),
        ('div[class*="payment"]', "payment div"),
        ('div[class*="card"]', "card div"),
        ('[data-e2e="payment"]', "data-e2e payment"),
        ('iframe[src*="payment"]', "payment iframe"),
        ('iframe[src*="stripe"]', "stripe iframe"),
        ('iframe[src*="checkout"]', "checkout iframe"),
        ('iframe[src*="3ds"]', "3ds iframe"),
    ]

    found_payment = False
    for sel, desc in payment_selectors:
        el = await try_find(tab, sel, 2)
        if el:
            print(f"  [FOUND] Payment element: {sel} ({desc})")
            found_payment = True
            findings["steps"].setdefault("payment", {})["selector"] = sel
            findings["steps"].setdefault("payment", {})["selector_desc"] = desc

    await take_screenshot(tab, "04_payment_page")

    if found_payment:
        print("\n  Payment form detected!")
        print("  Please fill card details and proceed to payment in the browser.")
        print("  The script will monitor for OTP/3DS prompts.")
        print("  Waiting 90 seconds for payment interaction...")
        findings["steps"].setdefault("payment", {})["form_found"] = True

        otp_selectors = [
            ('input[name*="otp"]', "OTP input"),
            ('input[placeholder*="OTP"]', "OTP placeholder"),
            ('input[placeholder*="code"]', "code placeholder"),
            ('div[class*="otp"]', "OTP div"),
            ('div[class*="3ds"]', "3DS div"),
            ('div[class*="verify"]', "verify div"),
            ('iframe[src*="3ds"]', "3DS iframe"),
            ('iframe[src*="otp"]', "OTP iframe"),
        ]

        otp_detected = False
        start = time.time()
        while time.time() - start < 90:
            await asyncio.sleep(3)
            for sel, desc in otp_selectors:
                el = await try_find(tab, sel, 1)
                if el:
                    print(f"\n  [FOUND] OTP/3DS element: {sel} ({desc})")
                    otp_detected = True
                    findings["steps"]["payment"]["otp_detected"] = True
                    findings["steps"]["payment"]["otp_selector"] = sel
                    await take_screenshot(tab, "05_otp_detected")
                    break
            if otp_detected:
                break
            elapsed = int(time.time() - start)
            print(f"\r  Monitoring for OTP/3DS... {elapsed}s/90s", end="", flush=True)

        print()
        if not otp_detected:
            print("  [OK] No OTP/3DS detected within 90 seconds")
            findings["steps"]["payment"]["otp_detected"] = False
            await take_screenshot(tab, "05_no_otp")
    else:
        print("\n  [WARN] No payment form detected automatically")
        findings["steps"].setdefault("payment", {})["form_found"] = False
        await take_screenshot(tab, "04_payment_manual_inspect")

    return tab


# ─── T0.4 — CAPTCHA Detection ─────────────────────────────────────────────

async def test_captcha_detection(tab):
    """Check if CAPTCHA appeared at any point."""
    print("\n" + "=" * 60)
    print("T0.4 -- CAPTCHA Detection Test")
    print("=" * 60)

    captcha_selectors = [
        ('div[class*="captcha"]', "captcha div"),
        ('div[class*="verify-image"]', "verify-image div"),
        ('div[class*="slider"]', "slider div"),
        ('[data-e2e="captcha"]', "data-e2e captcha"),
        ('iframe[src*="captcha"]', "captcha iframe"),
        ('div[class*="geetest"]', "geetest captcha"),
    ]

    captcha_found = False
    for sel, desc in captcha_selectors:
        el = await try_find(tab, sel, 1)
        if el:
            print(f"  [FOUND] CAPTCHA element: {sel} ({desc})")
            captcha_found = True
            findings["steps"]["captcha"] = {"found": True, "selector": sel}
            await take_screenshot(tab, "06_captcha_found")
            break

    if not captcha_found:
        print("  [OK] No CAPTCHA detected on current page")
        findings["steps"]["captcha"] = {"found": False}

    # Check HTML for captcha keywords
    try:
        html = await tab.get_content()
        captcha_keywords = ["captcha", "verify-image", "slider-puzzle", "geetest",
                           "nc_", "secsdk", "tcaptcha"]
        found_kw = [kw for kw in captcha_keywords if kw.lower() in html.lower()]
        if found_kw:
            print(f"  [INFO] Captcha keywords in HTML: {found_kw}")
            findings["steps"]["captcha"]["html_keywords"] = found_kw
    except Exception:
        pass

    return tab


# ─── T0.5 — Session Persistence ──────────────────────────────────────────

async def test_session_persistence(browser):
    """Test if session persists by loading a page with saved profile."""
    print("\n" + "=" * 60)
    print("T0.5 -- Session Persistence Test")
    print("=" * 60)

    tab = await browser.get(TIKTOK_HOME_URL)
    await asyncio.sleep(3)
    await take_screenshot(tab, "07_session_check")

    profile_selectors = [
        '[data-e2e="user-profile"]',
        '[data-e2e="profile-icon"]',
    ]

    logged_in = False
    for sel in profile_selectors:
        el = await try_find(tab, sel, 3)
        if el:
            print(f"  [OK] Session persists! Logged in (found: {sel})")
            logged_in = True
            findings["steps"]["session_persistence"] = {"valid": True, "selector": sel}
            break

    if not logged_in:
        current_url = await get_url(tab)
        if "login" in current_url:
            print(f"  [FAIL] Session expired. Redirected to login.")
        else:
            print(f"  [WARN] Could not determine login state at {current_url}")
        findings["steps"]["session_persistence"] = {"valid": False, "url": current_url}

    return tab


# ─── Main ─────────────────────────────────────────────────────────────────

async def main():
    args = set(sys.argv[1:])
    qr_only = "--qr-only" in args
    recharge_only = "--recharge" in args
    payment_only = "--payment" in args

    print("=" * 60)
    print("  TikTok Coin Fulfillment PoC (nodriver)")
    print(f"  Python: {sys.version.split()[0]}")
    print(f"  nodriver: {getattr(uc, '__version__', 'unknown')}")
    print(f"  Time: {datetime.now().isoformat()}")
    print("=" * 60)

    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    print("\n[1] Launching browser with nodriver (stealth CDP)...")
    print(f"    Profile: {SESSION_DIR}")
    try:
        browser = await uc.start(
            browser_args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-infobars",
            ],
            user_data_dir=str(SESSION_DIR),
        )
        print("  [OK] Browser launched")
    except Exception as e:
        print(f"  [ERROR] Failed to launch browser: {e}")
        findings["error"] = str(e)
        save_findings()
        return

    try:
        tab = None
        logged_in = False

        # T0.1 — QR Login
        if not recharge_only and not payment_only:
            tab, logged_in = await test_qr_login(browser)
            if not logged_in and not qr_only:
                print("\n  [INFO] QR login failed. Checking existing session...")
                tab = await test_session_persistence(browser)
                logged_in = findings.get("steps", {}).get("session_persistence", {}).get("valid", False)

        # T0.5 — Session persistence (if skipping QR)
        if (recharge_only or payment_only) and not logged_in:
            tab = await test_session_persistence(browser)
            logged_in = findings.get("steps", {}).get("session_persistence", {}).get("valid", False)

        # T0.2 — Recharge Page
        if not qr_only and not payment_only and logged_in:
            tab, _ = await test_recharge_page(browser, tab)

        # T0.3 — Payment Flow
        if not qr_only and not recharge_only and logged_in:
            tab = await test_payment_flow(browser, tab)

        # T0.4 — CAPTCHA Detection
        if tab and not qr_only:
            tab = await test_captcha_detection(tab)

    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        findings["error"] = str(e)
        findings["traceback"] = traceback.format_exc()
    finally:
        save_findings()
        print("\n[Done] PoC completed.")
        print("  Check:")
        print(f"    - {FINDINGS_FILE}  (results)")
        print(f"    - {SCREENSHOT_DIR}/   (screenshots)")
        print("\n  Browser stays open 10s for manual inspection...")
        await asyncio.sleep(10)
        try:
            browser.stop()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
