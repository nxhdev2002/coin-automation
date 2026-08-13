import os


def profile_name(user_name: str, tiktok_username: str) -> str:
    """Directory name for a profile — also used as the lock key.

    Kept in one place so the fulfillment flow and the spawn API can never
    disagree about which user-data-dir belongs to an account (a mismatch would
    let both launch Chrome on the same profile, or on two different ones).
    """
    return f"{user_name}-{tiktok_username}" if user_name else tiktok_username


def profile_path(profile_dir: str, name: str) -> str:
    return os.path.join(profile_dir, name)
