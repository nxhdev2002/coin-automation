import asyncio
import nodriver as uc
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.automation.tiktok_login import qr_login, check_logged_in
from src.automation.tiktok_recharge import select_package, click_recharge, select_add_card
from src.automation.selectors import SELECTORS

PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "poc", "profiles", "testuser_cdp")
os.makedirs(PROFILE, exist_ok=True)


class FakeClient:
    async def update_order(self, oid, data):
        if "qrCodeBase64" in data:
            print(f"  [QR shown]", flush=True)
        elif "fulfillmentPhase" in data:
            print(f"  [Phase] {data['fulfillmentPhase']}", flush=True)
    async def close(self): pass


async def main():
    browser = await uc.start(
        browser_args=["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"],
        user_data_dir=PROFILE,
    )
    print("Browser started", flush=True)

    # Go straight to login page
    tab = await browser.get("https://www.tiktok.com/login")
    await asyncio.sleep(3)
    print("On login page", flush=True)

    # Check if already logged in (profile exists in profile dir)
    logged_in = await check_logged_in(tab)
    print(f"check_logged_in={logged_in}", flush=True)

    if not logged_in:
        logged_in = await qr_login(tab, FakeClient(), "cdp-test", 1)
        print(f"qr_login result: {logged_in}", flush=True)

    if not logged_in:
        print("Not logged in, exiting", flush=True)
        browser.stop()
        return

    print("Logged in! Navigating to /coin...", flush=True)
    tab = await browser.get("https://www.tiktok.com/coin")
    await asyncio.sleep(5)

    pkg = await select_package(tab, 30)
    print(f"package selected: {pkg}", flush=True)

    clicked = await click_recharge(tab)
    print(f"recharge clicked: {clicked}", flush=True)
    await asyncio.sleep(3)

    add_card = await select_add_card(tab)
    print(f"add card: {add_card}", flush=True)
    await asyncio.sleep(3)

    # NOW: list all CDP targets to find pipopay iframe
    print("\n=== CDP TARGETS ===", flush=True)
    targets = await browser.send(uc.cdp.target.get_targets())
    for t in targets:
        url = (t.url or "")[:120]
        print(f"  type={t.type_} url={url}", flush=True)

    # Find pipopay target
    pipopay = [t for t in targets if t.url and "pipopay" in t.url]
    if pipopay:
        pt = pipopay[0]
        print(f"\n=== POPOPAY FOUND ===", flush=True)

        # Attach to target
        session_id = await browser.send(uc.cdp.target.attach_to_target(
            target_id=pt.target_id,
            flatten=True,
        ))
        print(f"Attached! session_id={session_id}", flush=True)

        # Evaluate JS on the pipopay iframe
        result = await browser.send(uc.cdp.runtime.evaluate(
            expression="document.querySelectorAll('input').length",
            return_by_value=True,
        ), sessionId=str(session_id))
        print(f"Input count: {result}", flush=True)

        # List all inputs
        result = await browser.send(uc.cdp.runtime.evaluate(
            expression="""
            (() => {
                const inputs = document.querySelectorAll('input');
                return Array.from(inputs).map(i => ({
                    placeholder: i.placeholder,
                    type: i.type,
                    name: i.name,
                    id: i.id,
                }));
            })()
            """,
            return_by_value=True,
        ), sessionId=str(session_id))
        print(f"Inputs: {result}", flush=True)
    else:
        print("\n=== NO POPOPAY TARGET FOUND ===", flush=True)

    print("\nDone. Waiting 10s...", flush=True)
    await asyncio.sleep(10)
    browser.stop()


asyncio.run(main())
