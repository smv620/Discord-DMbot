"""The admin password hash's .env form (#772), apart from the hashing itself, so reading
the settings needs neither argon2 nor aiohttp."""

import base64
import binascii
import re

# What argon2-cffi writes: version, memory, time and threads, then salt and hash (base64
# without padding). Checked at start so a damaged line in .env stops the API with words,
# rather than refusing every sign-in later.
_ARGON2ID = re.compile(r"\$argon2id\$v=19\$m=\d+,t=\d+,p=\d+\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+")


def encode_hash(argon2_hash: str) -> str:
    """The form kept in .env: base64, because an argon2 hash is full of `$` signs, which
    Docker Compose would try to expand when it reads the file."""
    return base64.urlsafe_b64encode(argon2_hash.encode("ascii")).decode("ascii")


def decode_hash(encoded: str) -> str | None:
    """The argon2id hash from its .env form, or None if it isn't one."""
    try:
        raw = base64.urlsafe_b64decode(encoded.encode("ascii")).decode("ascii")
    except (ValueError, UnicodeError, binascii.Error):
        return None
    return raw if _ARGON2ID.fullmatch(raw) else None
