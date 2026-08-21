from pydantic import BaseModel


class Settings(BaseModel):
    core_api_url: str = ""
    core_api_key: str = ""
    two_captcha_api_key: str = ""
    sadcaptcha_api_key: str = ""
    profile_dir: str = r"C:\coin-automation\profiles"
    screenshot_dir: str = r"C:\coin-automation\screenshots"
    log_dir: str = r"C:\coin-automation\logs"
    state_dir: str = r"C:\coin-automation\state"
    max_concurrent_browsers: int = 3
    # Number of Chrome instances kept pre-launched (empty profile) for the
    # new-account QR login path, to skip Chrome's cold-start latency. 0 disables.
    warm_pool_size: int = 1
    # A pooled browser sitting idle this long (e.g. overnight, no new-account
    # orders) is discarded on next acquire rather than handed out — its CDP
    # connection can go stale well before Chrome itself would time out.
    warm_pool_max_age_minutes: float = 30
    qr_timeout_minutes: int = 5
    # hard ceiling for one whole fulfillment; <= 0 disables
    order_timeout_minutes: float = 15
    captcha_max_retries: int = 3
    spawn_ttl_minutes: int = 30
    es_uri: str = ""
    es_username: str = ""
    es_password: str = ""
    es_index_format: str = "coin-automation-logs"


settings: Settings | None = None


def set_settings(s: Settings):
    global settings
    settings = s


def get_settings() -> Settings:
    global settings
    if settings is None:
        raise RuntimeError("Settings not initialized. Call load_settings() at startup.")
    return settings
