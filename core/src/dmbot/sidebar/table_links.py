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
_TRAILING = ".,;:!?'\""
OFFICIAL_HOST = "dndbeyond.com"


def _normal(url: str) -> str:
    """A link as compared: scheme and host in lower case, no trailing slash or fragment."""
    parts = urlsplit(url.strip())
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}" + (
        f"?{parts.query}" if parts.query else ""
    )


def is_official(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == OFFICIAL_HOST or host.endswith("." + OFFICIAL_HOST)


def keep_allowed(text: str, campaign_links: Iterable[str]) -> str:
    """`text` with every link removed except the first one that is allowed."""
    allowed = {_normal(link) for link in campaign_links}
    kept = False

    def decide(match: re.Match[str]) -> str:
        nonlocal kept
        url = match.group().rstrip(_TRAILING)
        tail = match.group()[len(url) :]
        if not kept and (is_official(url) or _normal(url) in allowed):
            kept = True
            return match.group()
        return tail

    return re.sub(r" {2,}", " ", _URL.sub(decide, text)).strip()


def has_link(text: str) -> bool:
    return bool(_URL.search(text))
