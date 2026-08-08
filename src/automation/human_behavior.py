import asyncio
import time
import random

import nodriver as uc


async def human_delay(min_ms: float = 300, max_ms: float = 1500) -> None:
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def human_type(tab, selector: str, text: str, min_ms: float = 30, max_ms: float = 120) -> None:
    el = await tab.find(selector, timeout=5)
    if not el:
        return
    await el.click()
    for char in text:
        await el.send_keys(char)
        await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def js_click(tab, selector: str) -> bool:
    js = f"""
    (() => {{
        const el = document.querySelector('{selector}');
        if (el) {{ el.click(); return true; }}
        return false;
    }})()
    """
    result = await tab.evaluate(js)
    return bool(result)
