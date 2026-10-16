"""Addresses and site names for imported homebrew (#1633): pure string work, no network.

A page is kept once per address, and a campaign approves a *site* (its domain) once, so both are
compared in one normal form: `normalise_url` and `normalise_domain`. Only https addresses with a
plain host name are accepted: an IP address, a port, a sign-in in the address or a name with odd
characters is refused, because the address is untrusted text from a DM (and, in a backup, from a
file). Nothing here fetches anything.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

URL_MAX = 2000
DOMAIN_MAX = 253
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")
_IP = re.compile(r"^[0-9.]+$")
_HEX = re.compile(r"^0x[0-9a-f]*$")


def normalise_domain(host: str) -> str | None:
    """The site a host belongs to, in the form approvals are kept in: lower case, no trailing dot
    and no leading `www.` (so `www.dndbeyond.com` and `dndbeyond.com` are one site). None when it
    is not a plain ASCII host name. A name whose last part is a number (`127.1`, `0x7f.1`) is
    refused, because resolvers read it as an IP address. This is a check on what is *written*; it
    is not a defence against a name that *resolves* to a private address: whatever fetches a page
    must check the address it resolves to."""
    if not host.isascii():  # (before lower-casing: some letters fold to ASCII ones)
        return None
    name = host.strip().lower().rstrip(".")
    if name.startswith("www.") and "." in name[4:]:
        name = name[4:]
    if not name or len(name) > DOMAIN_MAX or _IP.match(name) or not _DOMAIN.match(name):
        return None
    last = name.rsplit(".", 1)[1]
    if last.isdigit() or _HEX.match(last):
        return None
    return name


def normalise_url(url: str) -> tuple[str, str] | None:
    """(the address in its one normal form, its site) or None. https only, a plain host name, no
    sign-in or port, no fragment, no trailing slash on a path. The path and query are kept
    as written (they name the page)."""
    text = url.strip()
    if not text or len(text) > URL_MAX or any(c.isspace() or ord(c) < 32 for c in text):
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname
    if parts.scheme.lower() != "https" or not host or parts.username or parts.password:
        return None
    if port not in (None, 443):
        return None
    domain = normalise_domain(host)
    if domain is None:
        return None
    path = parts.path.rstrip("/") if parts.path != "/" else ""
    query = f"?{parts.query}" if parts.query else ""
    out = f"https://{host.lower().rstrip('.')}{path}{query}"
    return (out, domain) if len(out) <= URL_MAX else None
