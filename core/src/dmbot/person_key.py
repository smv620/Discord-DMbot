"""The keyed one-way hash DMbot keeps of a Discord ID after an account is deleted (#4145).

A person who deletes their account must not get a second free trial, and an unsubscribe must still
be honoured when they come back. Both need to recognise the same person later, but not to hold their
Discord ID. So the records keep `person_key(id)`: an HMAC-SHA256 of the ID under a server secret
(`DMBOT_PERSON_KEY`). A plain hash would not do: Discord IDs are guessable, so an unkeyed hash
could be reversed by trying every ID. The key is computed here, in Python, and handed to the
database as an argument: it never reaches SQL, a log line or a migration.

Losing or changing the secret loses recognition of every earlier key (a returning person looks new),
which is why `docs/DEPLOY.md` says to set it once and never change it.
"""

from __future__ import annotations

import hashlib
import hmac
import re

KEY_MIN = 32  # characters of secret; the same floor as the sign-in secret
INT64_MAX = 2**63 - 1
_HEX64 = re.compile(r"[0-9a-f]{64}")


class PersonKeyMissing(RuntimeError):
    """The secret is not set (or too short), so nothing is hashed: failing closed is safer than
    storing an unkeyed value that could be reversed."""


class PersonKeys:
    """Hashes Discord IDs under one secret. Built once from the settings."""

    __slots__ = ("_secret",)

    def __repr__(self) -> str:
        return "PersonKeys(...)"  # (never the secret, even in a traceback or a log line)

    def __init__(self, secret: str) -> None:
        if len(secret) < KEY_MIN:
            raise PersonKeyMissing(
                f"DMBOT_PERSON_KEY must be set to at least {KEY_MIN} characters "
                "(scripts/set-key DMBOT_PERSON_KEY)"
            )
        self._secret = secret.encode()

    def of(self, user_id: int) -> str:
        """64 hex characters standing for this Discord ID. The same ID always gives the same key
        under the same secret; another ID or another secret gives another key."""
        if (
            isinstance(user_id, bool)
            or not isinstance(user_id, int)
            or not 0 < user_id <= INT64_MAX
        ):
            raise ValueError("not a Discord ID")
        return hmac.new(self._secret, str(user_id).encode(), hashlib.sha256).hexdigest()


def valid(key: object) -> bool:
    """Whether this is the shape of a key (64 lowercase hex characters). The doors that take a key
    as an argument check this before they store it, so a malformed value is refused."""
    return isinstance(key, str) and _HEX64.fullmatch(key) is not None


_configured: PersonKeys | None = None  # set once at start-up, like the free users list


def configure(secret: str) -> bool:
    """Set the one secret the running process hashes with; True when it is usable. An unset or
    short secret leaves no key: the records are then written without one and work as before (the
    backfill job needs it, and the log says so at start-up). Called by the bot and by the API."""
    global _configured
    try:
        _configured = PersonKeys(secret)
    except PersonKeyMissing:
        _configured = None
    return _configured is not None


def key_for(user_id: int) -> str | None:
    """The key to store beside this Discord ID, or None when no secret is configured."""
    if _configured is None:
        return None
    try:
        return _configured.of(user_id)
    except ValueError:
        return None  # (not a Discord ID: a read finds nothing, as it always did)


def from_settings(settings: object) -> PersonKeys:
    """The keys for the running settings; raises `PersonKeyMissing` when the secret is not set."""
    return PersonKeys(str(getattr(settings, "person_key", "") or ""))
