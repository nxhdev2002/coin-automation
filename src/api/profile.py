import os

from fastapi import APIRouter, HTTPException
from loguru import logger

from ..automation.selectors import SELECTORS
from ..concurrency.drain_manager import get_drain_manager
from ..config import get_settings
from ..models.profile import SpawnProfileRequest, SpawnProfileResult, SpawnSessionList
from ..profile.paths import profile_name, profile_path
from ..profile.spawn import (
    ProfileBusyError, ProfileNotFoundError, SpawnSession, get_spawn_manager,
)

router = APIRouter(prefix="/profile", tags=["profile"])


def _to_result(session: SpawnSession) -> SpawnProfileResult:
    return SpawnProfileResult(
        session_id=session.session_id,
        profile_path=session.profile_path,
        logged_in=session.logged_in,
        url=session.url,
        devtools_url=session.devtools_url,
        websocket_url=session.websocket_url,
        started_at=session.started_at.isoformat(),
        expires_at=session.expires_at.isoformat(),
    )


def _resolve_target(request: SpawnProfileRequest, profile_dir: str) -> tuple[str, str]:
    """Return (profile_path, lock_key) for the requested profile."""
    if request.profile_path:
        path = os.path.normpath(request.profile_path)
        return path, os.path.basename(path)
    if not request.tiktok_username:
        raise HTTPException(
            status_code=400,
            detail="Provide either tiktok_username or profile_path",
        )
    name = profile_name(request.user_name, request.tiktok_username)
    return profile_path(profile_dir, name), name


@router.post("/spawn", response_model=SpawnProfileResult)
async def spawn_profile(request: SpawnProfileRequest):
    """Open a saved profile in a browser on this host and leave it running.

    The session holds the profile's lock (so a fulfillment can't launch a second
    Chrome on the same user-data-dir) until it's closed or its TTL expires.
    """
    if get_drain_manager().draining:
        raise HTTPException(
            status_code=503,
            detail="Service is draining for a deploy, retry shortly",
        )

    settings = get_settings()
    path, lock_key = _resolve_target(request, settings.profile_dir)
    url = request.url or SELECTORS["recharge_url"]
    ttl_minutes = request.ttl_minutes or settings.spawn_ttl_minutes

    try:
        session = await get_spawn_manager().spawn(
            profile_path=path,
            lock_key=lock_key,
            url=url,
            headless=request.headless,
            ttl_minutes=ttl_minutes,
            create_if_missing=request.create_if_missing,
            session_cookies_json=request.session_cookies_json,
            tiktok_profile_id=request.tiktok_profile_id,
        )
    except ProfileNotFoundError:
        raise HTTPException(status_code=404, detail=f"No saved profile at {path}")
    except ProfileBusyError:
        raise HTTPException(
            status_code=409,
            detail=f"Profile {lock_key} is busy (fulfillment or another spawn session in progress)",
        )
    except Exception as e:
        logger.error(f"Spawn failed for profile {path}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to launch browser: {e}")

    return _to_result(session)


@router.get("/sessions", response_model=SpawnSessionList)
async def list_sessions():
    return SpawnSessionList(sessions=[_to_result(s) for s in get_spawn_manager().sessions])


@router.delete("/sessions/{session_id}")
async def close_session(session_id: str):
    closed = await get_spawn_manager().close(session_id)
    if not closed:
        raise HTTPException(status_code=404, detail=f"No spawn session {session_id}")
    return {"closed": True, "session_id": session_id}
