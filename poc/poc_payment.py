"""PoC Step 2: Minimal — go to /coin, click package, click Recharge, dump result."""
import asyncio, json, sys, time
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import nodriver as uc

D = Path(__file__).parent
SHOTS = D / "poc_screenshots"
SESSION = D / "poc_session"
findings = {}

async def shot(tab, name):
    p = SHOTS / f"{name}.png"
    await tab.save_screenshot(str(p))
    print(f"  [shot] {p.name}", flush=True)

async def dump(tab, label):
    js = """
    (() => {
        const r = [];
        document.querySelectorAll('button, a, [role="button"], input, [data-e2e], form, select, textarea, iframe, canvas, div[class*="payment"], div[class*="card"], div[class*="otp"]').forEach((el) => {
            const b = el.getBoundingClientRect();
            if (b.width < 5 || b.height < 5) return;
            r.push({tag: el.tagName.toLowerCase(), e2e: el.getAttribute('data-e2e')||'', text: (el.innerText||'').trim().slice(0,80), id: el.id||'', cls: (el.className||'').toString().slice(0,50), ph: el.getAttribute('placeholder')||'', name: el.getAttribute('name')||'', src: (el.getAttribute('src')||'').slice(0,80), disabled: el.disabled||false, w: Math.round(b.width), h: Math.round(b.height), x: Math.round(b.x), y: Math.round(b.y)});
        });
        return JSON.stringify(r);
    })()
    """
    try:
        result = await tab.evaluate(js)
        els = json.loads(result) if isinstance(result, str) else result
        print(f"\n--- {label}: {len(els)} elements ---", flush=True)
        for el in els:
            parts = [el['tag']]
            if el.get('e2e'): parts.append(f"e2e={el['e2e']}")
            if el.get('text'): parts.append(f'text="{el["text"][:40]}"')
            if el.get('ph'): parts.append(f'ph="{el["ph"]}"')
            if el.get('name'): parts.append(f"name={el['name']}")
            if el.get('src'): parts.append(f"src={el['src'][:40]}")
            if el.get('disabled'): parts.append("DISABLED")
            print(f"  {el['w']}x{el['h']:3d} ({el['x']:4d},{el['y']:4d}) {' | '.join(parts)}", flush=True)
        findings[label] = els
        return els
    except Exception as e:
        print(f"  [dump error] {e}", flush=True)
        return []

