"""Discord ids in backup files are decimal strings (#4060).

A Discord id (a snowflake) is about 19 digits, and every real one is above 2^53. A JSON number
that big is exact in Python but not in a browser, `jq` or any other tool built on doubles: the
last digits come back changed. So a backup writes any number above 2^53 - 1 as a string, in the
campaign file and in every transcript part. Small numbers (times, counts, line numbers) stay
numbers.

Reading is the other way, and only under the keys that hold a Discord id (`ID_KEYS`, a written
list): a string of 1 to 20 digits there is the number it is, and a number (a file from before this
change) stays one. Nowhere else is a string touched, so a name, a line of speech or a text id (an
entry's, a payment provider's) that is all digits is never turned into a number. A key that holds an
id but something else (`"12a"`) stays as it is, and the section's own check or the database refuses
the whole backup, as for any damaged file. The conversion applies only to the sections and parts
whose rows are the database's own columns; the other sections write and check their ids as strings
themselves.
"""

from __future__ import annotations

from typing import Any

JS_SAFE = 2**53 - 1  # the largest integer a double holds exactly
ID_DIGITS_MAX = 20  # a 64-bit id is at most 20 digits
INT64_MAX = 2**63 - 1

# The keys whose values are Discord ids that the checks read as numbers: the BIGINT id columns of
# the transcript parts' tables and of the memory section (and the part's own `guild`), by name.
# An explicit list, not a pattern: a text column that happens to end `_id` or `_by` (an entry's id,
# a payment provider's) must never be turned into a number. `test_backup_ids` checks the list
# against the catalog, so a new id column cannot be forgotten.
ID_KEYS = frozenset(
    {
        "user_id", "guild", "speakers", "speaker_ids", "played_by", "imported_by", "decided_by",
        "topic_by", "player_id",
    }
)  # fmt: skip
# (BIGINT columns of those tables that hold no Discord id: counters and scope.)
NOT_DISCORD_IDS = frozenset({"guild_id", "line_id"})


# The sections of the campaign file whose rows are the database's columns as they are, so an id in
# them is a number that this module turns into a string and back. Every other section already writes
# its ids as strings and checks them as strings (the `dms`, `confirmations`, `recap_rights`
# sections and so on): converting those would break them. The transcript parts are such rows too.
NUMERIC_SECTIONS = frozenset({"memory"})


def write_sections(sections: dict[str, Any]) -> dict[str, Any]:
    """The campaign file's sections with the ids of the numeric ones written as strings."""
    return {k: write_ids(v) if k in NUMERIC_SECTIONS else v for k, v in sections.items()}


def read_sections(data: Any) -> Any:
    """A campaign file with the ids of its numeric sections put back to numbers. Anything that is
    not a file's shape is returned as it came, for the file's own checks to refuse."""
    if not isinstance(data, dict) or not isinstance(data.get("sections"), dict):
        return data
    sections = data["sections"]
    return {
        **data,
        "sections": {k: read_ids(v) if k in NUMERIC_SECTIONS else v for k, v in sections.items()},
    }


def write_ids(value: Any, *, under: bool = False) -> Any:
    """The value with every integer above JS_SAFE (or below its negative) written as a string,
    where a key names an id (a list under such a key too): exactly where `read_ids` puts it back,
    so a file always restores. A big number under any other key stays a number, and `unsafe_keys`
    names it so the writer can say so."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        # (only what the reader can put back: a positive id that fits 64 bits)
        return str(value) if under and JS_SAFE < value <= INT64_MAX else value
    if isinstance(value, dict):
        return {k: write_ids(v, under=str(k) in ID_KEYS) for k, v in value.items()}
    if isinstance(value, list):
        return [write_ids(v, under=under) for v in value]
    return value


def stringify_unsafe(value: Any) -> Any:
    """The value with every integer above JS_SAFE written as a string, under any key: for what the
    website's API sends a browser, where a number that big loses its last digits (#4060). Nothing
    else changes."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value) if abs(value) > JS_SAFE else value
    if isinstance(value, dict):
        return {k: stringify_unsafe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [stringify_unsafe(v) for v in value]
    return value


def unsafe_keys(value: Any, key: str = "") -> set[str]:
    """The keys that still hold an integer above JS_SAFE (for the log: names only, never values)."""
    if isinstance(value, bool):
        return set()
    if isinstance(value, int):
        return {key} if abs(value) > JS_SAFE else set()
    if isinstance(value, dict):
        return {k for name, v in value.items() for k in unsafe_keys(v, str(name))}
    if isinstance(value, list):
        return {k for v in value for k in unsafe_keys(v, key)}
    return set()


def _id_text(value: Any) -> Any:
    """A string of 1 to 20 digits as the number it is; anything else as it came."""
    is_digits = isinstance(value, str) and 1 <= len(value) <= ID_DIGITS_MAX and value.isascii()
    if is_digits and value.isdigit():
        return int(value)
    return value


def read_ids(value: Any, *, under: bool = False) -> Any:
    """The value with the ids written as strings put back to numbers: only under a key that names
    an id (a list under such a key too). `under`: inside such a key. Nothing else changes."""
    if isinstance(value, dict):
        return {k: read_ids(v, under=str(k) in ID_KEYS) for k, v in value.items()}
    if isinstance(value, list):
        return [read_ids(v, under=under) for v in value]
    return _id_text(value) if under else value
