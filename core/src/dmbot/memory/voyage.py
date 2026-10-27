"""Voyage AI's two calls for meaning search (#2664; docs/STORY_MEMORY.md 9a, "Embeddings"): turn
text into numbers (`voyage-3.5`) and put the best matches first (`rerank-2.5`).

Everything goes through one injectable `Transport` (like the ElevenLabs client), so tests use a
fake and nothing here opens a connection of its own. The key is held in the real transport, comes
from the environment only, and is never logged or put in an error message. Errors carry a kind and
a status, never a body: a body could echo the text that was sent. What is sent is the campaign's
own text, never sound; Voyage is named on the privacy page and does not train on it.

The request shapes are the ones Voyage documents; they live in this module so a change on their side
is a change here. dev1's live run on the server is the proof they still hold.
"""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp

API_BASE = "https://api.voyageai.com"
BATCH = 64  # texts per embeddings call: well inside the service's limits
MAX_CHARS = 8000  # a text longer than this is cut before it is sent; only its start is read
MAX_REPLY_BYTES = 8_000_000  # a reply bigger than this is refused (64 vectors are about 1 MB)
TIMEOUT_S = 60
MAX_RERANK_DOCUMENTS = 1000  # the service's limit for one rerank call

# (method, path, json body) -> (status, body bytes)
Transport = Callable[[str, str, "Mapping[str, Any] | None"], Awaitable[tuple[int, bytes]]]

# What to check for each kind, in plain words; used by the test tool (a later slice of #2664).
CHECKS: dict[str, str] = {
    "auth": "check that VOYAGE_API_KEY is right and has not expired.",
    "plan": "the account is out of credit or over its limit.",
    "refused": "the request was turned down.",
    "busy": "the service was busy or down; try again in a few minutes.",
    "network": "the connection failed or timed out; check the server's network.",
    "reply": "the answer was not numbers DMbot could read; try again, and say so if it repeats.",
}


class ProviderError(Exception):
    """A call failed. `kind` says how, in a fixed word; never the reply's body."""

    def __init__(self, kind: str, status: int = 0) -> None:
        super().__init__(f"{kind} ({status})" if status else kind)
        self.kind = kind
        self.status = status

    def describe(self) -> str:
        """`auth, HTTP 401`: fixed words and a number only, for the log and the test tool."""
        return f"{self.kind}, HTTP {self.status}" if self.status else f"{self.kind}, no HTTP reply"

    @property
    def permanent(self) -> bool:
        """Whether trying again would fail the same way (a bad key, a refused request)."""
        return self.kind in ("auth", "refused", "plan")


def _number(x: object) -> bool:
    """A real, finite number (not a bool, not NaN or infinity: either would poison a ranking)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _fail(status: int) -> ProviderError:
    if status in (401, 403):
        return ProviderError("auth", status)
    if status == 402:
        return ProviderError("plan", status)
    if status == 429 or status >= 500:
        return ProviderError("busy", status)
    return ProviderError("refused", status)


class HttpTransport:
    """The real calls. The key is held here and nowhere else."""

    def __init__(self, api_key: str, base: str = API_BASE) -> None:
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._base = base
        self._http: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None

    async def __call__(
        self, method: str, path: str, body: Mapping[str, Any] | None
    ) -> tuple[int, bytes]:
        if self._http is None:
            self._http = aiohttp.ClientSession()
        try:
            async with self._http.request(
                method,
                self._base + path,
                data=None if body is None else json.dumps(body),
                headers=self._headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S, connect=10),
            ) as response:
                if response.status != 200:
                    return response.status, b""  # the body is never kept: it could echo the text
                data = bytearray()
                async for chunk in response.content.iter_chunked(1 << 16):
                    data += chunk
                    if len(data) > MAX_REPLY_BYTES:
                        raise ProviderError("reply")
                return response.status, bytes(data)
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise ProviderError("network") from exc


@dataclass(frozen=True, slots=True)
class Ranked:
    index: int  # the position in the documents sent
    score: float


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """1.0 for the same direction, 0 for unrelated, -1 for opposite; 0 when either is all zeros.
    Voyage's vectors are unit length, but this does not rely on it."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class Voyage:
    """The two calls. `tokens` counts what the service said it used, for the cost log."""

    def __init__(self, transport: Transport, *, embed_model: str, rerank_model: str) -> None:
        self._send = transport
        self.embed_model = embed_model
        self.rerank_model = rerank_model
        self.tokens = 0

    async def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]:
        """One vector for each text, in order. `query` is for the text searched with (the service
        makes it differently from the text searched in). An empty list sends nothing."""
        out: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            batch = [t[:MAX_CHARS] for t in texts[start : start + BATCH]]
            reply = await self._call(
                "/v1/embeddings",
                {
                    "input": batch,
                    "model": self.embed_model,
                    "input_type": "query" if query else "document",
                },
            )
            rows = reply.get("data")
            if not isinstance(rows, list) or len(rows) != len(batch):
                raise ProviderError("reply")
            vectors: list[list[float]] = [[] for _ in batch]
            for row in rows:
                if not isinstance(row, dict):
                    raise ProviderError("reply")
                index, vector = row.get("index"), row.get("embedding")
                if (
                    not isinstance(index, int)
                    or not 0 <= index < len(batch)
                    or not isinstance(vector, list)
                    or not vector
                    or not all(_number(x) for x in vector)
                ):
                    raise ProviderError("reply")
                vectors[index] = [float(x) for x in vector]
            if not all(vectors):
                raise ProviderError("reply")
            out += vectors
        return out

    async def rerank(self, query: str, documents: Sequence[str], *, top_k: int) -> list[Ranked]:
        """The `top_k` documents that best answer `query`, best first, with the service's score."""
        if not documents or top_k <= 0:
            return []
        reply = await self._call(
            "/v1/rerank",
            {
                "query": query[:MAX_CHARS],
                "documents": [d[:MAX_CHARS] for d in documents[:MAX_RERANK_DOCUMENTS]],
                "model": self.rerank_model,
                "top_k": top_k,
            },
        )
        rows = reply.get("data")
        if not isinstance(rows, list):
            raise ProviderError("reply")
        ranked: list[Ranked] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ProviderError("reply")
            index, score = row.get("index"), row.get("relevance_score")
            if (
                not isinstance(index, int)
                or not 0 <= index < min(len(documents), MAX_RERANK_DOCUMENTS)
                or not _number(score)
            ):
                raise ProviderError("reply")
            ranked.append(Ranked(index, float(score)))
        return sorted(ranked, key=lambda r: -r.score)[:top_k]

    async def _call(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        status, raw = await self._send("POST", path, body)
        if status != 200:
            raise _fail(status)
        try:
            reply = json.loads(raw)
        except ValueError:
            raise ProviderError("reply") from None
        if not isinstance(reply, dict):
            raise ProviderError("reply")
        usage = reply.get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("total_tokens"), int):
            self.tokens += usage["total_tokens"]
        return reply
