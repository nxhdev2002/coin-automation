import os
import shutil

from loguru import logger


def profile_name(user_name: str, tiktok_username: str) -> str:
    """Directory name for a profile — also used as the lock key.

    Kept in one place so the fulfillment flow and the spawn API can never
    disagree about which user-data-dir belongs to an account (a mismatch would
    let both launch Chrome on the same profile, or on two different ones).
    """
    return f"{user_name}-{tiktok_username}" if user_name else tiktok_username


def profile_path(profile_dir: str, name: str) -> str:
    return os.path.join(profile_dir, name)


def graduate_profile(old_path: str, profile_dir: str, order_id: str) -> str:
    """Move a warm-pool profile out of `_warm_pool` into its permanent,
    order_id-named location once it's linked to a real account.

    Without this, every new account added via a warm-pool hit stays nested
    under `_warm_pool/<uuid>` forever — indistinguishable on disk from a
    pool instance that never got claimed, and skipped entirely by the cache
    cleanup sweep (which never touches `_warm_pool` since it may hold a
    browser still live in the pool queue).

    The caller must close the browser first — Chrome still holding the
    profile open would make the move fail on Windows. If the move fails
    anyway (e.g. a lingering file handle, antivirus scan), the original path
    is returned unchanged so a hiccup here never breaks account creation.
    """
    new_path = profile_path(profile_dir, order_id)
    try:
        shutil.move(old_path, new_path)
        logger.info(f"Graduated warm-pool profile {old_path} -> {new_path}")
        return new_path
    except OSError as e:
        logger.warning(f"Could not graduate warm-pool profile {old_path} -> {new_path}: {e}, keeping original path")
        return old_path
