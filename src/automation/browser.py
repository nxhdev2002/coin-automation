import asyncio
import os
import io
import random
import sys
import zipfile
import shutil
from pathlib import Path

import nodriver as uc
from loguru import logger


CHROMIUM_DIR = Path(os.environ.get("CHROMIUM_DIR", r"C:\coin-automation\chromium"))
CHROMIUM_EXE = CHROMIUM_DIR / "chrome-win" / "chrome.exe"


async def human_sleep(min_s: float = 1.0, max_s: float = 5.0):
    """Random sleep to mimic human behavior and avoid anti-bot detection."""
    delay = random.uniform(min_s, max_s)
    await asyncio.sleep(delay)


def _ensure_chromium() -> str:
    """Download Chromium if not present. Returns path to chrome.exe."""
    if CHROMIUM_EXE.exists():
        return str(CHROMIUM_EXE)

    logger.info(f"[CHROMIUM] Not found at {CHROMIUM_EXE}, downloading...")
    import requests

    resp = requests.get(
        "https://storage.googleapis.com/chromium-browser-snapshots/Win_x64/LAST_CHANGE",
        timeout=15,
    )
    resp.raise_for_status()
    revision = resp.text.strip()
    logger.info(f"[CHROMIUM] Latest revision: {revision}")

    zip_url = (
        f"https://storage.googleapis.com/chromium-browser-snapshots/"
        f"Win_x64/{revision}/chrome-win.zip"
    )
    logger.info(f"[CHROMIUM] Downloading from {zip_url}...")
    resp = requests.get(zip_url, timeout=300, stream=True)
    resp.raise_for_status()

    CHROMIUM_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = CHROMIUM_DIR / "chrome-win.zip"
    with open(zip_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    logger.info(f"[CHROMIUM] Extracting...")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(CHROMIUM_DIR)
    zip_path.unlink()

    if not CHROMIUM_EXE.exists():
        raise RuntimeError(f"[CHROMIUM] Extraction failed — {CHROMIUM_EXE} not found")

    logger.info(f"[CHROMIUM] Ready: {CHROMIUM_EXE}")
    return str(CHROMIUM_EXE)


async def launch_browser(profile_path: str, headless: bool = False, sadcaptcha_api_key: str = ""):
    os.makedirs(profile_path, exist_ok=True)
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        "--lang=en-US",
        "--accept-lang=en-US,en",
        "--blink-settings=imagesEnabled=false",
        "--disable-features=LazyImageLoading,MediaRouter,Translate",
        "--mute-audio",
        "--disable-background-networking",
        "--disable-sync",
        "--no-pings",
        "--disable-default-apps",
        "--disable-component-extensions-with-background-pages",
    ]
    logger.info(f"Launching browser: profile={profile_path} headless={headless} sadcaptcha={'yes' if sadcaptcha_api_key else 'no'}")
    print(f"  >> uc.start()...", flush=True)

    kwargs = {
        "browser_args": args,
        "user_data_dir": profile_path,
        "sandbox": False,
    }
    if headless:
        kwargs["headless"] = True

    if sadcaptcha_api_key:
        try:
            chromium_path = _ensure_chromium()
            kwargs["browser_executable_path"] = chromium_path
            from tiktok_captcha_solver.launcher import make_nodriver_solver
            browser = await make_nodriver_solver(sadcaptcha_api_key, **kwargs)
            logger.info("[SADCAPTCHA] Extension loaded — captchas will be auto-solved")
        except Exception as e:
            logger.warning(f"[SADCAPTCHA] Failed: {e} — falling back to normal browser")
            kwargs.pop("browser_executable_path", None)
            browser = await uc.start(**kwargs)
    else:
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


def parse_eval(raw):
    """Normalize CDP evaluate result into a Python value."""
    if raw is None:
        return None
    if isinstance(raw, list):
        out = {}
        for entry in raw:
            if isinstance(entry, list) and len(entry) == 2:
                key, val = entry
                if isinstance(val, dict) and "value" in val:
                    out[key] = val["value"]
                elif isinstance(val, dict) and val.get("type") == "null":
                    out[key] = None
                elif isinstance(val, dict) and val.get("type") == "undefined":
                    out[key] = None
                else:
                    out[key] = val
            else:
                return raw
        return out
    if isinstance(raw, dict) and "value" in raw:
        return raw["value"]
    return raw

