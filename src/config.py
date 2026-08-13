from pydantic import BaseModel


class Settings(BaseModel):
    core_api_url: str = ""
    core_api_key: str = ""
    two_captcha_api_key: str = ""
    profile_dir: str = r"C:\coin-automation\profiles"
    screenshot_dir: str = r"C:\coin-automation\screenshots"
    log_dir: str = r"C:\coin-automation\logs"
    max_concurrent_browsers: int = 3
    qr_timeout_minutes: int = 5
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
