"""Account numbers, personal links and the doors to email and move codes (#1231; docs/PLAN.md,
"Accounts").

TEMPORARY: the DMbot ID and the personal link token are CloudDev's #1228 work, and the email
sender and move codes are #1229 and #1230. Until those merge, this file holds small stand-ins
with the names and shapes the issues give (`to_dmbot_id`, `make_link_token`, `check_link_token`,
a `Mailer`, a `MoveCodes`), so the website's routes and pages can be built and tested now. When
they merge, delete the stand-ins and import theirs; the routes call only these names.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Protocol

log = logging.getLogger(__name__)

# The DMbot ID is the Discord ID written in base 36 (0-9 then A-Z): Stripe's invoice prefix
# allows only capital letters and digits, up to 12 of them.
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MAX_ID_LENGTH = 12
LINK_TOKEN_SECONDS = 30 * 86400  # a personal link lasts 30 days


def to_dmbot_id(user_id: int) -> str:
    """The DMbot ID for a Discord ID."""
    if user_id <= 0:
        raise ValueError("a Discord ID is a positive number")
    digits = ""
    while user_id:
        user_id, rest = divmod(user_id, 36)
        digits = ALPHABET[rest] + digits
    return digits


def from_dmbot_id(text: str) -> int | None:
    """The Discord ID behind a DMbot ID, or None if it isn't one."""
    if not text or len(text) > MAX_ID_LENGTH or any(c not in ALPHABET for c in text):
        return None
    number = 0
    for c in text:
        number = number * 36 + ALPHABET.index(c)
    return number or None


def link_secret(web_secret: bytes) -> bytes:
    """The key personal links are signed with, made from the website's own secret so it needs no
    setting of its own yet (#1228 adds LINK_TOKEN_SECRET), and apart from every other use of that
    secret."""
    return hmac.new(web_secret, b"dmbot-personal-link", hashlib.sha256).digest()


def _mac(secret: bytes, dmbot_id: str, expires: int) -> str:
    digest = hmac.new(secret, f"{dmbot_id}.{expires}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def make_link_token(secret: bytes, dmbot_id: str, now: int) -> str:
    """A signed code for a personal link to /pricing or /hello, good for 30 days."""
    expires = now + LINK_TOKEN_SECONDS
    return f"{expires}.{_mac(secret, dmbot_id, expires)}"


def check_link_token(secret: bytes, dmbot_id: str, token: str, now: int) -> bool:
    """The code is ours, for this ID, and not expired. Anything malformed is just no."""
    try:
        expires_raw, given = token.split(".")
        expires = int(expires_raw)
    except ValueError:
        return False
    # Bytes, so a stray non-ASCII character is just "no" and not an error.
    return now < expires and hmac.compare_digest(
        given.encode(), _mac(secret, dmbot_id, expires).encode()
    )


class Mailer(Protocol):
    """Sends the account emails. The real one is #1229's; the site only needs this."""

    async def send_sign_in_link(self, to: str, link: str) -> None: ...


class NoMailer:
    """No email sender is set up yet: nothing is sent, and the sign-in page still answers in
    the same words (it never says whether an address has an account)."""

    async def send_sign_in_link(self, to: str, link: str) -> None:
        log.warning("A sign-in link was asked for but email sending isn't set up")


class MoveCodes(Protocol):
    """Makes a move code and emails it to the account's payment email. The real one is #1230's."""

    async def send_code(self, user_id: int) -> None: ...
