"""PoC Step 3g: Navigate directly to pipopay iframe URL to see card form."""
import asyncio, json, sys
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

async def main():
    print("[1] Start browser...", flush=True)
    browser = await uc.start(
        browser_args=["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"],
        user_data_dir=str(SESSION),
    )
    print("  OK", flush=True)

    try:
        # Step 1: Go to /coin, open cashier, select Add Card, get iframe URL
        print("\n[2] Go to /coin, open cashier...", flush=True)
        tab = await browser.get("https://www.tiktok.com/coin")
        await asyncio.sleep(5)

        pkg = await tab.find('[data-e2e="wallet-package-selected"]', timeout=5)
        if pkg: await pkg.click()
        await asyncio.sleep(1)
        btn = await tab.find('[data-e2e="wallet-buy-now-button"]', timeout=5)
        if btn: await btn.click()
        await asyncio.sleep(4)

        await tab.evaluate("""
            (() => {
                const el = document.querySelector('[data-e2e="payment-method-item-ccdc"]');
                if (el) { el.scrollIntoView({block: 'center'}); el.click(); }
                const r = el?.querySelector('input[type="radio"]');
                if (r) r.click();
            })()
        """)
        await asyncio.sleep(5)
        print("  [OK] Add Card selected", flush=True)

        # Get full iframe URL
        iframe_src = await tab.evaluate("""
            (() => {
                const f = document.querySelector('iframe[src*="pipopay"]');
                return f ? f.src : null;
            })()
        """)
        print(f"  iframe src: {iframe_src}", flush=True)

        if not iframe_src:
            print("  [FAIL] No pipopay iframe", flush=True)
            return

        # Step 2: Open pipopay URL in a new tab
        print(f"\n[3] Navigating to pipopay URL directly...", flush=True)
        tab2 = await browser.get(iframe_src)
        await asyncio.sleep(5)
        url = await tab2.evaluate("location.href")
        print(f"  URL: {url}", flush=True)
        await shot(tab2, "90_pipopay_direct")

        # Dump all elements
        print("\n[4] Dumping pipopay card form...", flush=True)
        js = """
        (() => {
            const r = [];
            document.querySelectorAll('input, button, select, textarea, form, label, div[class*="card"], div[class*="number"], div[class*="expire"], div[class*="cvv"], div[class*="cvc"], div[class*="holder"], div[class*="name"], span[class*="label"], [placeholder]').forEach((el) => {
                const b = el.getBoundingClientRect();
                if (b.width < 5 || b.height < 5) return;
                r.push({tag: el.tagName.toLowerCase(), type: el.type||'', name: el.name||'', id: el.id||'', ph: el.placeholder||'', text: (el.innerText||'').trim().slice(0,60), cls: (el.className||'').toString().slice(0,60), autocomplete: el.autocomplete||'', w: Math.round(b.width), h: Math.round(b.height), x: Math.round(b.x), y: Math.round(b.y)});
            });
            return JSON.stringify(r);
        })()
        """
        result = await tab2.evaluate(js)
        els = json.loads(result) if isinstance(result, str) else result
        print(f"  {len(els)} elements:", flush=True)
        for el in els:
            parts = [el['tag']]
            if el.get('type'): parts.append(f"type={el['type']}")
            if el.get('ph'): parts.append(f'ph="{el["ph"]}"')
            if el.get('name'): parts.append(f"name={el['name']}")
            if el.get('id'): parts.append(f"id={el['id']}")
            if el.get('text'): parts.append(f'text="{el["text"][:40]}"')
            if el.get('autocomplete'): parts.append(f"ac={el['autocomplete']}")
            print(f"    {el['w']}x{el['h']:3d} ({el['x']:4d},{el['y']:4d}) {' | '.join(parts)}", flush=True)
        findings["pipopay_elements"] = els

        # Get full HTML
        html = await tab2.get_content()
        (SHOTS / "pipopay_direct.html").write_text(html, encoding='utf-8')
        print(f"\n  [Saved] pipopay_direct.html ({len(html)} bytes)", flush=True)

        # Take full page screenshot
        await shot(tab2, "91_pipopay_full")

    except Exception as e:
        print(f"\n[ERROR] {e}", flush=True)
        import traceback; traceback.print_exc()
        findings["error"] = str(e)
    finally:
        (D / "poc_pipopay_findings.json").write_text(
            json.dumps(findings, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
        print("\n[Done]", flush=True)
        await asyncio.sleep(10)
        try: browser.stop()
        except: pass

if __name__ == "__main__":
    asyncio.run(main())
