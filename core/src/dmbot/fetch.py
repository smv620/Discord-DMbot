"""Opening a link someone gave DMbot: a shared document or a web page (#264).

DMbot fetches whatever address a DM pastes, so every fetch is guarded against reaching
anything that isn't on the public internet (server-side request forgery):

- https only, on the normal port (443), and at most 3 redirects, each checked again;
- every address a host name resolves to must be public (no private, loopback,
  link-local, cloud-metadata or other special addresses). The connection goes to the
  addresses that were checked, so a second DNS answer can't swap in a private one;
- at most 10 MB and 30 seconds. The 10 MB counts unpacked bytes: a compressed reply is
  unpacked here a piece at a time, never all at once by aiohttp.

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
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field

import aiohttp
import yarl
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import DefaultResolver

MAX_BYTES = 10 * 1024 * 1024
TIMEOUT_S = 30
MAX_REDIRECTS = 3
FETCHING = asyncio.Semaphore(3)  # links opened at once, across all servers
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
    # Where those send someone who isn't signed in.
    "accounts.google.com",
    "login.microsoftonline.com",
    "login.live.com",
)
_DRIVE_HOSTS = ("drive.google.com", "drive.usercontent.google.com")
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


NOT_A_LINK = (
    "That doesn't look like a link. Open the page, copy the whole link, and paste it here. "
    "To add names you typed, use 📋 Paste a list."
)
ONLY_HTTPS = (
    "DMbot can only open links that start with https://. Download the file and add it "
    "with 📎 Upload a file instead."
)
NOT_PUBLIC = (
    "DMbot can't open that link. Use a link anyone can open, or download the file and add "
    "it with 📎 Upload a file."
)
NOT_SHARED = (
    "DMbot can't open that link: it may be private or mistyped. In Google Docs press Share "
    'and set General access to "Anyone with the link", then try again. Or download the '
    "file and add it with 📎 Upload a file."
)
DRIVE_PAGE = (
    "DMbot can't open that Google Drive file: it may be private, or too big for Google to "
    "hand over. Download it and add it with 📎 Upload a file."
)
TOO_BIG = (
    "That's too big for DMbot (up to 10 MB). Split it into smaller files and add them with "
    "📎 Upload a file."
)
UNREACHABLE = (
    "DMbot couldn't open that link just now. Make sure it opens in your browser, then try "
    "again in a minute."
)
WRONG_TYPE = (
    "DMbot can read web pages and PDF, Word or text files. That link is something else. "
    "Download it, save it as one of those, and add it with 📎 Upload a file."
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_V4_IN_V6 = (ipaddress.ip_network("::/96"), ipaddress.ip_network("::ffff:0:0:0/96"))
# A host made only of digits and dots (or with a colon) is meant as an IP address, even
# when it isn't written the usual way (127.1, 2130706433): never treat it as a name.
_NUMERIC_HOST = re.compile(r"^[0-9.]+$|:")
# A link pasted without https:// in front: one word, a host name, then a path.
_BARE_LINK = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+/\S*$", re.IGNORECASE)


def is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """On the public internet: not private, loopback, link-local (cloud metadata),
    carrier-grade NAT, multicast, reserved or documentation space."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:  # IPv6 that a NAT64 gateway turns into this IPv4 address
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif ip.sixtofour is not None:  # 6to4: the IPv4 address is inside
            ip = ip.sixtofour
        elif ip.teredo is not None:  # Teredo: the server and the client are inside
            return all(is_public(v4) for v4 in ip.teredo)
        elif any(ip in net for net in _V4_IN_V6):  # older ways to write IPv4 in IPv6
            return False
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
    if text.startswith("www.") or _BARE_LINK.match(text):
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
        token = base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")  # as pasted
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
        if _NUMERIC_HOST.search(url.host):
            raise LinkError(NOT_PUBLIC) from None  # an IP address written oddly
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


