"""PoC Step 3c: Access pipopay iframe to find card input fields (card number, expiry, CVV)."""
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
        print("\n[2] Go to /coin, open cashier, select Add Card...", flush=True)
        tab = await browser.get("https://www.tiktok.com/coin")
        await asyncio.sleep(5)

        # Select package + Recharge
        pkg = await tab.find('[data-e2e="wallet-package-selected"]', timeout=5)
        if pkg: await pkg.click()
        await asyncio.sleep(1)
        btn = await tab.find('[data-e2e="wallet-buy-now-button"]', timeout=5)
        if btn: await btn.click()
        await asyncio.sleep(4)
        print("  [OK] Cashier open", flush=True)

        # Click Add Card via JS
        await tab.evaluate("""
            (() => {
                const el = document.querySelector('[data-e2e="payment-method-item-ccdc"]');
                if (el) { el.scrollIntoView({block: 'center'}); el.click(); }
                const r = el?.querySelector('input[type="radio"]');
                if (r) r.click();
            })()
        """)
        await asyncio.sleep(3)
        print("  [OK] Add Card selected", flush=True)

        # Verify iframe visible
        iframe_info = await tab.evaluate("""
            (() => {
                const f = document.querySelector('iframe[src*="pipopay"]');
                if (!f) return null;
                const b = f.getBoundingClientRect();
                return JSON.stringify({w: Math.round(b.width), h: Math.round(b.height), x: Math.round(b.x), y: Math.round(b.y), src: f.src.slice(0, 120)});
            })()
        """)
        ii = json.loads(iframe_info) if isinstance(iframe_info, str) else iframe_info
        print(f"  pipopay iframe: {ii}", flush=True)

        if not ii or ii.get('w', 0) < 50:
            print("  [FAIL] iframe not visible", flush=True)
            return

        await shot(tab, "60_cashier_with_card_form")

        # Use nodriver to get iframe tab
        print("\n[3] Trying tab.get_iframe...", flush=True)
        try:
            iframe_tab = await tab.get_iframe('iframe[src*="pipopay"]', timeout=10)
            if iframe_tab:
                print(f"  [OK] Got iframe tab: {iframe_tab}", flush=True)

                # Wait for iframe to fully load
                await asyncio.sleep(3)
                await shot(iframe_tab, "61_iframe_content")

                # Dump all elements inside iframe
                print("\n[5] Dumping iframe elements...", flush=True)
                js_iframe = """
                (() => {
                    const r = [];
                    document.querySelectorAll('input, button, select, textarea, form, div[class*="card"], div[class*="number"], div[class*="expire"], div[class*="cvv"], div[class*="cvc"], div[class*="name"], div[class*="holder"], label, [data-e2e], [placeholder]').forEach((el) => {
                        const b = el.getBoundingClientRect();
                        if (b.width < 5 || b.height < 5) return;
                        r.push({tag: el.tagName.toLowerCase(), type: el.type||'', name: el.name||'', id: el.id||'', ph: el.placeholder||'', text: (el.innerText||'').trim().slice(0,60), cls: (el.className||'').toString().slice(0,60), autocomplete: el.autocomplete||'', w: Math.round(b.width), h: Math.round(b.height), x: Math.round(b.x), y: Math.round(b.y)});
                    });
                    return JSON.stringify(r);
                })()
                """
                result = await iframe_tab.evaluate(js_iframe)
                els = json.loads(result) if isinstance(result, str) else result
                print(f"  {len(els)} elements inside iframe:", flush=True)
                for el in els:
                    parts = [el['tag']]
                    if el.get('type'): parts.append(f"type={el['type']}")
                    if el.get('ph'): parts.append(f'ph="{el["ph"]}"')
                    if el.get('name'): parts.append(f"name={el['name']}")
                    if el.get('id'): parts.append(f"id={el['id']}")
                    if el.get('text'): parts.append(f'text="{el["text"][:30]}"')
                    if el.get('autocomplete'): parts.append(f"ac={el['autocomplete']}")
                    if el.get('cls'): parts.append(f"cls={el['cls'][:30]}")
                    print(f"    {el['w']}x{el['h']:3d} ({el['x']:4d},{el['y']:4d}) {' | '.join(parts)}", flush=True)
                findings["iframe_elements"] = els

                # Get full HTML of iframe
                try:
                    html = await iframe_tab.get_content()
                    (SHOTS / "pipopay_iframe.html").write_text(html, encoding='utf-8')
                    print(f"\n  [Saved] pipopay_iframe.html ({len(html)} bytes)", flush=True)
                except Exception as e:
                    print(f"  [HTML error] {e}", flush=True)

                # Try screenshot of just the iframe
                await shot(iframe_tab, "62_iframe_only")
            else:
                print("  [FAIL] get_iframe returned None", flush=True)

        except Exception as e:
            print(f"  [ERROR] get_iframe: {e}", flush=True)

        # Method 3: Fallback — try CDP target.get_targets
        if "iframe_elements" not in findings:
            print("\n[5] Fallback: CDP target.get_targets...", flush=True)
            try:
                cdp_result = await tab.send(uc.cdp.target.get_targets())
                targets = cdp_result[0] if cdp_result else []
                print(f"  {len(targets)} targets:", flush=True)
                for t in targets:
                    print(f"    {t.type} {t.url[:80]}", flush=True)
            except Exception as e:
                print(f"  [CDP error] {e}", flush=True)

        # Method 4: Fallback — try CDP page.get_frame_tree
        if "iframe_elements" not in findings:
            print("\n[6] Fallback: trying CDP directly...", flush=True)
            try:
                # Get all frame targets via CDP
                cdp = await tab.send(uc.cdp.target.get_targets())
                print(f"  CDP targets: {len(cdp[0]) if cdp else 0}", flush=True)
                for t in (cdp[0] if cdp else []):
                    print(f"    {t.type} {t.url[:80]}", flush=True)
            except Exception as e:
                print(f"  [CDP error] {e}", flush=True)

        await shot(tab, "63_final")

    except Exception as e:
        print(f"\n[ERROR] {e}", flush=True)
        import traceback; traceback.print_exc()
        findings["error"] = str(e)
    finally:
        (D / "poc_iframe_findings.json").write_text(
            json.dumps(findings, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
        print("\n[Done]", flush=True)
        await asyncio.sleep(10)
        try: browser.stop()
        except: pass

if __name__ == "__main__":
    asyncio.run(main())
