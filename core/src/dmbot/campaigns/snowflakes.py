"""Discord ids in backup files are decimal strings (#4060).

A Discord id (a snowflake) is about 19 digits, and every real one is above 2^53. A JSON number
that big is exact in Python but not in a browser, `jq` or any other tool built on doubles: the
last digits come back changed. So a backup writes any number above 2^53 - 1 as a string, in the
campaign file and in every transcript part. Small numbers (times, counts, line numbers) stay
numbers.

Reading is the other way, and only where the key says an id lives. A string of digits above
2^53 - 1 under such a key is the number it is (the only strings a backup writes); a number (a file
from before this change) stays one. Under a few keys that only ever hold a Discord id (`user_id`,
`guild`, `speakers`, ...) a string of 1 to 20 digits is accepted too. A string anywhere else is
left alone, so a name, a line of speech or a text id (a payment provider's, an entry's: 32 hex
characters) that is all digits is never turned into a number. A key that says an id but holds
something else (`"12a"`) stays as it is and the section's own check refuses the whole backup, as
for any damaged file.
"""

from __future__ import annotations

import re
from typing import Any

JS_SAFE = 2**53 - 1  # the largest integer a double holds exactly
ID_DIGITS_MAX = 20  # a 64-bit id is at most 20 digits

# The keys (database column names in the parts and the memory section; the sections' own names for
# the rest) whose values are Discord ids that the sections check as numbers. The `dms` and
# `confirmations` sections already wrote their ids as strings (`user`) and check them so: not here.
# `test_backup_ids` walks a full backup and fails on any number above JS_SAFE, so a field named
# some other way cannot slip through.
ONLY_IDS = re.compile(
    r"^(user_id|guild|speakers|speaker_ids|players|roles|owner_user_id|played_by"
    r"|imported_by|dm_user_ids|dm_accounts|channel_id|message_id)$|_user_id$|_channel_id$"
)
ID_KEY = re.compile(
    r"(_id|_ids|_by|_accounts|_users)$"
    r"|^(guild|speakers|speaker_ids|players|roles|channels|owner|owners)$"
)


def write_ids(value: Any) -> Any:
    """The value with every integer above JS_SAFE (or below its negative) written as a string."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > JS_SAFE else value
    if isinstance(value, dict):
        return {k: write_ids(v) for k, v in value.items()}
    if isinstance(value, list):
        return [write_ids(v) for v in value]
    return value


def _id_text(value: Any, strict: bool) -> Any:
    """A string of digits as the number it is, when the key allows it; anything else as it came.
    `strict`: the key only ever holds a Discord id, so any 1 to 20 digits count."""
    is_digits = isinstance(value, str) and 1 <= len(value) <= ID_DIGITS_MAX and value.isascii()
    if is_digits and value.isdigit() and (strict or int(value) > JS_SAFE):
        return int(value)
    return value


def read_ids(value: Any, *, under: str = "") -> Any:
    """The value with the ids written as strings put back to numbers: only under a key that names
    an id (a list under such a key too). `under`: that key, "" outside one. Nothing else changes."""
    if isinstance(value, dict):
        return {
            k: read_ids(v, under=str(k) if ID_KEY.search(str(k)) else "") for k, v in value.items()
        }
    if isinstance(value, list):
        return [read_ids(v, under=under) for v in value]
    return _id_text(value, bool(ONLY_IDS.search(under))) if under else value
