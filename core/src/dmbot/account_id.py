"""The DMbot ID: a Discord ID written in base 36 (`0-9A-Z`), fixed for the life of an account
(docs/PLAN.md, "Accounts").

It is the Stripe invoice prefix, which allows only capital letters and digits, up to 12
characters: base 36 fits a Discord ID (about 1.5 x 10^18) in 12, base 10 would not leave
room to spare. Pure functions; the database makes the same string for every account with
`dmbot_base36` (migration 0063) and a test holds the two together.
"""

from __future__ import annotations

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
LENGTH_MAX = 12  # Stripe's invoice prefix limit
LIMIT = 36**LENGTH_MAX - 1  # the biggest ID that still fits


def to_dmbot_id(discord_id: int) -> str:
    """A Discord ID in base 36. Raises ValueError for an ID that is not positive or does not
    fit in 12 characters."""
    if isinstance(discord_id, bool) or not isinstance(discord_id, int) or discord_id <= 0:
        raise ValueError("A Discord ID is a positive whole number.")
    if discord_id > LIMIT:
        raise ValueError("That ID is too big for a DMbot ID.")
    out = ""
    n = discord_id
    while n:
        n, digit = divmod(n, 36)
        out = DIGITS[digit] + out
    return out


def from_dmbot_id(text: str) -> int:
    """The Discord ID a DMbot ID stands for. Raises ValueError for anything that is not a
    DMbot ID: capital letters and digits only, 1 to 12 characters, no leading zero (the ID
    is the same number written one way only)."""
    if (
        not 1 <= len(text) <= LENGTH_MAX
        or text[0] == "0"
        or any(c not in DIGITS for c in text)  # also rejects lower case, spaces, signs
    ):
        raise ValueError("That is not a DMbot ID.")
    return int(text, 36)
