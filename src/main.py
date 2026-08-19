import os
import sys
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from .config import Settings, set_settings, get_settings
from .logging_setup import setup_logging
from .api.fulfill import router as fulfill_router
from .api.health import router as health_router
from .api.profile import router as profile_router
from .profile.spawn import get_spawn_manager
from .automation.browser_pool import get_warm_pool


async def load_settings() -> Settings:
    infisical_base = os.getenv("INFISICAL_BASE_URL", "https://app.infisical.com")
    infisical_client_id = os.getenv("INFISICAL_CLIENT_ID", "")
    infisical_client_secret = os.getenv("INFISICAL_CLIENT_SECRET", "")
    infisical_project_id = os.getenv("INFISICAL_PROJECT_ID", "")
    infisical_env = os.getenv("INFISICAL_ENVIRONMENT", "dev")
    infisical_path = os.getenv("INFISICAL_SECRET_PATH", "/coin-automation")

    if infisical_client_id and infisical_client_secret:
        try:
            import httpx

            logger.info("Fetching secrets from Infisical...")

            async with httpx.AsyncClient(timeout=10.0) as client:
                login_resp = await client.post(
                    f"{infisical_base}/api/v1/auth/universal-auth/login",
                    json={"clientId": infisical_client_id, "clientSecret": infisical_client_secret},
                )
                login_resp.raise_for_status()
                access_token = login_resp.json()["accessToken"]

                secrets_resp = await client.get(
                    f"{infisical_base}/api/v3/secrets/raw",
                    params={
                        "workspaceId": infisical_project_id,
                        "environment": infisical_env,
                        "secretPath": infisical_path,
                    },
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                secrets_resp.raise_for_status()

                secrets = {}
                raw_secrets = secrets_resp.json().get("secrets", [])
                logger.debug(f"Infisical raw response keys: {list(secrets_resp.json().keys())}")
                for item in raw_secrets:
                    k = item.get("secretKey") or item.get("key") or ""
                    v = item.get("secretValue") or item.get("value") or ""
                    if k:
                        secrets[k.replace("__", ".")] = v

                logger.info(f"Loaded {len(secrets)} secrets from Infisical:")
                for key, val in secrets.items():
                    masked = val[:2] + "***" if val and len(val) > 2 else ("***" if val else "(empty)")
                    logger.info(f"  {key} = {val}")

                return Settings(
                    core_api_url=secrets.get("CORE_API_URL", "https://api.vccus.net"),
                    core_api_key=secrets.get("CORE_API_KEY", "hoangdz2033"),
                    two_captcha_api_key=secrets.get("TWO_CAPTCHA_API_KEY", "e11ba3d2efe908bc276aedfed0c303bd"),
                    sadcaptcha_api_key=secrets.get("SADCAPTCHA_API_KEY", ""),
                    profile_dir=secrets.get("PROFILE_DIR", r"C:\coin-automation\profiles"),
                    screenshot_dir=secrets.get("SCREENSHOT_DIR", r"C:\coin-automation\screenshots"),
                    log_dir=secrets.get("LOG_DIR", r"C:\coin-automation\logs"),
                    state_dir=secrets.get("STATE_DIR", r"C:\coin-automation\state"),
                    max_concurrent_browsers=int(secrets.get("MAX_CONCURRENT_BROWSERS", "3")),
                    warm_pool_size=int(secrets.get("WARM_POOL_SIZE", "1")),
                    qr_timeout_minutes=int(secrets.get("QR_TIMEOUT_MINUTES", "5")),
                    order_timeout_minutes=float(secrets.get("ORDER_TIMEOUT_MINUTES", "15")),
                    captcha_max_retries=int(secrets.get("CAPTCHA_MAX_RETRIES", "3")),
                    spawn_ttl_minutes=int(secrets.get("SPAWN_TTL_MINUTES", "30")),
                    es_uri=secrets.get("ELASTICSEARCH.URI", ""),
                    es_username=secrets.get("ELASTICSEARCH.USERNAME", ""),
                    es_password=secrets.get("ELASTICSEARCH.PASSWORD", ""),
                )
        except Exception as e:
            logger.warning(f"Infisical fetch failed: {type(e).__name__}: {e}, using env/defaults")
    else:
        logger.info("Infisical credentials not set, loading from env/defaults")

    return Settings(
        core_api_url=os.getenv("CORE_API_URL", "https://localhost:44396"),
        core_api_key=os.getenv("CORE_API_KEY", "hoangdz2033@@"),
        two_captcha_api_key=os.getenv("TWO_CAPTCHA_API_KEY", "e11ba3d2efe908bc276aedfed0c303bd"),
        sadcaptcha_api_key=os.getenv("SADCAPTCHA_API_KEY", ""),
        profile_dir=os.getenv("PROFILE_DIR", r"C:\coin-automation\profiles"),
        screenshot_dir=os.getenv("SCREENSHOT_DIR", r"C:\coin-automation\screenshots"),
        log_dir=os.getenv("LOG_DIR", r"C:\coin-automation\logs"),
        state_dir=os.getenv("STATE_DIR", r"C:\coin-automation\state"),
        spawn_ttl_minutes=int(os.getenv("SPAWN_TTL_MINUTES", "30")),
        order_timeout_minutes=float(os.getenv("ORDER_TIMEOUT_MINUTES", "15")),
        warm_pool_size=int(os.getenv("WARM_POOL_SIZE", "1")),
        es_uri=os.getenv("ES_URI", ""),
        es_username=os.getenv("ES_USERNAME", ""),
        es_password=os.getenv("ES_PASSWORD", ""),
        es_index_format=os.getenv("ES_INDEX_FORMAT", "coin-automation-logs"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = await load_settings()
    set_settings(s)
    settings = get_settings()

    os.makedirs(settings.profile_dir, exist_ok=True)
    os.makedirs(settings.screenshot_dir, exist_ok=True)
    os.makedirs(settings.log_dir, exist_ok=True)
    os.makedirs(settings.state_dir, exist_ok=True)

    setup_logging(
        settings.log_dir,
        settings.es_uri,
        settings.es_username,
        settings.es_password,
        settings.es_index_format,
    )

    if settings.warm_pool_size > 0:
        get_warm_pool(settings)
        logger.info(f"Warm browser pool started (size={settings.warm_pool_size})")

    logger.info(f"Coin Automation Service started — API: {settings.core_api_url}")

    yield

    if settings.warm_pool_size > 0:
        warm_closed = await get_warm_pool(settings).close_all()
        if warm_closed:
            logger.info(f"Closed {warm_closed} warm-pool browser(s) on shutdown")

    closed = await get_spawn_manager().close_all()
    if closed:
        logger.info(f"Closed {closed} spawned profile browser(s) on shutdown")

    logger.info("Coin Automation Service shutting down")


app = FastAPI(title="Coin Automation Service", lifespan=lifespan)


@app.get("/")
async def root():
    return {"status": "ok"}


app.include_router(fulfill_router)
app.include_router(health_router)
app.include_router(profile_router)
