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
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from email.utils import parseaddr

log = logging.getLogger(__name__)

TOKEN_CHARS = 20  # hex characters of the HMAC kept: 80 bits, far past guessing
ADDRESS = re.compile(rf"\+ticket-(\d{{1,19}})-([0-9a-f]{{{TOKEN_CHARS}}})@")
SIGNATURE_WINDOW_S = 300  # a webhook older than this is a replay
MAX_BODY_BYTES = 256_000  # one inbound message, before it is read at all


def ticket_token(secret: bytes, ticket_id: int) -> str:
    digest = hmac.new(secret, f"support-ticket:{ticket_id}".encode(), hashlib.sha256)
    return digest.hexdigest()[:TOKEN_CHARS]


_PLAIN_DOMAIN = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")


def reply_domain(value: str) -> str:
    """MAIL_REPLY_DOMAIN as a lowercase plain domain, or "" (and one warning) when it isn't one
    (#1717). Only letters, digits, dots and hyphens are kept out of caution: the value ends up in
    a mail header, so anything else (a space, "<", a newline) is refused, never cleaned."""
    domain = value.strip().lower()
    if not domain:
        return ""
    if len(domain) > 253 or not _PLAIN_DOMAIN.fullmatch(domain):
        log.warning("MAIL_REPLY_DOMAIN is not a plain domain name, so it is ignored.")
        return ""
    return domain


def reply_address(sender: str, ticket_id: int, secret: bytes, domain: str = "") -> str | None:
    """The Reply-To for a ticket's emails, made from the support sender (`info@x` becomes
    `info+ticket-12-<token>@x`). `domain`, when given, replaces x: the bare domain's mail goes
    to a person's inbox through the host's email routing, so replies need a subdomain that only
    the mail service receives (#1717). None when there is no usable sender or secret."""
    address = parseaddr(sender)[1]
    local, at, own = address.partition("@")
    if not (local and at and own and secret) or "+" in local:
        return None
    domain = domain or own
    return f"{local}+ticket-{ticket_id}-{ticket_token(secret, ticket_id)}@{domain}"


def ticket_of(addresses: Iterable[str], secret: bytes) -> int | None:
    """The ticket a message was sent to, from its To addresses, when the token is right. The
    domain is not checked: the token is what proves the ticket, so a reply to the old address
    (before MAIL_REPLY_DOMAIN was set) still works."""
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
    r"|Want to talk to a person instead\?|Want a person instead\?|Didn't help\? Reply"
    # The line that opens the conversation DMbot's emails carry (#2192).
    r"|Earlier in this conversation:\s*$)",
    re.IGNORECASE,
)


def _squash(text: str) -> str:
    """Words only, single-spaced, so a re-wrapped copy of an email still matches the original."""
    return " ".join(text.split())


def _long_enough(text: str) -> bool:
    """Unlikely to be the same by chance: at least 25 characters and 4 words. A short common
    opening or answer ("Hi,", "Yes.", "Thanks!") is as likely to be the person's own."""
    return len(text) >= 25 and len(text.split()) >= 4


def _is_sent_back(lines: Sequence[str], sent: Sequence[str]) -> bool:
    """Whether the lines from here on are a copy of one of the emails DMbot or the team sent.
    Whole lines only: from this line, lines are joined (single-spaced, so a copy the mail program
    re-wrapped still matches) while the join is still the start of the email, and it is a copy
    once the join reaches the email's first 80 characters (or all of a shorter email), and those
    are long enough to prove it (`_long_enough`). A line with the person's own words after the
    email's text is never part of a copy, so "Please restart it. Did that, still red." is theirs.
    Known limit: a person who retypes a whole stored team reply of 25 to 80 characters on a line
    of its own, with their words on the next line, is cut too; that can't be told from a copy."""
    for email in sent:
        whole = _squash(email)
        target = whole[:80]
        if not _long_enough(target):
            continue
        joined = ""
        for line in lines:
            words = _squash(line)
            if not words:
                continue
            joined = f"{joined} {words}".strip()
            if not whole.startswith(joined):
                break
            if len(joined) >= len(target):
                return True
    return False


def fresh_words(text: str, limit: int = 4000, sent: Sequence[str] = ()) -> str:
    """What the person wrote, without the email they replied to: cut at the first quoted line or
    "On ... wrote:" line, tidied and shortened. `sent` are the emails they may be answering
    (DMbot's or the team's newest words): a mail program that adds no ">" sends those back too,
    and they must not be kept as the person's. New words sit above any quote, so the first
    non-empty line is never cut as a copy. Empty when nothing is left."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list[str] = []
    for number, line in enumerate(lines):
        if _QUOTE_START.match(line) or (kept and _is_sent_back(lines[number:], sent)):
            break
        if line.strip():
            kept.append(line.rstrip())
        elif kept:
            kept.append("")
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
