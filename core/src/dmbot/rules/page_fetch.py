"""Fetching ONE web page for homebrew (#1634), under the guards of `dmbot.fetch` plus the rules
of this feature (docs/PLAN.md, "Homebrew (part 2)"):

- one address, once, when a DM asks: never a crawl, a list of pages, or a link followed from it;
- https on port 443, and every address the host resolves to must be public (`dmbot.fetch`'s
  resolver; the connection goes to the addresses it checked);
- a redirect is followed only if it stays on an allowed site (at most 3, each checked);
- no cookies, no sign-in, a plain user agent that names DMbot;
- about 10 seconds and 2 MB (unpacked bytes), a page read a piece at a time;
- at most one page at a time per campaign and a few an hour (`FetchGate`).

It returns the status and the text; deciding what a 404 means or reading the words is
`dmbot.rules.dndbeyond`'s job. Nothing here logs an address or a page.
"""

from __future__ import annotations

import asyncio
import time
import zlib
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

import aiohttp
import yarl

from dmbot.fetch import (
    STRICT,
    LinkError,
    Policy,
    _inflate,
    _PublicResolver,
    check_url,
)

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 10
MAX_REDIRECTS = 3
USER_AGENT = (
    "DMbot (a Discord helper for D&D tables; reads one page when a DM pastes its link; "
    "https://github.com/smv620/Discord-DMbot)"
)
FETCHING = asyncio.Semaphore(3)  # pages read at once, across all campaigns
PER_WINDOW = 6  # pages a campaign may read in a window...
WINDOW_S = 600  # ...of ten minutes
BUSY = "DMbot is already reading a page for this campaign. Wait a moment, then try again."
TOO_OFTEN = "DMbot needs a short break from reading links. Try again in a few minutes."


class PageError(Exception):
    """Why a page could not be read, in plain words for the DM."""


@dataclass(frozen=True, slots=True)
class Reply:
    status: int
    text: str


class FetchGate:
    """One page at a time per campaign, and a small number per window. Held in memory: a restart
    forgets it, which only ever lets a DM try again sooner."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._busy: set[tuple[int, str]] = set()
        self._done: defaultdict[tuple[int, str], deque[float]] = defaultdict(deque)

    def enter(self, guild_id: int, campaign_id: str) -> None:
        """Take the campaign's place, or raise `PageError` saying why not."""
        key = (guild_id, campaign_id)
        if key in self._busy:
            raise PageError(BUSY)
        recent = self._done[key]
        now = self._clock()
        while recent and now - recent[0] > WINDOW_S:
            recent.popleft()
        if len(recent) >= PER_WINDOW:
            raise PageError(TOO_OFTEN)
        recent.append(now)
        self._busy.add(key)

    def leave(self, guild_id: int, campaign_id: str) -> None:
        self._busy.discard((guild_id, campaign_id))


async def fetch_page(
    url: str,
    *,
    redirect_ok: Callable[[str], bool],
    policy: Policy = STRICT,
) -> Reply:
    """The status and text of the page at `url` (already checked and normalised by the caller).
    A redirect is followed only when `redirect_ok(host)` says its host is allowed. Any failure
    to reach or read the page raises `PageError` with plain words (never the address)."""
    target = yarl.URL(url)
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
    try:
        # The place is taken first, then the clock starts (as dmbot.fetch does).
        async with FETCHING, asyncio.timeout(TIMEOUT_S):
            connector = aiohttp.TCPConnector(resolver=_PublicResolver(policy), use_dns_cache=False)
            async with aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                auto_decompress=False,
                cookie_jar=aiohttp.DummyCookieJar(),  # no cookies, ever
            ) as session:
                for _ in range(MAX_REDIRECTS + 1):
                    check_url(target, policy)
                    async with session.get(
                        target,
                        allow_redirects=False,
                        headers={"Accept-Encoding": "gzip", "User-Agent": USER_AGENT},
                    ) as resp:
                        if resp.status in (301, 302, 303, 307, 308):
                            location = resp.headers.get("Location")
                            if not location:
                                raise PageError("no place to go")
                            target = target.join(yarl.URL(location))
                            if not redirect_ok((target.host or "").casefold()):
                                raise PageError("left the site")
                            continue
                        return Reply(resp.status, await _text(resp))
                raise PageError("too many redirects")
    except PageError:
        raise
    except LinkError as exc:
        raise PageError(str(exc)) from None
    except (TimeoutError, aiohttp.ClientError, OSError, ValueError, zlib.error) as exc:
        if isinstance(exc.__cause__, LinkError):  # the resolver's refusal, wrapped
            raise PageError(str(exc.__cause__)) from None
        raise PageError("did not answer") from None


async def _text(resp: aiohttp.ClientResponse) -> str:
    """The body as text, at most `MAX_BYTES` of it unpacked; more is refused, not cut."""
    if (resp.content_length or 0) > MAX_BYTES:
        raise PageError("too big")
    encoding = resp.headers.get("Content-Encoding", "identity").strip().casefold()
    if encoding in ("", "identity"):
        inflate = None
    elif encoding in ("gzip", "x-gzip"):
        inflate = zlib.decompressobj(zlib.MAX_WBITS | 16)
    else:
        raise PageError("an answer DMbot cannot read")
    data = bytearray()
    sent = 0
    # (`_inflate` stops at dmbot.fetch's own 10 MB and one megabyte a step; the 2 MB checked here
    # then refuses the page, so at most about 3 MB is ever held.)
    async for piece in resp.content.iter_chunked(64 * 1024):
        sent += len(piece)
        if sent > MAX_BYTES:
            raise PageError("too big")
        if inflate is None:
            data += piece
        else:
            try:
                await _inflate(inflate, piece, data)
            except LinkError:
                raise PageError("too big") from None
        if len(data) > MAX_BYTES:
            raise PageError("too big")
    if inflate is not None and not inflate.eof:
        raise PageError("cut short")
    try:
        return bytes(data).decode(resp.charset or "utf-8", errors="replace")
    except LookupError:  # a charset Python does not know
        return bytes(data).decode("utf-8", errors="replace")
