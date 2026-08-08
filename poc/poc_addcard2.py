"""PoC Step 3b: Click Add Card via JS, scroll into view, wait for iframe to load."""
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
        print("\n[2] Go to /coin...", flush=True)
        tab = await browser.get("https://www.tiktok.com/coin")
        await asyncio.sleep(5)

        print("\n[3] Click package + Recharge...", flush=True)
        pkg = await tab.find('[data-e2e="wallet-package-selected"]', timeout=5)
        if pkg: await pkg.click()
        await asyncio.sleep(1)
        btn = await tab.find('[data-e2e="wallet-buy-now-button"]', timeout=5)
        if btn: await btn.click()
        await asyncio.sleep(4)
        print("  [OK] Cashier open", flush=True)
        await shot(tab, "50_cashier")

        print("\n[4] Scroll to Add Card option + click via JS...", flush=True)
        # Scroll the Add Card option into view
        await tab.evaluate("""
            (() => {
                const el = document.querySelector('[data-e2e="payment-method-item-ccdc"]');
                if (el) el.scrollIntoView({behavior: 'smooth', block: 'center'});
            })()
        """)
        await asyncio.sleep(1)

        # Click via JS
        clicked = await tab.evaluate("""
            (() => {
                const el = document.querySelector('[data-e2e="payment-method-item-ccdc"]');
                if (!el) return 'not found';
                el.click();
                const radio = el.querySelector('input[type="radio"]');
                if (radio) radio.click();
                return 'clicked';
            })()
        """)
        print(f"  JS click result: {clicked}", flush=True)
        await asyncio.sleep(3)
        await shot(tab, "51_after_add_card_js")

        # Check radio state
        radio_state = await tab.evaluate("""
            (() => {
                const radios = document.querySelectorAll('input[name="payment-method"]');
                return JSON.stringify(Array.from(radios).map(r => ({checked: r.checked, value: r.value, parent: r.closest('[data-e2e]')?.getAttribute('data-e2e') || ''})));
            })()
        """)
        rs = json.loads(radio_state) if isinstance(radio_state, str) else radio_state
        print(f"  Radio states:", flush=True)
        for r in rs:
            print(f"    {r['parent']}: checked={r['checked']} value={r['value']}", flush=True)
        findings["radio_states"] = rs

        # Check if any new elements appeared (card form)
        print("\n[5] Dumping page after Add Card click...", flush=True)
        js_all = """
        (() => {
            const r = [];
            document.querySelectorAll('input, iframe, [data-e2e], div[class*="card"], div[class*="form"], button[class*="pay"], div[class*="cvv"], div[class*="expire"], div[class*="number"]').forEach((el) => {
                const b = el.getBoundingClientRect();
                if (b.width < 5 || b.height < 5) return;
                r.push({tag: el.tagName.toLowerCase(), e2e: el.getAttribute('data-e2e')||'', text: (el.innerText||'').trim().slice(0,60), id: el.id||'', cls: (el.className||'').toString().slice(0,50), ph: el.getAttribute('placeholder')||'', name: el.getAttribute('name')||'', type: el.getAttribute('type')||'', src: (el.getAttribute('src')||'').slice(0,80), w: Math.round(b.width), h: Math.round(b.height), x: Math.round(b.x), y: Math.round(b.y)});
            });
            return JSON.stringify(r);
        })()
        """
        result = await tab.evaluate(js_all)
        els = json.loads(result) if isinstance(result, str) else result
        # Only show NEW elements (not in coin page)
        known_e2es = {'tiktok-logo','search-box','search-user-input','search-box-button','upload-icon',
                      'top-dm-icon','inbox-icon','profile-icon','wallet-title-get-coins',
                      'wallet-transaction-history-entrance','wallet-user-name','wallet-coins-balance',
                      'wallet-exchange-entrance','wallet-revenue','invite-link-open','copy-code-button',
                      'referral-code','wallet-buy-coins-title','wallet-recharge-advantage-tip',
                      'wallet-coins-packages','wallet-package-selected','wallet-package-coin-icon-0',
                      'wallet-package-coin-num-0','wallet-package-price-0','wallet-package-1',
                      'wallet-package-coin-icon-1','wallet-package-coin-num-1','wallet-package-price-1',
                      'wallet-package-2','wallet-package-coin-icon-2','wallet-package-coin-num-2',
                      'wallet-package-price-2','wallet-package-3','wallet-package-coin-icon-3',
                      'wallet-package-coin-num-3','wallet-package-price-3','wallet-package-4',
                      'wallet-package-coin-icon-4','wallet-package-coin-num-4','wallet-package-price-4',
                      'wallet-package-5','wallet-package-coin-icon-5','wallet-package-coin-num-5',
                      'wallet-package-price-5','wallet-package-6','wallet-package-coin-icon-6',
                      'wallet-package-coin-num-6','wallet-package-price-6','wallet-package-custom',
                      'wallet-package-coin-text-custom','wallet-package-text-custom',
                      'reward-tooltip','reward-info-button','reward-tooltip-button',
                      'reward-status-label','reward-section.popular_deal','reward-item.invite&get_rewards',
                      'reward-link','reward-referral-code','reward-checkbox.219392640260',
                      'wallet-title-payment-method','wallet-payment-icon-VISA','wallet-payment-icon-MASTER',
                      'wallet-payment-icon-DINERS','wallet-payment-icon-DISCOVER','wallet-payment-icon-AMEX',
                      'wallet-payment-icon-MOMO','wallet-payment-icon-ZALOPAY','wallet-payment-icon-BANK_TRANSFER',
                      'wallet-payment-icon-VNPAY_QR','wallet-payment-icon-GOOGLEPAY',
                      'wallet-title-total-price','wallet-total-price','wallet-buy-now-button',
                      'cashier-secure-payment','banner.pc_coin_page_bottom.recharge_referral_evergreen_pc'}

        new_els = [el for el in els if el.get('e2e') not in known_e2es]
        print(f"  Total: {len(els)} elements, {len(new_els)} new:", flush=True)
        for el in new_els:
            parts = [el['tag']]
            if el.get('e2e'): parts.append(f"e2e={el['e2e']}")
            if el.get('text'): parts.append(f'text="{el["text"][:40]}"')
            if el.get('ph'): parts.append(f'ph="{el["ph"]}"')
            if el.get('name'): parts.append(f"name={el['name']}")
            if el.get('type') and el['type'] != 'text': parts.append(f"type={el['type']}")
            if el.get('src'): parts.append(f"src={el['src'][:50]}")
            print(f"  {el['w']}x{el['h']:3d} ({el['x']:4d},{el['y']:4d}) {' | '.join(parts)}", flush=True)
        findings["new_elements"] = new_els

        # Check iframes (maybe card form is inside pipopay iframe that became visible)
        print("\n[6] Checking iframes...", flush=True)
        iframes = await tab.evaluate("""
            (() => {
                const ifs = document.querySelectorAll('iframe');
                return JSON.stringify(Array.from(ifs).map(f => {
                    const b = f.getBoundingClientRect();
                    return {src: f.src||'', id: f.id||'', name: f.name||'', w: Math.round(b.width), h: Math.round(b.height), x: Math.round(b.x), y: Math.round(b.y), style: f.style.cssText || ''};
                }));
            })()
        """)
        ifs = json.loads(iframes) if isinstance(iframes, str) else iframes
        print(f"  {len(ifs)} iframes:", flush=True)
        for f in ifs:
            print(f"    {f['w']}x{f['h']} ({f['x']},{f['y']}) src={f['src'][:80]}", flush=True)
        findings["iframes"] = ifs

        # Check Pay now button
        pay = await tab.evaluate("""
            (() => {
                const b = document.querySelector('[data-e2e="cashier-footer-button"]');
                if (!b) return null;
                const r = b.getBoundingClientRect();
                return JSON.stringify({text: b.innerText, disabled: b.disabled, w: Math.round(r.width), h: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y)});
            })()
        """)
        pay_info = json.loads(pay) if isinstance(pay, str) else pay
        print(f"\n  Pay now: {pay_info}", flush=True)
        findings["pay_now"] = pay_info

        # Wait and check again (iframe might load slowly)
        print("\n[7] Waiting 10s for iframe to load...", flush=True)
        for i in range(10):
            await asyncio.sleep(1)
            iframe_check = await tab.evaluate("""
                (() => {
                    const f = document.querySelector('iframe[src*="pipopay"]');
                    if (!f) return null;
                    const b = f.getBoundingClientRect();
                    return JSON.stringify({w: Math.round(b.width), h: Math.round(b.height)});
                })()
            """)
            ic = json.loads(iframe_check) if isinstance(iframe_check, str) else iframe_check
            if ic and ic.get('w', 0) > 50:
                print(f"  [{i+1}s] pipopay iframe visible! {ic['w']}x{ic['h']}", flush=True)
                await shot(tab, "52_card_form_visible")
                break
            print(f"  [{i+1}s] still 0x0...", flush=True)

        await shot(tab, "53_final")

        # Save HTML for analysis
        html = await tab.get_content()
        (SHOTS / "addcard_final.html").write_text(html, encoding='utf-8')
        print("\n  [Saved] addcard_final.html", flush=True)

    except Exception as e:
        print(f"\n[ERROR] {e}", flush=True)
        import traceback; traceback.print_exc()
        findings["error"] = str(e)
    finally:
        (D / "poc_addcard2_findings.json").write_text(
            json.dumps(findings, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
        print("\n[Done]", flush=True)
        await asyncio.sleep(10)
        try: browser.stop()
        except: pass

if __name__ == "__main__":
    asyncio.run(main())
