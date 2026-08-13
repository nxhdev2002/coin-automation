import httpx
from loguru import logger

TELEGRAM_API = "https://api.telegram.org"


class TelegramNotifier:
    """Minimal Telegram Bot API client for operational alerts.

    Deliberately independent of the core API: an alert about the VPS losing or
    changing its public IP is exactly the situation where the core may be
    unreachable (broken IP whitelist), so it must not depend on it.
    """

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 10.0):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    async def send(self, text: str) -> bool:
        if not self.enabled:
            logger.warning("Telegram alert skipped: bot token / chat id not configured")
            return False

        # Plain text on purpose — no parse_mode, so hostnames and IPs can't break
        # the message with stray Markdown characters.
        payload = {"chat_id": self._chat_id, "text": text}
        url = f"{TELEGRAM_API}/bot{self._bot_token}/sendMessage"

        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                return True
            except Exception as e:
                # Never log the URL — it carries the bot token.
                logger.error(f"Telegram send failed (attempt {attempt}): {type(e).__name__}: {e}")

        return False
