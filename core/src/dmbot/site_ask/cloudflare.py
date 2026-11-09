"""The one call to Cloudflare Workers AI for the site's short answer (#3856): Llama 3.2 3B on the
free allowance, through the REST endpoint. No paid fallback: a refusal, a missing allowance or a
slow answer is an `AskFailed`, and the page says plainly it cannot answer right now.

Like the Voyage client, the call goes through one injectable `Transport`, so tests use a fake and
nothing here opens a connection of its own. The token is held by the real transport and by nothing
else, comes from the environment only and is never logged or put in an error: errors carry a kind
and a status, never a body (a body could echo the question).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

API_BASE = "https://api.cloudflare.com"
MODEL = "@cf/meta/llama-3.2-3b-instruct"
TIMEOUT_S = 10  # a slower answer is no answer: the page has its best match already
MAX_REPLY_BYTES = 200_000
# A live answer needs about 80 tokens; this is at least four times that (the cut-answer rule).
MAX_TOKENS = 512

Transport = Callable[[str, "Mapping[str, Any]"], Awaitable[tuple[int, bytes]]]


class AskFailed(Exception):
    """The call did not give an answer. `kind` is a fixed word, for the log: "auth", "refused",
    "busy", "network" or "reply"."""

    def __init__(self, kind: str, status: int = 0) -> None:
        super().__init__(f"{kind} ({status})" if status else kind)
        self.kind = kind
        self.status = status


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    tokens_in: int
    tokens_out: int


class HttpTransport:
    """The real call. The token is held here and nowhere else."""

    def __init__(self, token: str, base: str = API_BASE) -> None:
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self._base = base
        self._http: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None

    async def __call__(self, path: str, body: Mapping[str, Any]) -> tuple[int, bytes]:
        if self._http is None:
            self._http = aiohttp.ClientSession()
        try:
            async with self._http.post(
                self._base + path,
                data=json.dumps(body),
                headers=self._headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S, connect=5),
            ) as response:
                if response.status != 200:
                    return response.status, b""  # the body is never kept
                data = bytearray()
                async for chunk in response.content.iter_chunked(1 << 14):
                    data += chunk
                    if len(data) > MAX_REPLY_BYTES:
                        raise AskFailed("reply")
                return response.status, bytes(data)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise AskFailed("network") from exc


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


class Workers:
    """What the service needs from Workers AI: ask a system prompt and a question."""

    def __init__(self, transport: Transport, account_id: str) -> None:
        self._transport = transport
        self._path = f"/client/v4/accounts/{account_id}/ai/run/{MODEL}"

    async def ask(self, system: str, question: str) -> Reply:
        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": question},
            ],
            "max_tokens": MAX_TOKENS,
            "temperature": 0.1,
        }
        status, raw = await self._transport(self._path, body)
        if status in (401, 403):
            raise AskFailed("auth", status)
        if status == 429 or status >= 500:
            raise AskFailed("busy", status)
        if status != 200:
            raise AskFailed("refused", status)
        try:
            data = json.loads(raw)
            result = data["result"]
            text = result["response"]
        except (ValueError, KeyError, TypeError):
            raise AskFailed("reply", status) from None
        if data.get("success") is False or not isinstance(text, str) or not text.strip():
            raise AskFailed("reply", status)
        usage = result.get("usage") if isinstance(result, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        return Reply(
            text.strip(), _count(usage.get("prompt_tokens")), _count(usage.get("completion_tokens"))
        )