def sign_in_page(ending: str, url: yarl.URL, *hosts: str) -> bool:
    """A file host answered with a web page instead of the file: it wants a sign-in, or
    the file isn't shared with everyone. Its text must never reach the AI. `hosts`: every
    host on the way, so a short link to a file host is caught too. A Google Doc
    published to the web (…/pub) is a public page."""
    if ending != ".html":
        return False
    if (url.host or "").casefold() == "docs.google.com" and url.path.endswith("/pub"):
        return False
    return any(_host_is(h.casefold(), d) for h in hosts for d in _FILE_HOSTS)


def sign_in_message(*hosts: str) -> str:
    """What to tell the DM about a file host's page. Google Drive also sends one for a
    file too big for it to scan for viruses: downloading it is the way."""
    drive = any(_host_is(h.casefold(), d) for h in hosts for d in _DRIVE_HOSTS)
    return DRIVE_PAGE if drive else NOT_SHARED


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
    hosts = [target.host or ""]
    timeout = aiohttp.ClientTimeout(total=TIMEOUT_S)
    try:
        # The place is taken first, then the clock starts: waiting behind other links
        # never uses up this link's time.
        async with FETCHING, asyncio.timeout(TIMEOUT_S):
            connector = aiohttp.TCPConnector(resolver=_PublicResolver(policy), use_dns_cache=False)
            async with aiohttp.ClientSession(
                timeout=timeout, connector=connector, auto_decompress=False
            ) as session:
                for _ in range(MAX_REDIRECTS + 1):
                    check_url(target, policy)
                    async with session.get(
                        target, allow_redirects=False, headers={"Accept-Encoding": "gzip"}
                    ) as resp:
                        if resp.status in (301, 302, 303, 307, 308):
                            location = resp.headers.get("Location")
                            if not location:
                                raise LinkError(UNREACHABLE)
                            target = target.join(yarl.URL(location))
                            hosts.append(target.host or "")
                            continue
                        return await _read(resp, target, hosts)
                raise LinkError(NOT_SHARED)  # a redirect loop is usually a sign-in page
    except LinkError:
        raise
    except (TimeoutError, aiohttp.ClientError, OSError, ValueError) as exc:
        if isinstance(exc.__cause__, LinkError):  # the resolver's refusal, wrapped
            raise exc.__cause__ from None
        raise LinkError(UNREACHABLE) from None


async def _read(resp: aiohttp.ClientResponse, url: yarl.URL, hosts: list[str]) -> Fetched:
    if resp.status in (401, 403, 404, 410):
        raise LinkError(NOT_SHARED)
    if resp.status != 200:
        raise LinkError(UNREACHABLE)
    ending = _ending(resp, url)
    if ending is None:
        raise LinkError(WRONG_TYPE)
    if sign_in_page(ending, url, *hosts):
        raise LinkError(sign_in_message(*hosts))
    if (resp.content_length or 0) > MAX_BYTES:
        raise LinkError(TOO_BIG)
    encoding = resp.headers.get("Content-Encoding", "identity").strip().casefold()
    if encoding in ("", "identity"):
        inflate = None
    elif encoding in ("gzip", "x-gzip", "deflate"):
        inflate = zlib.decompressobj(zlib.MAX_WBITS | 32)  # a gzip or zlib header
    else:
        raise LinkError(WRONG_TYPE)  # never asked for: DMbot only accepts gzip
    data = bytearray()
    try:
        async for piece in resp.content.iter_chunked(64 * 1024):
            if inflate is not None:
                # Never unpack more than the room left: a tiny "zip bomb" stays tiny.
                piece = inflate.decompress(piece, MAX_BYTES + 1 - len(data))
                if inflate.unconsumed_tail:
                    raise LinkError(TOO_BIG)
            data += piece
            if len(data) > MAX_BYTES:
                raise LinkError(TOO_BIG)
    except zlib.error:
        raise LinkError(UNREACHABLE) from None
    return Fetched(bytes(data), "link" + ending)
