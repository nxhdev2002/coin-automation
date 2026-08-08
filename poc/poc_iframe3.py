"""PoC Step 3e: Access pipopay iframe via CDP execution contexts."""
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
        await asyncio.sleep(4)
        print("  [OK] Add Card selected, iframe should be visible", flush=True)

        # Get frame tree
        print("\n[3] CDP page.get_frame_tree...", flush=True)
        frame_tree = await tab.send(cdp.page.get_frame_tree())
        # frame_tree is a FrameTree object directly

        def walk(ft, depth=0):
            frame = ft.frame
            fid = getattr(frame, 'id_', None) or getattr(frame, 'id', None) or '?'
            print(f"  {'  '*depth}id={fid} name='{frame.name}' url={frame.url[:80]}", flush=True)
            for child in (ft.child_frames or []):
                walk(child, depth+1)

        walk(frame_tree)
        findings["frame_tree"] = {}

        # Find pipopay frame
        def find_frame(ft, pattern):
            if pattern in ft.frame.url:
                return ft
            for child in (ft.child_frames or []):
                r = find_frame(child, pattern)
                if r: return r
            return None

        pipopay_ft = find_frame(frame_tree, "pipopay")
        if not pipopay_ft:
            print("  [FAIL] No pipopay frame found", flush=True)
            return

        pipopay_frame_id = getattr(pipopay_ft.frame, 'id_', None) or getattr(pipopay_ft.frame, 'id', None)
        print(f"\n  [FOUND] pipopay frame ID: {pipopay_frame_id}", flush=True)

        # Get execution contexts
        print("\n[4] Getting execution contexts...", flush=True)
        # Enable Runtime to get execution contexts
        await tab.send(cdp.runtime.enable())

        # Get all execution contexts via Runtime.evaluate with includeCommandLineAPI
        # Actually, we need to listen for executionContextCreated events
        # Or we can try to evaluate JS in the iframe context using isolated world

        # Method: Create an isolated world in the pipopay frame
        print("\n[5] Creating isolated world in pipopay frame...", flush=True)
        try:
            result = await tab.send(cdp.page.create_isolated_world(
                frame_id=pipopay_frame_id,
                world_name="pipopay_ctx",
                grant_universal_access=True
            ))
            # Result is ExecutionContextId (int)
            ctx_id = result[0] if isinstance(result, (list, tuple)) else result
            print(f"  Context ID: {ctx_id}", flush=True)

            # Now evaluate JS in this isolated world to get iframe content
            print("\n[6] Evaluating JS in isolated world...", flush=True)
            js = """
            (() => {
                const r = [];
                document.querySelectorAll('input, button, select, textarea, form, label, div, span, [placeholder]').forEach((el) => {
                    const b = el.getBoundingClientRect();
                    if (b.width < 5 || b.height < 5) return;
                    r.push({tag: el.tagName.toLowerCase(), type: el.type||'', name: el.name||'', id: el.id||'', ph: el.placeholder||'', text: (el.innerText||'').trim().slice(0,60), cls: (el.className||'').toString().slice(0,60), autocomplete: el.autocomplete||'', w: Math.round(b.width), h: Math.round(b.height), x: Math.round(b.x), y: Math.round(b.y)});
                });
                return JSON.stringify(r.slice(0, 50));
            })()
            """

            eval_result = await tab.send(cdp.runtime.evaluate(
                expression=js,
                context_id=int(ctx_id) if ctx_id else None,
                return_by_value=True,
            ))
            # eval_result is a tuple (RemoteObject, ExceptionDetails)
            remote_obj, exc_details = eval_result if isinstance(eval_result, (list, tuple)) else (eval_result, None)

            if exc_details:
                print(f"  [EXCEPTION] {exc_details}", flush=True)
            elif remote_obj:
                value = remote_obj.value if hasattr(remote_obj, 'value') else str(remote_obj)
                if isinstance(value, str) and value:
                    els = json.loads(value)
                else:
                    els = value if isinstance(value, list) else []

                print(f"\n  --- pipopay iframe: {len(els)} elements ---", flush=True)
                for el in els:
                    parts = [el['tag']]
                    if el.get('type'): parts.append(f"type={el['type']}")
                    if el.get('ph'): parts.append(f'ph="{el["ph"]}"')
                    if el.get('name'): parts.append(f"name={el['name']}")
                    if el.get('id'): parts.append(f"id={el['id']}")
                    if el.get('text'): parts.append(f'text="{el["text"][:30]}"')
                    if el.get('autocomplete'): parts.append(f"ac={el['autocomplete']}")
                    print(f"    {el['w']}x{el['h']:3d} ({el['x']:4d},{el['y']:4d}) {' | '.join(parts)}", flush=True)
                findings["pipopay_elements"] = els

                # Get full HTML
                html_result = await tab.send(cdp.runtime.evaluate(
                    expression="document.documentElement.outerHTML",
                    context_id=int(ctx_id) if ctx_id else None,
                    return_by_value=True,
                ))
                html_obj, _ = html_result if isinstance(html_result, (list, tuple)) else (html_result, None)
                html_val = html_obj.value if html_obj and hasattr(html_obj, 'value') else str(html_obj)
                (SHOTS / "pipopay_iframe.html").write_text(html_val, encoding='utf-8')
                print(f"\n  [Saved] pipopay_iframe.html ({len(html_val)} bytes)", flush=True)

        except Exception as e:
            print(f"  [ERROR] {e}", flush=True)
            import traceback; traceback.print_exc()

        await shot(tab, "80_final")

    except Exception as e:
        print(f"\n[ERROR] {e}", flush=True)
        import traceback; traceback.print_exc()
        findings["error"] = str(e)
    finally:
        (D / "poc_iframe3_findings.json").write_text(
            json.dumps(findings, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
        print("\n[Done]", flush=True)
        await asyncio.sleep(10)
        try: browser.stop()
        except: pass

if __name__ == "__main__":
    asyncio.run(main())
