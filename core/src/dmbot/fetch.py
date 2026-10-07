"""Opening a link someone gave DMbot: a shared document or a web page (#264).

DMbot fetches whatever address a DM pastes, so every fetch is guarded against reaching
anything that isn't on the public internet (server-side request forgery):

- https only, on the normal port (443), and at most 3 redirects, each checked again;
- every address a host name resolves to must be public (no private, loopback,
  link-local, cloud-metadata or other special addresses). The connection goes to the
  addresses that were checked, so a second DNS answer can't swap in a private one;
- at most 10 MB and 30 seconds.

Share links from the usual file hosts (Google Docs and Drive, Dropbox, OneDrive and
SharePoint) are turned into their download address first. Links are never logged: they
can hold private tokens.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import re
import socket
from collections.abc import Callable
from dataclasses import dataclass, field

import aiohttp
import yarl
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import DefaultResolver

MAX_BYTES = 10 * 1024 * 1024
TIMEOUT_S = 30
MAX_REDIRECTS = 3
_GDOC = re.compile(r"^/document/(?:u/\d+/)?d/([A-Za-z0-9_-]{20,})")
_GDRIVE = re.compile(r"^/file/(?:u/\d+/)?d/([A-Za-z0-9_-]{20,})")
# File hosts whose web pages (not files) mean "sign in" or "not shared with everyone".
_FILE_HOSTS = (
    "docs.google.com",
    "drive.google.com",
    "drive.usercontent.google.com",
    "dropbox.com",
    "dropboxusercontent.com",
    "onedrive.live.com",
    "api.onedrive.com",
    "1drv.ms",
    "sharepoint.com",
)
# What the reply is, from its Content-Type: the ending text_of reads it by.
_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    "text/plain": ".txt",
    "text/markdown": ".txt",
    "text/csv": ".txt",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
}
_ENDINGS = (".pdf", ".docx", ".doc", ".txt", ".md", ".csv", ".html", ".htm")


class LinkError(Exception):
    """Plain words for the DM: why the link can't be read and what to do."""


NOT_A_LINK = "That doesn't look like a link. Copy the whole address, starting with https://"
ONLY_HTTPS = (
    "DMbot only opens secure links (starting with https://). Download the file and add it "
    "with 📎 Upload a file instead."
)
NOT_PUBLIC = "DMbot can't open that address. Use a link anyone on the internet can open."
NOT_SHARED = (
    "DMbot can't open that link: it may need a sign-in. Share it so anyone with the link "
    "can view it, and try again. Or download the file and add it with 📎 Upload a file."
)
TOO_BIG = "That's too big (up to 10 MB). Split it into smaller files."
UNREACHABLE = "DMbot couldn't reach that link. Check it, then try again in a minute."
WRONG_TYPE = (
    "DMbot can read documents (.pdf, .docx, .txt) and web pages. That link is something "
    "else. Download it, save it as one of those, and add it with 📎 Upload a file."
)


def is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """On the public internet: not private, loopback, link-local (cloud metadata),
    carrier-grade NAT, multicast, reserved or documentation space."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


@dataclass(frozen=True, slots=True)
class Policy:
    """Which addresses a fetch may reach. Tests loosen it for a local server."""

    schemes: frozenset[str] = frozenset({"https"})
    ports: frozenset[int] = frozenset({443})
    allow_ip: Callable[[ipaddress.IPv4Address | ipaddress.IPv6Address], bool] = field(
        default=is_public
    )


STRICT = Policy()


def _host_is(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def direct_url(link: str) -> yarl.URL:
    """The address to fetch for a link: share pages of the usual file hosts become their
    download address; anything else is fetched as it is."""
    text = link.strip().strip("<>")
    if text.startswith("www."):
        text = "https://" + text
    try:
        url = yarl.URL(text)
    except ValueError as exc:
        raise LinkError(NOT_A_LINK) from exc
    if not url.scheme or not url.host:
        raise LinkError(NOT_A_LINK)
    host = url.host.casefold()
    if host == "docs.google.com" and (m := _GDOC.match(url.path)):
        return yarl.URL(f"https://docs.google.com/document/d/{m[1]}/export?format=txt")
    if host == "drive.google.com" and (m := _GDRIVE.match(url.path)):
        return yarl.URL(f"https://drive.google.com/uc?export=download&id={m[1]}")
    if _host_is(host, "dropbox.com"):
        return url.update_query(dl="1")
    if host in ("1drv.ms", "onedrive.live.com"):
        # OneDrive's share pages are a web app; its sharing API serves the file itself.
        token = base64.urlsafe_b64encode(str(url).encode()).decode().rstrip("=")
        return yarl.URL(f"https://api.onedrive.com/v1.0/shares/u!{token}/root/content")
    if _host_is(host, "sharepoint.com"):
        return url.update_query(download="1")
    return url


def check_url(url: yarl.URL, policy: Policy = STRICT) -> None:
    """Before each request (and each redirect): the scheme, the port, and an address
    written as a number. Host names are checked when they're resolved."""
    if url.scheme not in policy.schemes:
        raise LinkError(ONLY_HTTPS if url.scheme == "http" else NOT_A_LINK)
    if not url.host:
        raise LinkError(NOT_A_LINK)
    if url.port not in policy.ports or url.user or url.password:
        raise LinkError(NOT_PUBLIC)
    try:
        ip = ipaddress.ip_address(url.host.strip("[]"))
    except ValueError:
        return  # a name: the resolver checks what it points to
    if not policy.allow_ip(ip):
        raise LinkError(NOT_PUBLIC)


