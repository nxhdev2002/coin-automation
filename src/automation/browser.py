import asyncio
import os
import sys
import nodriver as uc
from loguru import logger


async def launch_browser(profile_path: str, headless: bool = False):
    os.makedirs(profile_path, exist_ok=True)
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
    ]
    logger.info(f"Launching browser: profile={profile_path} headless={headless}")
    print(f"  >> uc.start()...", flush=True)

    kwargs = {
        "browser_args": args,
        "user_data_dir": profile_path,
    }
    if headless:
        kwargs["headless"] = True

    browser = await uc.start(**kwargs)
    print(f"  >> browser started!", flush=True)
    return browser


async def wait_for_element(tab, selector: str, timeout: int = 10, poll_interval: float = 0.5) -> bool:
    """Poll via JS for an element to appear. Returns True if found, False on timeout."""
    js = f"""
    (() => {{
        const el = document.querySelector(`{selector}`);
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    }})()
    """
    elapsed = 0.0
    while elapsed < timeout:
        try:
            result = await tab.evaluate(js)
            if result:
                return True
        except Exception:
            pass
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return False


async def wait_for_element_fn(tab, js_fn: str, timeout: int = 10, poll_interval: float = 0.5) -> bool:
    """Poll via JS function that returns truthy. Returns True if truthy, False on timeout."""
    elapsed = 0.0
    while elapsed < timeout:
        try:
            result = await tab.evaluate(js_fn)
            if result:
                return True
        except Exception:
            pass
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return False


async def click_element_js(tab, selector: str) -> bool:
    """Click element via JS. More reliable than tab.find().click()."""
    js = f"""
    (() => {{
        const el = document.querySelector(`{selector}`);
        if (!el) return false;
        el.scrollIntoView({{block: 'center'}});
        el.click();
        return true;
    }})()
    """
    try:
        result = await tab.evaluate(js)
        return bool(result)
    except Exception:
        return False