async def main():
    print("[1] Start browser...", flush=True)
    browser = await uc.start(
        browser_args=["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"],
        user_data_dir=str(SESSION),
    )
    print("  OK", flush=True)

    try:
        print("\n[2] Go to /coin...", flush=True)
        tab = await browser.get("https://www.tiktok.com/coin")
        await asyncio.sleep(5)
        print(f"  URL: {await tab.evaluate('location.href')}", flush=True)
        await shot(tab, "30_coin")

        # Check login state
        logged_in = await tab.evaluate("""
            (() => {
                const loginBtn = document.querySelector('[data-e2e="top-login-button"]');
                return loginBtn ? false : true;
            })()
        """)
        print(f"  Logged in: {logged_in}", flush=True)

        if not logged_in:
            print("  [WARN] Not logged in! Checking for packages anyway...", flush=True)

        await dump(tab, "coin_page")

        # Try clicking package
        print("\n[3] Clicking package 0...", flush=True)
        pkg = None
        for sel in ['[data-e2e="wallet-package-0"]', '[data-e2e="wallet-package-selected"]']:
            try:
                pkg = await tab.find(sel, timeout=3)
                if pkg:
                    print(f"  Found: {sel}", flush=True)
                    break
            except: pass

        if pkg:
            await pkg.click()
            await asyncio.sleep(1)
            await shot(tab, "31_pkg_selected")
        else:
            print("  [WARN] No package found", flush=True)

        # Read total price
        total = await tab.evaluate("""
            (() => {
                const el = document.querySelector('[data-e2e="wallet-total-price"]');
                return el ? el.innerText : 'N/A';
            })()
        """)
        print(f"  Total: {total}", flush=True)

        # Click Recharge
        print("\n[4] Clicking Recharge...", flush=True)
        try:
            btn = await tab.find('[data-e2e="wallet-buy-now-button"]', timeout=3)
            if btn:
                is_disabled = await tab.evaluate("""
                    (() => {
                        const b = document.querySelector('[data-e2e="wallet-buy-now-button"]');
                        return b ? b.disabled : true;
                    })()
                """)
                print(f"  Button disabled: {is_disabled}", flush=True)

                if not is_disabled:
                    await btn.click()
                    print("  [OK] Clicked!", flush=True)
                    await asyncio.sleep(5)
                    await shot(tab, "32_after_recharge")
                    await dump(tab, "payment_page")

                    # Check iframes
                    iframes = await tab.evaluate("""
                        (() => {
                            const ifs = document.querySelectorAll('iframe');
                            return JSON.stringify(Array.from(ifs).map(f => ({src: f.src||'', id: f.id||'', w: f.offsetWidth, h: f.offsetHeight})));
                        })()
                    """)
                    ifs = json.loads(iframes) if isinstance(iframes, str) else iframes
                    print(f"\n  Iframes: {len(ifs)}", flush=True)
                    for f in ifs:
                        print(f"    {f['w']}x{f['h']} src={f['src'][:60]}", flush=True)
                    findings["iframes"] = ifs

                    # Check OTP
                    otp = await tab.evaluate("""
                        (() => {
                            const kws = ['otp', 'verification', 'one-time', 'security code', '3ds', 'authenticate'];
                            const found = [];
                            for (const el of document.querySelectorAll('*')) {
                                const t = (el.innerText||'').toLowerCase();
                                for (const k of kws) if (t.includes(k) && el.offsetWidth > 10) { found.push({tag: el.tagName.toLowerCase(), kw: k, text: t.slice(0,60)}); break; }
                            }
                            return JSON.stringify(found.slice(0, 5));
                        })()
                    """)
                    otp_list = json.loads(otp) if isinstance(otp, str) else otp
                    print(f"\n  OTP/3DS: {'DETECTED' if otp_list else 'None'}", flush=True)
                    findings["otp"] = otp_list

                    # Check CAPTCHA
                    captcha = await tab.evaluate("""
                        (() => {
                            const kws = ['captcha', 'verify-image', 'slider', 'geetest', 'secsdk', 'tcaptcha'];
                            const found = [];
                            for (const el of document.querySelectorAll('*')) {
                                const c = (el.className||'').toString().toLowerCase();
                                const id = (el.id||'').toLowerCase();
                                for (const k of kws) if (c.includes(k) || id.includes(k)) { found.push({tag: el.tagName.toLowerCase(), kw: k, cls: c.slice(0,40)}); break; }
                            }
                            return JSON.stringify(found.slice(0, 5));
                        })()
                    """)
                    cap_list = json.loads(captcha) if isinstance(captcha, str) else captcha
                    print(f"  CAPTCHA: {'DETECTED' if cap_list else 'None'}", flush=True)
                    findings["captcha"] = cap_list

                    # Monitor 30s for redirects
                    print("\n[5] Monitoring 30s for redirects...", flush=True)
                    for i in range(30):
                        await asyncio.sleep(1)
                        url = await tab.evaluate("location.href")
                        if isinstance(url, str) and 'coin' not in url:
                            print(f"  [{i+1}s] Redirect: {url}", flush=True)
                            await shot(tab, "33_redirect")
                            await dump(tab, "redirect_page")
                            break
                else:
                    print("  [SKIP] Button disabled (not logged in)", flush=True)
                    print("  Need to login first via poc_discover.py", flush=True)
            else:
                print("  [WARN] Recharge button not found", flush=True)
        except Exception as e:
            print(f"  [ERR] {e}", flush=True)

        # Save HTML
        try:
            html = await tab.get_content()
            (SHOTS / "payment_page.html").write_text(html, encoding='utf-8')
            print("\n  [Saved] payment_page.html", flush=True)
        except: pass

    except Exception as e:
        print(f"\n[ERROR] {e}", flush=True)
        import traceback; traceback.print_exc()
    finally:
        (D / "poc_payment_findings.json").write_text(
            json.dumps(findings, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
        print("\n[Done] Findings saved.", flush=True)
        await asyncio.sleep(5)
        try: browser.stop()
        except: pass

if __name__ == "__main__":
    asyncio.run(main())
