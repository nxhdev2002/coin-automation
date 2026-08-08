"""PoC Step 3f: Find pipopay via CDP target.get_targets, inspect attributes."""
import asyncio, json, sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import nodriver as uc
from nodriver import cdp

D = Path(__file__).parent
SHOTS = D / "poc_screenshots"
SESSION = D / "poc_session"

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
        await asyncio.sleep(5)
        print("  [OK] Add Card selected", flush=True)

        # Get all targets
        print("\n[3] CDP target.get_targets...", flush=True)
        result = await tab.send(cdp.target.get_targets())
        targets = result[0] if isinstance(result, (list, tuple)) else result
        if not isinstance(targets, list):
            targets = [targets]

        print(f"  {len(targets)} targets:", flush=True)
        for i, t in enumerate(targets):
            # Dump all attributes
            attrs = {k: v for k, v in vars(t).items() if not k.startswith('_')}
            url = attrs.get('url', '')
            print(f"\n  [{i}] attrs: {json.dumps(attrs, default=str, indent=2)[:300]}", flush=True)

        # Find pipopay target
        pipopay_target = None
        for t in targets:
            attrs = {k: v for k, v in vars(t).items() if not k.startswith('_')}
            url = str(attrs.get('url', ''))
            if 'pipopay' in url:
                pipopay_target = t
                print(f"\n  [FOUND] pipopay target: {url[:80]}", flush=True)
                break

        if not pipopay_target:
            print("\n  [WARN] No pipopay target. Dumping all target URLs...", flush=True)
            for t in targets:
                attrs = {k: v for k, v in vars(t).items() if not k.startswith('_')}
                print(f"    url={attrs.get('url','')[:80]} type={attrs.get('type_','')}", flush=True)

        # Also try frame tree again after longer wait
        print("\n[4] Frame tree (after 5s wait)...", flush=True)
        await asyncio.sleep(5)
        frame_tree = await tab.send(cdp.page.get_frame_tree())

        def walk(ft, depth=0):
            frame = ft.frame
            fid = getattr(frame, 'id_', None) or getattr(frame, 'id', None) or '?'
            print(f"  {'  '*depth}frame: id={fid} url={frame.url[:80]}", flush=True)
            for child in (ft.child_frames or []):
                walk(child, depth+1)

        walk(frame_tree)

        # Also try Runtime.enable + get execution contexts
        print("\n[5] Runtime contexts...", flush=True)
        # Enable runtime to receive context events
        await tab.send(cdp.runtime.enable())

        # Try to get all execution contexts
        # In CDP, we need to listen for executionContextCreated events
        # But nodriver might have a way to get current contexts

        # Try evaluating in all frames via isolated worlds
        # First, let's try to get all frames from the frame tree
        all_frames = []

        def collect_frames(ft):
            all_frames.append(ft.frame)
            for child in (ft.child_frames or []):
                collect_frames(child)

        collect_frames(frame_tree)

        print(f"  {len(all_frames)} frames total", flush=True)
        for f in all_frames:
            fid = getattr(f, 'id_', None) or getattr(f, 'id', None) or '?'
            print(f"    {fid} url={f.url[:80]}", flush=True)

        # Try creating isolated world in each frame
        for f in all_frames:
            fid = getattr(f, 'id_', None) or getattr(f, 'id', None)
            url = f.url
            if 'pipopay' in url or 'pay' in url:
                print(f"\n  [FOUND] pay frame: {fid} url={url[:80]}", flush=True)
                ctx_result = await tab.send(cdp.page.create_isolated_world(
                    frame_id=fid,
                    world_name="pay_ctx",
                    grant_univeral_access=True
                ))
                ctx_id = ctx_result[0] if isinstance(ctx_result, (list, tuple)) else ctx_result
                print(f"  Context: {ctx_id} (type: {type(ctx_id).__name__})", flush=True)

                js = "document.documentElement.outerHTML.slice(0, 500)"
                eval_result = await tab.send(cdp.runtime.evaluate(
                    expression=js,
                    context_id=ctx_id,
                    return_by_value=True,
                ))
                remote_obj, exc = eval_result if isinstance(eval_result, (list, tuple)) else (eval_result, None)
                if exc:
                    print(f"  [EXC] {exc}", flush=True)
                else:
                    val = remote_obj.value if remote_obj and hasattr(remote_obj, 'value') else str(remote_obj)
                    print(f"  HTML preview: {str(val)[:200]}", flush=True)

        # If no pay frame in tree, try creating isolated world on the main frame
        # and accessing the iframe via contentDocument (might work with CDP)
        if not any('pay' in f.url for f in all_frames):
            print("\n[6] No pay frame. Trying contentDocument access via CDP...", flush=True)
            main_fid = getattr(frame_tree.frame, 'id_', None) or getattr(frame_tree.frame, 'id', None)
            ctx_result = await tab.send(cdp.page.create_isolated_world(
                frame_id=main_fid,
                world_name="main_ctx",
                grant_univeral_access=True
            ))
            ctx_id = ctx_result[0] if isinstance(ctx_result, (list, tuple)) else ctx_result

            # Try to access iframe content
            js_iframe = """
            (() => {
                const iframe = document.querySelector('iframe[src*="pipopay"]');
                if (!iframe) return 'no iframe found';
                try {
                    const doc = iframe.contentDocument;
                    if (!doc) return 'contentDocument is null (cross-origin)';
                    const inputs = doc.querySelectorAll('input');
                    return JSON.stringify(Array.from(inputs).map(i => ({
                        type: i.type, name: i.name, id: i.id, ph: i.placeholder,
                        cls: (i.className||'').slice(0, 40)
                    })));
                } catch(e) { return 'error: ' + e.message; }
            })()
            """
            eval_result = await tab.send(cdp.runtime.evaluate(
                expression=js_iframe,
                context_id=ctx_id,
                return_by_value=True,
            ))
            remote_obj, exc = eval_result if isinstance(eval_result, (list, tuple)) else (eval_result, None)
            if exc:
                print(f"  [EXC] {exc}", flush=True)
            else:
                val = remote_obj.value if remote_obj and hasattr(remote_obj, 'value') else str(remote_obj)
                print(f"  Result: {str(val)[:300]}", flush=True)

    except Exception as e:
        print(f"\n[ERROR] {e}", flush=True)
        import traceback; traceback.print_exc()
    finally:
        print("\n[Done]", flush=True)
        await asyncio.sleep(10)
        try: browser.stop()
        except: pass

if __name__ == "__main__":
    asyncio.run(main())
