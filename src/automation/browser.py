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
from nodriver.core.util import ProxyForwarder


CHROMIUM_DIR = Path(os.environ.get("CHROMIUM_DIR", r"C:\coin-automation\chromium"))
CHROMIUM_EXE = CHROMIUM_DIR / "chrome-win" / "chrome.exe"


async def human_sleep(min_s: float = 1.0, max_s: float = 5.0):
    """Random sleep to mimic human behavior and avoid anti-bot detection."""
    delay = random.uniform(min_s, max_s)
    await asyncio.sleep(delay)


async def get_with_retry(browser, url: str, retries: int = 1):
    """`browser.get(url)`, retrying once on a transient CDP handshake timeout.

    nodriver opens a fresh CDP websocket per tab the first time it's used; under
    concurrent browser load (multiple Chrome instances launching/navigating at
    once) that handshake can occasionally miss its timeout window even though
    the browser process itself is healthy (`TimeoutError: timed out during
    opening handshake`, seen in production). A bare retry clears it without
    failing the whole order — only that specific error is retried, anything
    else propagates immediately.
    """
    for attempt in range(retries + 1):
        try:
            return await browser.get(url)
        except TimeoutError as e:
            if attempt >= retries or "opening handshake" not in str(e):
                raise
            logger.warning(f"CDP handshake timeout navigating to {url}, retrying ({attempt + 1}/{retries})")
            await asyncio.sleep(1)


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


async def launch_browser(profile_path: str, headless: bool = False, sadcaptcha_api_key: str = "", disable_images: bool = False, proxy_url: str = ""):
    os.makedirs(profile_path, exist_ok=True)
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        "--lang=en-US",
        "--accept-lang=en-US,en",
        "--disable-features=MediaRouter,Translate",
        "--mute-audio",
        "--disable-background-networking",
        "--disable-sync",
        "--no-pings",
        "--disable-default-apps",
        "--disable-component-extensions-with-background-pages",
    ]
    if disable_images:
        args.extend([
            "--blink-settings=imagesEnabled=false",
            "--disable-features=LazyImageLoading,MediaRouter,Translate",
        ])

    proxy_forwarder = None
    if proxy_url:
        # ProxyForwarder transparently handles authenticated proxies (user:pass@host:port)
        # by spinning up a local unauthenticated forwarder Chrome can point --proxy-server
        # at directly — Chrome itself can't carry inline credentials on that flag.
        proxy_forwarder = ProxyForwarder(proxy_server=proxy_url)
        args.append(f"--proxy-server={proxy_forwarder.proxy_server}")

    logger.info(f"Launching browser: profile={profile_path} headless={headless} sadcaptcha={'yes' if sadcaptcha_api_key else 'no'} images={'off' if disable_images else 'on'} proxy={'yes' if proxy_url else 'no'}")
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

    if proxy_forwarder is not None:
        browser._proxy_forwarder = proxy_forwarder

    print(f"  >> browser started!", flush=True)
    return browser


async def close_browser(browser, grace_seconds: float = 3.0) -> None:
    """Shut Chrome down gracefully instead of killing the process outright.

    `Browser.stop()` (nodriver) calls `self._process.terminate()`/`.kill()`
    almost immediately — that's a hard kill, not a real shutdown, so Chrome
    never gets to flush the profile's cookie/session DB to disk. A TikTok
    login session established moments earlier can come back logged-out the
    next time this same profile is launched, because the write never landed.

    Ask Chrome to close itself first (`Browser.close` — flushes profile data
    the same way quitting normally would), give it a moment to actually exit,
    and only fall back to the hard kill in `browser.stop()` if it doesn't.
    """
    try:
        await browser.send(uc.cdp.browser.close())
    except Exception:
        pass

    process = getattr(browser, "_process", None)
    if process is not None:
        deadline = asyncio.get_event_loop().time() + grace_seconds
        while asyncio.get_event_loop().time() < deadline:
            if process.poll() is not None:
                break
            await asyncio.sleep(0.1)

    try:
        browser.stop()
    except Exception:
        pass

    proxy_forwarder = getattr(browser, "_proxy_forwarder", None)
    if proxy_forwarder is not None and proxy_forwarder.server is not None:
        try:
            proxy_forwarder.server.close()
            await proxy_forwarder.server.wait_closed()
        except Exception:
            pass


async def wait_for_element(tab, selector: str, timeout: int = 30, poll_interval: float = 0.5) -> bool:
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

