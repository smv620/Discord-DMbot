"""Personal links (docs/PLAN.md, "Accounts"): `uid` plus a signed token, so the greeting and
a person's experience points on the website can't be shown by guessing an ID.

A token is `<expires>.<signature>`: the signature is an HMAC-SHA256, keyed with a server
secret (`LINK_TOKEN_SECRET`), over the DMbot ID and the expiry. It lasts 30 days. Anyone
holding the link can see what the link shows (a name and a total of minutes), nothing more,
so it opens no account. The secret never leaves the server and is never logged.
"""

from __future__ import annotations

import hashlib
import hmac

LIFETIME_S = 30 * 86400
_SIGNATURE_CHARS = 32  # hex characters kept (128 bits)


def _sign(dmbot_id: str, expires: int, secret: bytes) -> str:
    message = f"{dmbot_id}.{expires}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:_SIGNATURE_CHARS]


def make_link_token(dmbot_id: str, now: int, secret: bytes) -> str:
    """A token for this DMbot ID, good for 30 days from `now`."""
    if not secret:
        raise ValueError("Link tokens need a secret.")
    expires = now + LIFETIME_S
    return f"{expires}.{_sign(dmbot_id, expires, secret)}"


def check_link_token(dmbot_id: str, token: str, now: int, secret: bytes) -> bool:
    """True only for a token made for this ID with this secret that has not run out."""
    if not secret:
        return False
    stamp, dot, signature = token.partition(".")
    if not dot or not stamp.isascii() or not stamp.isdigit() or len(stamp) > 12:
        return False
    expires = int(stamp)
    good = _sign(dmbot_id, expires, secret)
    # compare_digest on every path that reaches the secret: no early exit on a near match
    if not hmac.compare_digest(signature.encode(), good.encode()):
        return False
    return now <= expires
