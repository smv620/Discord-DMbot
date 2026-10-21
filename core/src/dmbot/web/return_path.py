"""Where sign-in sends a person back to (#2301): only a page under /account on this site.

The sign-in link may carry `next=/account/import/<server>.<campaign>`, so a person who opened
a page signed out comes back to that page, not the account home. Only a path under /account
made of plain address characters is accepted, and the redirect is always built on the site's own
address, so no value from a link can send anyone to another site (an open redirect).
"""

from __future__ import annotations

import re

MAX_LENGTH = 200

# /account, or /account/ followed by plain segments. No query, no fragment, no dots-only
# segment (".." would climb out), no backslash or doubled slash (browsers read those as hosts).
_PATH = re.compile(r"/account(?:/[A-Za-z0-9._~-]+)*/?")


def safe_return_path(raw: str | None) -> str | None:
    """The path to go back to, or None for anything that isn't a plain page under /account."""
    if not raw or len(raw) > MAX_LENGTH or not _PATH.fullmatch(raw):
        return None
    if any(segment in (".", "..") for segment in raw.split("/")):
        return None
    return raw
