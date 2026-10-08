"""Short-lived signed values for the web API (#435): the sign-in and install checks
(OAuth `state`) and the account-deletion confirmation.

A token is `purpose.data.expires.nonce.mac`, signed with WEB_SECRET_KEY. The purpose is
part of what's signed, so a token made for one thing can't be used for another. Anything
malformed, expired, or signed with another key reads as None, never as an error.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


def _mac(secret: bytes, message: str) -> str:
    digest = hmac.new(secret, message.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def make(secret: bytes, purpose: str, data: str, *, now: int, seconds: int) -> str:
    if "." in purpose or "." in data:
        raise ValueError("purpose and data can't contain dots")
    body = f"{purpose}.{data}.{now + seconds}.{secrets.token_urlsafe(16)}"
    return f"{body}.{_mac(secret, body)}"


def read(secret: bytes, purpose: str, token: str, *, now: int) -> str | None:
    """The token's data if it's ours, for this purpose and unexpired; otherwise None."""
    try:
        token.encode("ascii")
        body, mac = token.rsplit(".", 1)
        got_purpose, data, expires_raw, _nonce = body.split(".")
        expires = int(expires_raw)
    except (ValueError, UnicodeError):
        return None
    if got_purpose != purpose or not hmac.compare_digest(_mac(secret, body), mac):
        return None
    return data if now < expires else None
