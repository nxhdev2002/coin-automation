import asyncio
import ipaddress
import json
import os
import socket
from datetime import datetime, timezone

import httpx
from loguru import logger

from ..config import get_settings
from ..models.ip import IpState
from ..notify.telegram import TelegramNotifier

# IPv4-only endpoints on purpose: a provider that can answer with IPv6 would make
# the address flap between families and fire bogus "IP changed" alerts.
DEFAULT_IP_PROVIDERS = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)

FAILURE_ALERT_THRESHOLD = 3


async def fetch_public_ip(
    providers: tuple[str, ...] = DEFAULT_IP_PROVIDERS,
    timeout: float = 10.0,
) -> tuple[str, str]:
    """Return (ip, provider_url), or ("", "") if no provider gave a usable IPv4.

    Anything that doesn't parse as an IPv4 address (an error page, an IPv6
    answer, a truncated body) is discarded rather than treated as a new IP —
    this is the main guard against false change alerts.
    """
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for url in providers:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                candidate = (resp.text or "").strip()
                ipaddress.IPv4Address(candidate)
                return candidate, url
            except Exception as e:
                logger.warning(f"Public IP lookup via {url} failed: {type(e).__name__}: {e}")
    return "", ""


class IpWatcher:
    """Polls the VPS public IP and alerts on Telegram when it changes.

    The last known IP is persisted, so a restart or deploy doesn't look like a
    change and re-alert.
    """

    def __init__(
        self,
        state_path: str,
        notifier: TelegramNotifier,
        interval_minutes: int,
        providers: tuple[str, ...] = DEFAULT_IP_PROVIDERS,
        failure_threshold: int = FAILURE_ALERT_THRESHOLD,
    ):
        self._state_path = state_path
        self._notifier = notifier
        self._interval_minutes = interval_minutes
        self._providers = providers
        self._failure_threshold = failure_threshold

        self._public_ip = ""
        self._previous_ip = ""
        self._source = ""
        self._checked_at: datetime | None = None
        self._changed_at: datetime | None = None
        self._failures = 0
        self._failure_alerted = False
        self._has_state = False
        self._loaded = False

        self._hostname = socket.gethostname()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._interval_minutes > 0

    @property
    def has_checked(self) -> bool:
        return self._checked_at is not None

    def state(self) -> IpState:
        return IpState(
            public_ip=self._public_ip,
            previous_ip=self._previous_ip,
            source=self._source,
            checked_at=self._checked_at.isoformat() if self._checked_at else "",
            changed_at=self._changed_at.isoformat() if self._changed_at else "",
            consecutive_failures=self._failures,
            hostname=self._hostname,
            monitor_enabled=self.enabled,
            interval_minutes=self._interval_minutes,
        )

    async def check(self) -> IpState:
        async with self._lock:
            self._load_state()

            ip, source = await fetch_public_ip(self._providers)
            now = datetime.now(timezone.utc)
            self._checked_at = now

            if not ip:
                return await self._handle_failure()

            if self._failures:
                logger.info(f"Public IP lookup recovered after {self._failures} failure(s)")
            self._failures = 0
            self._failure_alerted = False
            self._source = source

            if not self._has_state:
                self._public_ip = ip
                self._changed_at = now
                self._has_state = True
                self._save_state()
                logger.info(f"IP monitor baseline set: {ip} (via {source})")
                await self._notifier.send(
                    f"coin-automation: IP monitor started\n"
                    f"Host: {self._hostname}\n"
                    f"Current IP: {ip}"
                )
            elif ip != self._public_ip:
                self._previous_ip = self._public_ip
                self._public_ip = ip
                self._changed_at = now
                self._save_state()
                logger.warning(f"VPS public IP changed: {self._previous_ip} -> {ip} (via {source})")
                await self._notifier.send(
                    f"🚨 coin-automation: VPS public IP changed\n"
                    f"Old: {self._previous_ip}\n"
                    f"New: {ip}\n"
                    f"Host: {self._hostname}\n"
                    f"At:  {_local_stamp(now)}"
                )
            else:
                logger.debug(f"VPS public IP unchanged: {ip}")

            return self.state()

    async def _handle_failure(self) -> IpState:
        self._failures += 1
        logger.warning(f"Public IP lookup failed ({self._failures} in a row)")
        if self._failures >= self._failure_threshold and not self._failure_alerted:
            # One warning per outage, not one per poll.
            self._failure_alerted = True
            await self._notifier.send(
                f"⚠️ coin-automation: cannot determine VPS public IP\n"
                f"Host: {self._hostname}\n"
                f"Failed checks: {self._failures}\n"
                f"Last known IP: {self._public_ip or 'unknown'}"
            )
        return self.state()

    def start(self) -> None:
        if not self.enabled:
            logger.info("IP monitor disabled (interval <= 0), endpoints still available")
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_forever())
        logger.info(f"IP monitor started, checking every {self._interval_minutes} minute(s)")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info("IP monitor stopped")

    async def _run_forever(self) -> None:
        delay = self._interval_minutes * 60
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # A bad check must never kill the loop.
                logger.error(f"IP monitor check errored: {type(e).__name__}: {e}")
            await asyncio.sleep(delay)

    def _load_state(self) -> None:
        """Re-read the state file before every check — it is the source of truth.

        Reading it each time (it's a few bytes) means a state written by a
        previous process, or edited by hand on the VPS, is picked up instead of
        being masked by whatever this process happens to hold in memory.
        """
        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:
            logger.warning(f"Could not read IP state {self._state_path}: {e}, starting fresh")
            return

        public_ip = data.get("public_ip", "") or ""
        if not public_ip:
            return

        if not self._loaded or public_ip != self._public_ip:
            logger.info(f"IP monitor state loaded: last known IP {public_ip}")

        self._public_ip = public_ip
        self._previous_ip = data.get("previous_ip", "") or ""
        changed_at = data.get("changed_at", "")
        if changed_at:
            try:
                self._changed_at = datetime.fromisoformat(changed_at)
            except ValueError:
                pass
        self._has_state = True
        self._loaded = True

    def _save_state(self) -> None:
        data = {
            "public_ip": self._public_ip,
            "previous_ip": self._previous_ip,
            "changed_at": self._changed_at.isoformat() if self._changed_at else "",
        }
        tmp_path = f"{self._state_path}.tmp"
        try:
            os.makedirs(os.path.dirname(self._state_path) or ".", exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            # Atomic swap: a kill mid-write (deploy does taskkill /T) can't leave
            # a half-written state file behind.
            os.replace(tmp_path, self._state_path)
        except Exception as e:
            logger.error(f"Could not persist IP state to {self._state_path}: {e}")


def _local_stamp(moment: datetime) -> str:
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


_ip_watcher: IpWatcher | None = None


def get_ip_watcher() -> IpWatcher:
    global _ip_watcher
    if _ip_watcher is None:
        settings = get_settings()
        _ip_watcher = IpWatcher(
            state_path=os.path.join(settings.state_dir, "ip_state.json"),
            notifier=TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id),
            interval_minutes=settings.ip_check_interval_minutes,
        )
    return _ip_watcher