class _PublicResolver(AbstractResolver):
    """Resolves like aiohttp does, then refuses the host if ANY address isn't allowed.
    aiohttp connects to exactly the addresses returned here."""

    def __init__(self, policy: Policy) -> None:
        self._inner = DefaultResolver()
        self._policy = policy

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        found = await self._inner.resolve(host, port, family)
        if not found or not all(
            self._policy.allow_ip(ipaddress.ip_address(r["host"])) for r in found
        ):
            raise LinkError(NOT_PUBLIC)
        return found

    async def close(self) -> None:
        await self._inner.close()


def sign_in_page(ending: str, *hosts: str) -> bool:
    """A file host answered with a web page instead of the file: it wants a sign-in, or
    the file isn't shared with everyone. Its text must never reach the AI."""
    return ending == ".html" and any(_host_is(h, d) for h in hosts for d in _FILE_HOSTS)


@dataclass(frozen=True, slots=True)
class Fetched:
    data: bytes
    filename: str  # an ending text_of reads it by: "link.pdf", "link.html", ...


def _ending(resp: aiohttp.ClientResponse, url: yarl.URL) -> str | None:
    kind = _TYPES.get(resp.content_type.casefold())
    if kind:
        return kind
    # A file host's "octet-stream": go by the file's name.
    names = [url.path.casefold()]
    disposition = resp.headers.get("Content-Disposition", "")
    if m := re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition, re.I):
        names.insert(0, m[1].casefold())
    for name in names:
        for ending in _ENDINGS:
            if name.endswith(ending):
                return {".md": ".txt", ".csv": ".txt", ".htm": ".html"}.get(ending, ending)
    return None


async def fetch(link: str, policy: Policy = STRICT) -> Fetched:
    """The file or page behind a link. Raises LinkError in plain words for anything that
    goes wrong (never another error, and never with the link in it)."""
    target = direct_url(link)
    first_host = (target.host or "").casefold()
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
    connector = aiohttp.TCPConnector(resolver=_PublicResolver(policy), use_dns_cache=False)
    try:
        async with (
            asyncio.timeout(TIMEOUT_S),
            aiohttp.ClientSession(timeout=timeout, connector=connector) as session,
        ):
            for _ in range(MAX_REDIRECTS + 1):
                check_url(target, policy)
                async with session.get(target, allow_redirects=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        target = target.join(yarl.URL(resp.headers.get("Location", "")))
                        continue
                    return await _read(resp, target, first_host)
            raise LinkError(NOT_SHARED)  # a redirect loop is usually a sign-in page
    except LinkError:
        raise
    except (TimeoutError, aiohttp.ClientError, OSError, ValueError) as exc:
        if isinstance(exc.__cause__, LinkError):  # the resolver's refusal, wrapped
            raise exc.__cause__ from None
        raise LinkError(UNREACHABLE) from None


async def _read(resp: aiohttp.ClientResponse, url: yarl.URL, first_host: str) -> Fetched:
    if resp.status in (401, 403, 404, 410):
        raise LinkError(NOT_SHARED)
    if resp.status != 200:
        raise LinkError(UNREACHABLE)
    ending = _ending(resp, url)
    if ending is None:
        raise LinkError(WRONG_TYPE)
    if sign_in_page(ending, (url.host or "").casefold(), first_host):
        raise LinkError(NOT_SHARED)
    if (resp.content_length or 0) > MAX_BYTES:
        raise LinkError(TOO_BIG)
    data = bytearray()
    async for piece in resp.content.iter_chunked(64 * 1024):
        data += piece
        if len(data) > MAX_BYTES:
            raise LinkError(TOO_BIG)
    return Fetched(bytes(data), "link" + ending)
