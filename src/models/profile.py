from pydantic import BaseModel, Field


class SpawnProfileRequest(BaseModel):
    """Open a browser on an already-saved profile so it can be driven manually.

    Identify the profile either by `tiktok_username` (+ `user_name`, same
    convention the fulfillment flow uses) or by an explicit `profile_path`.
    """

    tiktok_username: str = ""
    user_name: str = ""
    profile_path: str = ""
    url: str = ""
    headless: bool = False
    ttl_minutes: int = Field(default=0, ge=0, description="0 = use SPAWN_TTL_MINUTES")
    create_if_missing: bool = False


class SpawnProfileResult(BaseModel):
    session_id: str
    profile_path: str
    logged_in: bool
    url: str
    devtools_url: str = ""
    websocket_url: str = ""
    started_at: str = ""
    expires_at: str = ""


class SpawnSessionList(BaseModel):
    sessions: list[SpawnProfileResult]
