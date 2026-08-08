import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from loguru import logger

from .config import settings, Settings
from .logging_setup import setup_logging
from .api.fulfill import router as fulfill_router
from .api.health import router as health_router


async def load_settings() -> Settings:
    infisical_base = os.getenv("INFISICAL_BASE_URL", "https://app.infisical.com")
    infisical_client_id = os.getenv("INFISICAL_CLIENT_ID", "")
    infisical_client_secret = os.getenv("INFISICAL_CLIENT_SECRET", "")
    infisical_project_id = os.getenv("INFISICAL_PROJECT_ID", "")
    infisical_env = os.getenv("INFISICAL_ENVIRONMENT", "prod")
    infisical_path = os.getenv("INFISICAL_SECRET_PATH", "/coin-automation")

    if infisical_client_id and infisical_client_secret:
        try:
            import httpx

            async with httpx.AsyncClient() as client:
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
                for item in secrets_resp.json().get("secrets", []):
                    key = item["key"].replace("__", ".")
                    secrets[key] = item["value"]

                return Settings(
                    core_api_url=secrets.get("CORE_API_URL", "http://localhost:443"),
                    core_api_key=secrets.get("CORE_API_KEY", ""),
                    two_captcha_api_key=secrets.get("TWO_CAPTCHA_API_KEY", ""),
                    profile_dir=secrets.get("PROFILE_DIR", r"C:\coin-automation\profiles"),
                    screenshot_dir=secrets.get("SCREENSHOT_DIR", r"C:\coin-automation\screenshots"),
                    log_dir=secrets.get("LOG_DIR", r"C:\coin-automation\logs"),
                    max_concurrent_browsers=int(secrets.get("MAX_CONCURRENT_BROWSERS", "3")),
                    qr_timeout_minutes=int(secrets.get("QR_TIMEOUT_MINUTES", "5")),
                    captcha_max_retries=int(secrets.get("CAPTCHA_MAX_RETRIES", "3")),
                    es_uri=secrets.get("ELASTICSEARCH.URI", ""),
                    es_username=secrets.get("ELASTICSEARCH.USERNAME", ""),
                    es_password=secrets.get("ELASTICSEARCH.PASSWORD", ""),
                )
        except Exception as e:
            logger.warning(f"Infisical fetch failed: {e}, using env/defaults")

    return Settings(
        core_api_url=os.getenv("CORE_API_URL", "http://localhost:443"),
        core_api_key=os.getenv("CORE_API_KEY", ""),
        two_captcha_api_key=os.getenv("TWO_CAPTCHA_API_KEY", ""),
        profile_dir=os.getenv("PROFILE_DIR", r"C:\coin-automation\profiles"),
        screenshot_dir=os.getenv("SCREENSHOT_DIR", r"C:\coin-automation\screenshots"),
        log_dir=os.getenv("LOG_DIR", r"C:\coin-automation\logs"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global settings
    settings = await load_settings()

    os.makedirs(settings.profile_dir, exist_ok=True)
    os.makedirs(settings.screenshot_dir, exist_ok=True)
    os.makedirs(settings.log_dir, exist_ok=True)

    setup_logging(
        settings.log_dir,
        settings.es_uri,
        settings.es_username,
        settings.es_password,
        settings.es_index_format,
    )

    logger.info(f"Coin Automation Service started — API: {settings.core_api_url}")

    yield

    logger.info("Coin Automation Service shutting down")


app = FastAPI(title="Coin Automation Service", lifespan=lifespan)
app.include_router(fulfill_router)
app.include_router(health_router)
