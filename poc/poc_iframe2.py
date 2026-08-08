"""PoC Step 3d: Access pipopay iframe via browser.tabs and CDP frame tree."""
import asyncio, json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import nodriver as uc
from nodriver import cdp

D = Path(__file__).parent
SHOTS = D / "poc_screenshots"
SESSION = D / "poc_session"
findings = {}

async def shot(tab, name):
    p = SHOTS / f"{name}.png"
    await tab.save_screenshot(str(p))
    print(f"  [shot] {p.name}", flush=True)

async def dump_elements(tab, label):
    js = """
    (() => {
        const r = [];
        document.querySelectorAll('input, button, select, textarea, form, label, div, span, [placeholder], [data-e2e]').forEach((el) => {
            const b = el.getBoundingClientRect();
            if (b.width < 5 || b.height < 5) return;
            r.push({tag: el.tagName.toLowerCase(), type: el.type||'', name: el.name||'', id: el.id||'', ph: el.placeholder||'', text: (el.innerText||'').trim().slice(0,60), cls: (el.className||'').toString().slice(0,60), autocomplete: el.autocomplete||'', w: Math.round(b.width), h: Math.round(b.height), x: Math.round(b.x), y: Math.round(b.y)});
        });
        return JSON.stringify(r.slice(0, 50));
    })()
    """
    try:
        result = await tab.evaluate(js)
        els = json.loads(result) if isinstance(result, str) else result
        print(f"\n  --- {label}: {len(els)} elements ---", flush=True)
        for el in els:
            parts = [el['tag']]
            if el.get('type'): parts.append(f"type={el['type']}")
            if el.get('ph'): parts.append(f'ph="{el["ph"]}"')
            if el.get('name'): parts.append(f"name={el['name']}")
            if el.get('id'): parts.append(f"id={el['id']}")
            if el.get('text'): parts.append(f'text="{el["text"][:30]}"')
            if el.get('autocomplete'): parts.append(f"ac={el['autocomplete']}")
            print(f"    {el['w']}x{el['h']:3d} ({el['x']:4d},{el['y']:4d}) {' | '.join(parts)}", flush=True)
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
        print("\n[2] Go to /coin, open cashier, select Add Card...", flush=True)
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
        await asyncio.sleep(3)
        print("  [OK] Add Card selected", flush=True)

        # Verify iframe
        iframe_info = await tab.evaluate("""
            (() => {
                const f = document.querySelector('iframe[src*="pipopay"]');
                if (!f) return null;
                const b = f.getBoundingClientRect();
                return JSON.stringify({w: Math.round(b.width), h: Math.round(b.height), src: f.src.slice(0, 100)});
            })()
        """)
        ii = json.loads(iframe_info) if isinstance(iframe_info, str) else iframe_info
        print(f"  iframe: {ii}", flush=True)

        # Method 1: Check browser.tabs for iframe
        print("\n[3] Checking browser.tabs...", flush=True)
        all_tabs = browser.tabs
        print(f"  {len(all_tabs)} tabs:", flush=True)
        for t in all_tabs:
            try:
                url = await t.evaluate("location.href")
            except:
                url = "?"
            print(f"    {type(t).__name__} url={url[:80]}", flush=True)

        # Find pipopay tab
        pipopay_tab = None
        for t in all_tabs:
            try:
                url = await t.evaluate("location.href")
                if "pipopay" in str(url):
                    pipopay_tab = t
                    print(f"\n  [FOUND] pipopay tab: {url[:80]}", flush=True)
                    break
            except:
                continue

        if pipopay_tab:
            print("\n[4] Dumping pipopay iframe content...", flush=True)
            await asyncio.sleep(2)
            await shot(pipopay_tab, "70_pipopay_content")
            await dump_elements(pipopay_tab, "pipopay_iframe")

            # Get full HTML
            try:
                html = await pipopay_tab.get_content()
                (SHOTS / "pipopay_iframe.html").write_text(html, encoding='utf-8')
                print(f"\n  [Saved] pipopay_iframe.html ({len(html)} bytes)", flush=True)
            except Exception as e:
                print(f"  [HTML error] {e}", flush=True)

        # Method 2: CDP page.get_frame_tree
        if not pipopay_tab:
            print("\n[4b] CDP page.get_frame_tree...", flush=True)
            try:
                result = await tab.send(cdp.page.get_frame_tree())
                frame_tree = result[0] if result else None
                if frame_tree:
                    ft = frame_tree
                    print(f"  Main frame: {ft.frame.url[:80]}", flush=True)

                    def walk(frame_tree, depth=0):
                        ft = frame_tree
                        frame = ft.frame
                        print(f"  {'  '*depth}frame: {frame.name or '(unnamed)'} url={frame.url[:80]}", flush=True)
                        for child in ft.child_frames:
                            walk(child, depth+1)

                    walk(frame_tree)

                    # Try to find pipopay frame
                    def find_frame(frame_tree, url_pattern):
                        if url_pattern in frame_tree.frame.url:
                            return frame_tree
                        for child in frame_tree.child_frames:
                            result = find_frame(child, url_pattern)
                            if result:
                                return result
                        return None

                    pipopay_frame = find_frame(frame_tree, "pipopay")
                    if pipopay_frame:
                        print(f"\n  [FOUND] pipopay frame ID: {pipopay_frame.frame.id}", flush=True)

                        # Get execution context for this frame
                        ctx_result = await tab.send(cdp.runtime.evaluate(
                            expression="document.body.innerHTML",
                            context_id=None,
                        ))
                        print(f"  CDP evaluate result: {str(ctx_result)[:200]}", flush=True)
                    else:
                        print("  [WARN] pipopay frame not found in tree", flush=True)
            except Exception as e:
                print(f"  [CDP error] {e}", flush=True)

        # Method 3: Try CDP target.get_targets
        if not pipopay_tab:
            print("\n[5] CDP target.get_targets...", flush=True)
            try:
                result = await tab.send(cdp.target.get_targets())
                targets = result if isinstance(result, list) else [result]
                print(f"  {len(targets)} targets", flush=True)
                for t in targets:
                    if hasattr(t, 'url'):
                        print(f"    {t.type} {t.url[:80]}", flush=True)
                    elif isinstance(t, dict):
                        print(f"    {t.get('type','')} {t.get('url','')[:80]}", flush=True)
            except Exception as e:
                print(f"  [error] {e}", flush=True)

        await shot(tab, "71_final")

    except Exception as e:
        print(f"\n[ERROR] {e}", flush=True)
        import traceback; traceback.print_exc()
        findings["error"] = str(e)
    finally:
        (D / "poc_iframe2_findings.json").write_text(
            json.dumps(findings, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
        print("\n[Done]", flush=True)
        await asyncio.sleep(10)
        try: browser.stop()
        except: pass

if __name__ == "__main__":
    asyncio.run(main())
