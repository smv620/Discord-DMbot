"""DMbot's AI text calls (Anthropic's Messages API). The first use is turning a document
into a names list (`dmbot.memory.name_documents`). The key comes only from the server's
settings, is never logged and never shown (CLAUDE.md, API keys). Bring-your-own keys
per server come later (#50).
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
REQUEST_TIMEOUT_S = 180
CONNECT_TIMEOUT_S = 15


class AIError(Exception):
    """In plain words for the DM; the cause is in the log."""


class AnthropicClient:
    def __init__(
        self, api_key: str, model: str = DEFAULT_MODEL, session: aiohttp.ClientSession | None = None
    ) -> None:
        self._key = api_key
        self.model = model
        self._session = session

    def __repr__(self) -> str:  # never the key
        return f"AnthropicClient(model={self.model!r})"

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def complete(self, system: str, text: str, *, max_tokens: int = 8000) -> str:
        """One request: `system` instructions, `text` as the user's message. Returns the
        answer's text. Raises AIError in plain words."""
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": text}],
        }
        headers = {
            "x-api-key": self._key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        try:
            async with self._get_session().post(API_URL, json=body, headers=headers) as resp:
                if resp.status in (401, 403):
                    log.error("The AI service refused the key (HTTP %s)", resp.status)
                    raise AIError(
                        "The AI service didn't accept DMbot's key. Ask whoever runs DMbot to "
                        "check ANTHROPIC_API_KEY."
                    )
                if resp.status in (429, 529) or resp.status >= 500:
                    log.warning("The AI service is busy (HTTP %s)", resp.status)
                    raise AIError("The AI service is busy right now. Try again in a minute.")
                if resp.status != 200:
                    log.error("AI request failed (HTTP %s): %s", resp.status, await resp.text())
                    raise AIError("The AI service couldn't do that. Try again later.")
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.warning("AI request failed: %s", type(exc).__name__)
            raise AIError("DMbot couldn't reach the AI service. Try again in a minute.") from exc
        usage = data.get("usage", {})
        log.info(
            "AI request done: model=%s in=%s out=%s stop=%s",
            self.model,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            data.get("stop_reason"),
        )
        blocks = data.get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
