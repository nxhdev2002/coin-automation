"""PoC Step 3: Add new card flow — select 'Add Credit Or Debit Card', dump card form."""
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

async def dump(tab, label):
    js = """
    (() => {
        const r = [];
        document.querySelectorAll('button, a, [role="button"], input, [data-e2e], form, select, textarea, iframe, canvas, div[class*="card"], div[class*="payment"], div[class*="otp"], div[class*="cvv"], div[class*="expire"], div[class*="number"]').forEach((el) => {
            const b = el.getBoundingClientRect();
            if (b.width < 5 || b.height < 5) return;
            r.push({tag: el.tagName.toLowerCase(), e2e: el.getAttribute('data-e2e')||'', text: (el.innerText||'').trim().slice(0,80), id: el.id||'', cls: (el.className||'').toString().slice(0,60), ph: el.getAttribute('placeholder')||'', name: el.getAttribute('name')||'', type: el.getAttribute('type')||'', src: (el.getAttribute('src')||'').slice(0,80), disabled: el.disabled||false, w: Math.round(b.width), h: Math.round(b.height), x: Math.round(b.x), y: Math.round(b.y)});
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
            if el.get('type'): parts.append(f"type={el['type']}")
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
        await shot(tab, "40_coin")

        print("\n[3] Clicking package (30 coins)...", flush=True)
        pkg = await tab.find('[data-e2e="wallet-package-selected"]', timeout=5)
        if pkg:
            await pkg.click()
            await asyncio.sleep(1)
            print("  [OK] Package selected", flush=True)
        else:
            print("  [WARN] No package", flush=True)

        print("\n[4] Clicking Recharge...", flush=True)
        btn = await tab.find('[data-e2e="wallet-buy-now-button"]', timeout=5)
        if btn:
            await btn.click()
            await asyncio.sleep(4)
            print("  [OK] Recharge clicked", flush=True)
            await shot(tab, "41_cashier")
        else:
            print("  [WARN] No Recharge button", flush=True)
            return

        print("\n[5] Selecting 'Add Credit Or Debit Card'...", flush=True)
        add_card = await tab.find('[data-e2e="payment-method-item-ccdc"]', timeout=5)
        if add_card:
            await add_card.click()
            await asyncio.sleep(3)
            print("  [OK] Add Card clicked", flush=True)
            await shot(tab, "42_add_card_form")
            await dump(tab, "add_card_form")
        else:
            print("  [WARN] Add Card option not found", flush=True)
            await dump(tab, "cashier_no_add_card")
            return

        # Check if card form appeared inside iframe
        print("\n[6] Checking iframes for card form...", flush=True)
        iframes = await tab.evaluate("""
            (() => {
                const ifs = document.querySelectorAll('iframe');
                return JSON.stringify(Array.from(ifs).map(f => {
                    const b = f.getBoundingClientRect();
                    return {src: f.src||'', id: f.id||'', name: f.name||'', w: Math.round(b.width), h: Math.round(b.height), x: Math.round(b.x), y: Math.round(b.y)};
                }));
            })()
        """)
        ifs = json.loads(iframes) if isinstance(iframes, str) else iframes
        print(f"  {len(ifs)} iframes:", flush=True)
        for f in ifs:
            print(f"    {f['w']}x{f['h']} ({f['x']},{f['y']}) src={f['src'][:80]}", flush=True)
        findings["add_card_iframes"] = ifs

        # Check for visible inputs (card number, expiry, CVV)
        print("\n[7] Looking for card input fields...", flush=True)
        card_inputs = await tab.evaluate("""
            (() => {
                const inputs = document.querySelectorAll('input');
                const result = [];
                for (const inp of inputs) {
                    const b = inp.getBoundingClientRect();
                    if (b.width < 20 || b.height < 10) continue;
                    result.push({
                        tag: 'input',
                        type: inp.type || '',
                        name: inp.name || '',
                        id: inp.id || '',
                        ph: inp.placeholder || '',
                        cls: (inp.className||'').slice(0, 50),
                        autocomplete: inp.autocomplete || '',
                        w: Math.round(b.width), h: Math.round(b.height),
                        x: Math.round(b.x), y: Math.round(b.y),
                        visible: b.width > 20 && b.height > 10
                    });
                }
                return JSON.stringify(result);
            })()
        """)
        ci = json.loads(card_inputs) if isinstance(card_inputs, str) else card_inputs
        print(f"  {len(ci)} visible inputs:", flush=True)
        for i in ci:
            label_parts = []
            if i.get('ph'): label_parts.append(f'ph="{i["ph"]}"')
            if i.get('name'): label_parts.append(f"name={i['name']}")
            if i.get('id'): label_parts.append(f"id={i['id']}")
            if i.get('autocomplete'): label_parts.append(f"ac={i['autocomplete']}")
            if i.get('type') and i['type'] != 'text': label_parts.append(f"type={i['type']}")
            print(f"    {i['w']}x{i['h']} ({i['x']},{i['y']}) {' | '.join(label_parts)}", flush=True)
        findings["card_inputs"] = ci

        # Check Pay now button state
        pay_btn = await tab.evaluate("""
            (() => {
                const b = document.querySelector('[data-e2e="cashier-footer-button"]');
                if (!b) return null;
                const r = b.getBoundingClientRect();
                return JSON.stringify({text: b.innerText, disabled: b.disabled, w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y)});
            })()
        """)
        pay_info = json.loads(pay_btn) if isinstance(pay_btn, str) else pay_btn
        print(f"\n  Pay now button: {pay_info}", flush=True)
        findings["pay_now_button"] = pay_info

        # OTP/3DS check
        otp = await tab.evaluate("""
            (() => {
                const kws = ['otp', 'verification', 'one-time', 'security code', '3ds', '3d secure', 'authenticate', 'verify'];
                const found = [];
                for (const el of document.querySelectorAll('*')) {
                    const t = (el.innerText||'').toLowerCase();
                    for (const k of kws) if (t.includes(k) && el.offsetWidth > 10) { found.push({tag: el.tagName.toLowerCase(), kw: k, text: t.slice(0,60)}); break; }
                }
                return JSON.stringify(found.slice(0, 5));
            })()
        """)
        otp_list = json.loads(otp) if isinstance(otp, str) else otp
        print(f"  OTP/3DS: {'DETECTED' if otp_list else 'None'}", flush=True)
        findings["otp"] = otp_list

        # CAPTCHA check
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

        await shot(tab, "43_final")
        await dump(tab, "final_page")

        # Save HTML
        html = await tab.get_content()
        (SHOTS / "add_card_page.html").write_text(html, encoding='utf-8')
        print("\n  [Saved] add_card_page.html", flush=True)

    except Exception as e:
        print(f"\n[ERROR] {e}", flush=True)
        import traceback; traceback.print_exc()
        findings["error"] = str(e)
    finally:
        (D / "poc_addcard_findings.json").write_text(
            json.dumps(findings, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
        print("\n[Done] Findings saved.", flush=True)
        await asyncio.sleep(10)
        try: browser.stop()
        except: pass

if __name__ == "__main__":
    asyncio.run(main())
