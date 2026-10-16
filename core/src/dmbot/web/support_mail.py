"""Support mail in and out (#1431): the reply address that carries a ticket's number and a
signed token, reading a person's emailed reply into the ticket, and checking that the mail
service's inbound webhook is really its own.

A reply goes to `info+ticket-<id>-<token>@...` (the Reply-To of every email about a ticket). The
token is an HMAC of the ticket's number under the site's link secret, so a stranger can't write
into someone's ticket by guessing the number, and the sender must also be the address on the
ticket owner's account. Anything else is dropped and only counted (the log says how many, never
who or what). No words are logged here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from collections.abc import Iterable, Mapping
from email.utils import parseaddr

TOKEN_CHARS = 20  # hex characters of the HMAC kept: 80 bits, far past guessing
ADDRESS = re.compile(rf"\+ticket-(\d{{1,19}})-([0-9a-f]{{{TOKEN_CHARS}}})@")
SIGNATURE_WINDOW_S = 300  # a webhook older than this is a replay
MAX_BODY_BYTES = 256_000  # one inbound message, before it is read at all


def ticket_token(secret: bytes, ticket_id: int) -> str:
    digest = hmac.new(secret, f"support-ticket:{ticket_id}".encode(), hashlib.sha256)
    return digest.hexdigest()[:TOKEN_CHARS]


def reply_address(sender: str, ticket_id: int, secret: bytes) -> str | None:
    """The Reply-To for a ticket's emails, made from the support sender (`info@x` becomes
    `info+ticket-12-<token>@x`). None when there is no usable sender or secret."""
    address = parseaddr(sender)[1]
    local, at, domain = address.partition("@")
    if not (local and at and domain and secret) or "+" in local:
        return None
    return f"{local}+ticket-{ticket_id}-{ticket_token(secret, ticket_id)}@{domain}"


def ticket_of(addresses: Iterable[str], secret: bytes) -> int | None:
    """The ticket a message was sent to, from its To addresses, when the token is right."""
    for raw in addresses:
        found = ADDRESS.search(parseaddr(raw)[1].lower())
        if found is None:
            continue
        ticket_id = int(found.group(1))
        if hmac.compare_digest(found.group(2), ticket_token(secret, ticket_id)):
            return ticket_id
    return None


_QUOTE_START = re.compile(
    r"^\s*(>|On .{5,120} wrote:\s*$|-{2,}\s*Original Message|From:\s.+|Sent from my "
    # DMbot's own closing line, which asks for the word PERSON: below it everything is quoted from
    # our email even if the mail program left no ">" (it would hand every ticket over, #1518).
    r"|Want to talk to a person instead\?|Want a person instead\?|Didn't help\? Reply)",
    re.IGNORECASE,
)


def fresh_words(text: str, limit: int = 4000) -> str:
    """What the person wrote, without the email they replied to: cut at the first quoted line or
    "On ... wrote:" line, tidied and shortened. Empty when nothing is left."""
    kept: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _QUOTE_START.match(line):
            break
        kept.append(line.rstrip())
    words = "\n".join(kept).strip()
    words = "".join(c for c in words if c == "\n" or c == "\t" or c.isprintable())
    return words[:limit].strip()


def verify_signature(secret: str, headers: Mapping[str, str], body: bytes, *, now: float) -> bool:
    """A webhook signed the Svix way (the mail service's inbound webhooks): the secret is
    `whsec_<base64>`; the signature is HMAC-SHA256 of `<id>.<timestamp>.<body>`, sent as
    `v1,<base64>` (several, space separated, while a secret is being changed)."""
    if not secret:
        return False
    key_text = secret.removeprefix("whsec_")
    try:
        key = base64.b64decode(key_text, validate=True)
    except ValueError:
        return False
    if len(key) < 16:  # "whsec_" alone would make a key anyone can sign with
        return False
    message_id = headers.get("svix-id", "")
    stamp = headers.get("svix-timestamp", "")
    given = headers.get("svix-signature", "")
    if not (message_id and stamp.isascii() and stamp.isdigit() and given):
        return False
    if abs(now - int(stamp)) > SIGNATURE_WINDOW_S:
        return False
    signed = f"{message_id}.{stamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(
        part.startswith("v1,") and hmac.compare_digest(part[3:], expected) for part in given.split()
    )


def sender_address(raw: object) -> str:
    """The sender's bare lower-case address from a string or an {email: ...} object."""
    if isinstance(raw, dict):
        raw = raw.get("email") or raw.get("address") or ""
    return parseaddr(str(raw))[1].lower() if isinstance(raw, str) else ""


def recipients(raw: object) -> list[str]:
    """The To addresses as a list of strings, from a string, a list, or {email: ...} objects."""
    if isinstance(raw, str):
        return [raw]
    out: list[str] = []
    if isinstance(raw, list):
        for item in raw[:20]:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict) and isinstance(item.get("email"), str):
                out.append(item["email"])
    return out
