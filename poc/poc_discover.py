"""
PoC Step 1: Spawn browser + discover all selectors on TikTok login page.
Goal: find QR tab/button and QR code element as fast as possible.

Then after login, discover elements on recharge page.

Usage:
  python poc_discover.py              # Full flow: discover login → QR → scan → discover recharge
  python poc_discover.py --login      # Just login page discovery (no QR scan)
  python poc_discover.py --recharge   # Just recharge page (requires existing session)
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import nodriver as uc

SCREENSHOT_DIR = Path(__file__).parent / "poc_screenshots"
SESSION_DIR = Path(__file__).parent / "poc_session"
FINDINGS_FILE = Path(__file__).parent / "poc_findings.json"

TIKTOK_LOGIN_URL = "https://www.tiktok.com/login"
TIKTOK_HOME_URL = "https://www.tiktok.com"
TIKTOK_RECHARGE_CANDIDATES = [
    "https://www.tiktok.com/coin",
    "https://www.tiktok.com/recharge",
    "https://www.tiktok.com/wallet",
]

QR_TIMEOUT = 300
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

findings = {
    "started_at": datetime.now().isoformat(),
    "nodriver": getattr(uc, '__version__', 'unknown'),
    "steps": {},
}


def save():
    findings["finished_at"] = datetime.now().isoformat()
    FINDINGS_FILE.write_text(
        json.dumps(findings, indent=2, ensure_ascii=False, default=str),
        encoding='utf-8'
    )
    print(f"\n >> Findings saved: {FINDINGS_FILE}")


async def screenshot(tab, name):
    path = SCREENSHOT_DIR / f"{name}.png"
    try:
        await tab.save_screenshot(str(path))
        print(f"  [shot] {path.name}")
    except Exception as e:
        print(f"  [shot ERR] {e}")
    return str(path)


async def dump_elements(tab, label: str):
    """Dump all interactive elements on page: buttons, links, inputs, tabs."""
    print(f"\n--- Discovering elements on {label} ---")

    # JavaScript to extract all interactive elements with their info
    js = """
    (() => {
        try {
            const results = [];
            const sel = 'button, a, [role="tab"], [role="button"], input, [data-e2e], div[class*="tab"], div[class*="qr"], div[class*="code"], canvas, img[class*="qr"], img[src*="qr"]';
            document.querySelectorAll(sel).forEach((el, i) => {
                const r = el.getBoundingClientRect();
                if (r.width < 5 || r.height < 5) return;
                results.push({
                    i: i, tag: el.tagName.toLowerCase(),
                    id: el.id || '', cls: (el.className || '').toString().slice(0, 50),
                    text: (el.innerText || '').trim().slice(0, 80),
                    e2e: el.getAttribute('data-e2e') || '',
                    href: el.getAttribute('href') || '',
                    type: el.getAttribute('type') || '',
                    role: el.getAttribute('role') || '',
                    ph: el.getAttribute('placeholder') || '',
                    name: el.getAttribute('name') || '',
                    src: (el.getAttribute('src') || '').slice(0, 60),
                    w: Math.round(r.width), h: Math.round(r.height),
                    x: Math.round(r.x), y: Math.round(r.y),
                });
            });
            return JSON.stringify(results);
        } catch(e) { return JSON.stringify({error: e.message}); }
    })()
    """

    try:
        result = await tab.evaluate(js)
        if isinstance(result, str):
            elements = json.loads(result)
        else:
            elements = result

        if isinstance(elements, dict) and "error" in elements:
            print(f"  [JS ERROR] {elements['error']}")
            findings["steps"][label] = {"error": elements["error"]}
            return []

        print(f"  Found {len(elements)} interactive elements:\n")
        for el in elements:
            parts = [el['tag']]
            if el.get('e2e'): parts.append(f"e2e={el['e2e']}")
            if el.get('text'): parts.append(f'text="{el["text"][:30]}"')
            if el.get('id'): parts.append(f"id={el['id']}")
            if el.get('cls') and not el.get('e2e'): parts.append(f"class={el['cls'][:30]}")
            if el.get('href'): parts.append(f"href={el['href'][:30]}")
            if el.get('ph'): parts.append(f'ph="{el["ph"]}"')
            if el.get('name'): parts.append(f"name={el['name']}")
            print(f"  [{el['i']:3d}] {el['w']}x{el['h']:3d} ({el['x']:4d},{el['y']:4d}) {' | '.join(parts)}")

        findings["steps"][label] = {"element_count": len(elements), "elements": elements}
        return elements

    except Exception as e:
        print(f"  [ERROR] dump_elements: {e}")
        findings["steps"][label] = {"error": str(e)}
        return []


async def find_qr_tab_and_click(tab) -> bool:
    """Find QR login option and click it."""
    print("\n--- Looking for QR login button ---")

    # From PoC: login page has div[data-e2e="channel-item"] elements.
    # First one is "Use QR code". Click it.
    js = """
    (() => {
        try {
            const items = document.querySelectorAll('[data-e2e="channel-item"]');
            for (const el of items) {
                const text = (el.innerText || '').toLowerCase().trim();
                if (text.includes('qr')) {
                    el.click();
                    return JSON.stringify({found: true, text: text, tag: el.tagName});
                }
            }
            return JSON.stringify({found: false, count: items.length});
        } catch(e) { return JSON.stringify({found: false, error: e.message}); }
    })()
    """
    try:
        result = await tab.evaluate(js)
        data = json.loads(result) if isinstance(result, str) else result
        if data.get("found"):
            print(f"  [FOUND+CLICKED] QR login: text='{data.get('text', '')}'")
            await asyncio.sleep(3)
            return True
        print(f"  [NOT FOUND] {data}")
    except Exception as e:
        print(f"  [JS ERROR] {e}")

    return False


async def find_qr_code_element(tab):
    """Find the QR code image/canvas element and return its selector."""
    print("\n--- Looking for QR code element ---")

    # Try CSS selectors first (fast)
    for sel in ['[data-e2e="qr-code"]', 'img[src*="qrcode"]', 'img[src*="qr"]',
                'canvas[class*="qr"]', 'div[class*="qrcode"]', 'div[class*="qr-code"]']:
        try:
            el = await tab.find(sel, timeout=2)
            if el:
                box_js = "(e) => { const r = e.getBoundingClientRect(); return Math.round(r.width) + 'x' + Math.round(r.height); }"
                try:
                    size = await el.apply(box_js)
                except Exception:
                    size = "?x?"
                print(f"  [FOUND] QR code: {sel} ({size})")
                findings["steps"]["qr_code_element"] = {"selector": sel, "size": size}
                return sel, el
        except Exception:
            continue

    # JS fallback: find canvas or img with QR
    print("  Trying JS search for QR code...")
    js = """
    (() => {
        try {
            const canvases = document.querySelectorAll('canvas');
            for (const c of canvases) {
                const r = c.getBoundingClientRect();
                if (r.width >= 80) return JSON.stringify({type:'canvas', w:Math.round(r.width), h:Math.round(r.height), cls:c.className, id:c.id});
            }
            const imgs = document.querySelectorAll('img');
            for (const img of imgs) {
                const r = img.getBoundingClientRect();
                if (r.width >= 80 && (img.src.includes('qr') || img.className.includes('qr'))) 
                    return JSON.stringify({type:'img', w:Math.round(r.width), h:Math.round(r.height), src:img.src.slice(0,80), cls:img.className});
            }
            return JSON.stringify({type:'none'});
        } catch(e) { return JSON.stringify({type:'error', msg:e.message}); }
    })()
    """
    try:
        result = await tab.evaluate(js)
        data = json.loads(result) if isinstance(result, str) else result
        if data.get("type") != "none" and data.get("type") != "error":
            print(f"  [FOUND via JS] QR code: {data}")
            findings["steps"]["qr_code_element"] = data
            return None, None
        print(f"  [NOT FOUND] {data}")
    except Exception as e:
        print(f"  [JS ERROR] {e}")

    return None, None


async def check_logged_in(tab) -> bool:
    """Check if currently logged in."""
    url = tab.target.url if hasattr(tab, 'target') else str(tab)
    if "login" not in url and "tiktok.com" in url and "404" not in url:
        return True
    for sel in ['[data-e2e="user-profile"]', '[data-e2e="profile-icon"]']:
        try:
            el = await tab.find(sel, timeout=1)
            if el:
                return True
        except Exception:
            continue
    return False


# ─── Main ─────────────────────────────────────────────────────────────────

async def main():
    args = set(sys.argv[1:])
    login_only = "--login" in args
    recharge_only = "--recharge" in args

    print("=" * 60)
    print(f"  TikTok PoC — Selector Discovery (nodriver {getattr(uc, '__version__', '?')})")
    print(f"  {datetime.now().isoformat()}")
    print("=" * 60)

    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Spawn browser
    print("\n[1] Launching browser...")
    browser = await uc.start(
        browser_args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
        ],
        user_data_dir=str(SESSION_DIR),
    )
    print(f"  OK — profile: {SESSION_DIR}")

    try:
        if recharge_only:
            # Skip login, go straight to recharge
            print("\n[2] Checking existing session...")
            tab = await browser.get(TIKTOK_HOME_URL)
            await asyncio.sleep(3)
            logged_in = await check_logged_in(tab)
            if not logged_in:
                print("  NOT logged in. Run without --recharge first to login.")
                return
            print("  OK — logged in")
        else:
            # 2. Go to login page
            print("\n[2] Navigating to login page...")
            tab = await browser.get(TIKTOK_LOGIN_URL)
            await asyncio.sleep(3)
            await screenshot(tab, "01_login_page")

            # 3. Check if already logged in (session reuse)
            logged_in = await check_logged_in(tab)
            if logged_in:
                print("  Already logged in (session reuse)!")
                findings["steps"]["session_reuse"] = True
            else:
                print(f"  URL: {tab.target.url}")
                # 4. Dump all elements on login page
                await dump_elements(tab, "login_page")

                # 5. Find and click QR tab
                clicked = await find_qr_tab_and_click(tab)
                if clicked:
                    await screenshot(tab, "02_after_qr_click")
                    # 6. Dump elements again (after QR tab click)
                    await dump_elements(tab, "login_page_after_qr_tab")

                # 7. Find QR code element
                qr_sel, qr_el = await find_qr_code_element(tab)
                await screenshot(tab, "03_qr_code")

                if login_only:
                    print("\n[Done] --login mode, stopping here.")
                    save()
                    return

                # 8. Wait for user to scan QR
                print(f"\n[3] Waiting for QR scan ({QR_TIMEOUT}s)...")
                print("    >> Scan the QR code with your TikTok app")
                print("-" * 60)

                start = time.time()
                while time.time() - start < QR_TIMEOUT:
                    await asyncio.sleep(2)
                    if await check_logged_in(tab):
                        elapsed = int(time.time() - start)
                        print(f"\n  [OK] Login detected after {elapsed}s!")
                        findings["steps"]["qr_login"] = {"success": True, "scan_time_s": elapsed}
                        break
                    elapsed = int(time.time() - start)
                    remaining = QR_TIMEOUT - elapsed
                    print(f"\r  Waiting... {elapsed}s/{QR_TIMEOUT}s", end="", flush=True)
                else:
                    print(f"\n  [FAIL] QR scan timeout")
                    findings["steps"]["qr_login"] = {"success": False}
                    save()
                    return

                await screenshot(tab, "04_after_login")
                logged_in = True

        # 9. Discover recharge page
        print("\n[4] Discovering recharge page...")
        recharge_url = None
        for url in TIKTOK_RECHARGE_CANDIDATES:
            print(f"  Trying: {url}")
            try:
                tab = await browser.get(url)
                await asyncio.sleep(3)
                current = tab.target.url
                print(f"  URL: {current}")

                if "login" in current:
                    print("  -> Redirected to login, skip")
                    continue

                await screenshot(tab, f"05_recharge_{url.split('/')[-1]}")
                elements = await dump_elements(tab, f"recharge_page_{url.split('/')[-1]}")

                # Check if we found package-like elements
                pkg_elements = [e for e in elements if any(kw in (e.get('text', '') + e.get('className', '') + e.get('dataE2e', '')).lower()
                               for kw in ['coin', 'package', 'recharge', 'xu', 'point'])]
                if pkg_elements:
                    print(f"\n  [FOUND] {len(pkg_elements)} package-like elements!")
                    recharge_url = url
                    findings["steps"]["recharge"] = {"url": url, "actual_url": current}
                    break

            except Exception as e:
                print(f"  [ERROR] {e}")

        if not recharge_url:
            # Try from home page
            print("\n  Not found. Trying from home page...")
            tab = await browser.get(TIKTOK_HOME_URL)
            await asyncio.sleep(3)
            await screenshot(tab, "06_home_page")
            await dump_elements(tab, "home_page")

        # 10. Save page HTML for manual inspection
        try:
            html = await tab.get_content()
            html_path = SCREENSHOT_DIR / "page_source.html"
            html_path.write_text(html, encoding='utf-8')
            print(f"\n  [Saved] Page HTML: {html_path}")
        except Exception as e:
            print(f"  [WARN] Could not save HTML: {e}")

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
        findings["error"] = str(e)
    finally:
        save()
        print("\n[Done] Check poc_findings.json + poc_screenshots/")
        print("  Browser stays open 15s...")
        await asyncio.sleep(15)
        try:
            browser.stop()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
