"""Which links may appear in an answer posted in the table's DMbot chat (#1187).

The hard rule (CLAUDE.md, "DM authority", "Table questions"): at most one link per answer, and
only to dndbeyond.com or to a public link shared with this campaign (its house-rules file of
record, or another link the DM gave it). Checked here in code, never left to the model, so a
link the AI wrote (or copied from a page) that is not on the list is dropped, and a link from
another campaign never gets through because the list is built from this campaign alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

_URL = re.compile(r"https?://[^\s<>()\[\]]+", re.IGNORECASE)
# What Discord (and a browser) would still turn into a link without "http://": "www.x.com",
# "discord.gg/abc", "evil.com/x". The model must not slip one in, so these go too.
_BARE = re.compile(
    r"\bwww\.[a-z0-9.-]+\b(?:/\S*)?|"
    r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|gg|ly|me|app|dev|co|info|xyz|"
    r"link|page|site|online|tv|us|uk|de|fr|ru|cn|to)\b(?:/\S*)?",
    re.IGNORECASE,
)
_TRAILING = ".,;:!?'\""
OFFICIAL_HOST = "dndbeyond.com"


def _normal(url: str) -> str:
    """A link as compared: scheme and host in lower case, no trailing slash or fragment."""
    parts = urlsplit(url.strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}" + (
        f"?{parts.query}" if parts.query else ""
    )


def is_plain(url: str) -> bool:
    """An ordinary https address: no backslash, no user-info (`https://evil.com\\@dndbeyond.com`
    reads as dndbeyond.com to Python but goes to evil.com in a browser), plain ASCII."""
    if not url.isascii() or "\\" in url or "@" in url or not url.lower().startswith("https://"):
        return False
    parts = urlsplit(url)
    return bool(parts.hostname) and parts.username is None and parts.port in (None, 443)


def is_official(url: str) -> bool:
    if not is_plain(url):
        return False
    host = (urlsplit(url).hostname or "").lower()
    return host == OFFICIAL_HOST or host.endswith("." + OFFICIAL_HOST)


def keep_allowed(text: str, campaign_links: Iterable[str]) -> str:
    """`text` with every link removed except the first one that is allowed."""
    return keep_allowed_in([text], campaign_links)[0]


def keep_allowed_in(texts: Iterable[str], campaign_links: Iterable[str]) -> list[str]:
    """Several texts that are posted together: at most one link in all of them, and only an
    allowed one. A bare address ("evil.com/x") is dropped too."""
    allowed = {_normal(link) for link in campaign_links if is_plain(link)}
    kept = False

    def decide(match: re.Match[str]) -> str:
        nonlocal kept
        url = match.group().rstrip(_TRAILING)
        tail = match.group()[len(url) :]
        if not kept and (is_official(url) or (is_plain(url) and _normal(url) in allowed)):
            kept = True
            return match.group()
        return tail

    out = []
    for text in texts:
        marked = _URL.sub(decide, text)
        # Bare addresses are cut from what is left, but not from the link just kept.
        pieces = re.split(r"(https?://\S+)", marked)
        cleaned = "".join(
            piece if piece.lower().startswith("http") else _BARE.sub("", piece) for piece in pieces
        )
        out.append(re.sub(r" {2,}", " ", cleaned).strip())
    return out


def strip_links(text: str) -> str:
    """What a person typed, with every link (and bare address) taken out: DMbot repeats a
    question in the chat, and must not become the way a link gets posted there."""
    return re.sub(r" {2,}", " ", _BARE.sub("", _URL.sub("", text))).strip()


def has_link(text: str) -> bool:
    return bool(_URL.search(text))
