import json
import shutil

from loguru import logger

from ..automation.browser import launch_browser, close_browser, export_cookies, inject_cookies
from .paths import profile_path


async def launch_from_cookies_or_profile(settings, correlation_id: str, profile_path_value: str, session_cookies_json: str, **launch_kwargs):
    """Launch a browser for an existing account: from stored session cookies into a
    throwaway ephemeral profile if available, else the legacy persistent profile dir
    (until this account's cookies have been migrated). Returns (browser, profile, is_ephemeral)."""
    if session_cookies_json:
        profile = profile_path(settings.profile_dir, f"_ephemeral_{correlation_id}")
        browser = await launch_browser(profile, sadcaptcha_api_key=settings.sadcaptcha_api_key, **launch_kwargs)
        try:
            await inject_cookies(browser, json.loads(session_cookies_json))
        except Exception as e:
            logger.warning(f"Cookie injection failed for {correlation_id}, proceeding without: {e}")
        return browser, profile, True

    profile = profile_path_value or profile_path(settings.profile_dir, correlation_id)
    browser = await launch_browser(profile, sadcaptcha_api_key=settings.sadcaptcha_api_key, **launch_kwargs)
    return browser, profile, False


async def teardown_session_browser(browser, profile: str, tiktok_profile_id: str, core_client, is_ephemeral: bool, refresh_cookies: bool) -> None:
    """Refresh stored cookies before closing when the session was confirmed valid this run —
    this is also how an account still on its legacy persistent profile dir opportunistically
    migrates to cookie storage the next time it's used (no separate migration script needed).
    Then close the browser, and for a throwaway ephemeral profile, delete its directory."""
    if refresh_cookies and tiktok_profile_id:
        try:
            fresh_cookies = await export_cookies(browser)
            await core_client.update_tiktok_profile(tiktok_profile_id, {"sessionCookiesJson": json.dumps(fresh_cookies)})
        except Exception as e:
            logger.warning(f"Could not refresh stored cookies for profile {tiktok_profile_id}: {e}")
    try:
        await close_browser(browser)
    except Exception:
        pass
    if is_ephemeral:
        shutil.rmtree(profile, ignore_errors=True)
