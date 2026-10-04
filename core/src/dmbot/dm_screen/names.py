"""Names of the channels DMbot makes (docs/PLAN.md, "Channel structure"). Pure, no Discord.

Discord lowercases text channel names and turns spaces into dashes, so these build names
the way Discord shows them. For the campaign "Rime of the Frostmaiden":

- DM screen, full name:   `dmb-dm-screen-rime-of-the-frostmaiden`
- sub-channels, short:    `dmb-time-rmfthfrstmdn`, `dmb-npcs-rmfthfrstmdn`, …

Clashes between campaigns in one server get a number in front of both names, starting
at 2 ("Frozens Cake" after "Frozen Sick": `dmb-dm-screen-2frozens-cake`, `dmb-time-2frznsck`).
The number is chosen once and stored on the campaign, so names never shift later.
"""

from __future__ import annotations

from collections.abc import Iterable

PREFIX = "dmb"
SCREEN_PREFIX = f"{PREFIX}-dm-screen"
LEGACY_SCREEN_PREFIX = "dm-screen"  # DM screens made before the dmb- prefix (PR #75)
CHANNEL_NAME_MAX = 100  # Discord's limit
SHORT_MAX = 15
VOWELS = frozenset("aeiou")  # y is not a vowel
FALLBACK = "campaign"  # when a name has no usable characters (only emoji, say)


def slug(campaign_name: str) -> str:
    """The full name as Discord would write it: lowercase words joined by dashes."""
    text = "".join(c if c.isalnum() else "-" for c in campaign_name.casefold())
    return "-".join(part for part in text.split("-") if part) or FALLBACK


def short_name(campaign_name: str) -> str:
    """The condensed name for sub-channels.

    Keep only a-z and 0-9. If more than half of the letters are vowels, keep them and
    cut to 15 characters; otherwise drop the vowels and cut to 15. Digits don't count
    as vowels or consonants.
    """
    text = "".join(c for c in campaign_name.lower() if "a" <= c <= "z" or "0" <= c <= "9")
    if not text:
        return FALLBACK
    letters = [c for c in text if c.isalpha()]
    vowels = sum(c in VOWELS for c in letters)
    if vowels * 2 > len(letters):
        return text[:SHORT_MAX]
    # Never empty: a name of only vowels took the branch above.
    return "".join(c for c in text if c not in VOWELS)[:SHORT_MAX]


def numbered(base: str, number: int) -> str:
    """`base`, with the clash number in front when it is 2 or more."""
    return f"{number}{base}" if number >= 2 else base


def _fit(name: str) -> str:
    return name[:CHANNEL_NAME_MAX].rstrip("-")


def screen_channel_name(campaign_name: str, number: int = 1) -> str:
    return _fit(f"{SCREEN_PREFIX}-{numbered(slug(campaign_name), number)}")


def sub_channel_name(kind: str, campaign_name: str, number: int = 1) -> str:
    """`sub_channel_name("time", "Rime of the Frostmaiden")` is `dmb-time-rmfthfrstmdn`."""
    return _fit(f"{PREFIX}-{kind}-{numbered(short_name(campaign_name), number)}")


def _rendered(campaign_name: str, number: int) -> tuple[str, str]:
    """The names Discord will show: the DM screen's, and the sub-channels' short part."""
    return (
        screen_channel_name(campaign_name, number),
        numbered(short_name(campaign_name), number),
    )


def clashes(campaign_name: str, number: int, others: Iterable[tuple[str, int | None]]) -> bool:
    """Whether these names, with this number, are already shown for another campaign."""
    screen, short = _rendered(campaign_name, number)
    for name, other_number in others:
        if other_number is None:
            continue  # picks its number later, avoiding ours
        other_screen, other_short = _rendered(name, other_number)
        if screen == other_screen or short == other_short:
            return True
    return False


def pick_channel_number(campaign_name: str, others: Iterable[tuple[str, int | None]]) -> int:
    """The lowest number (1 = no prefix) that gives names no other campaign shows.

    `others` are the server's other campaigns as (name, stored number or None). Names
    are compared as Discord will show them, numbers and the 100-character cut included,
    so "2 Frozens Cake" can't land on the `2frznsck` that "Frozens Cake" already shows.
    Campaigns without a number yet don't block anything: they pick theirs later.
    """
    taken = list(others)
    number = 1
    while clashes(campaign_name, number, taken):
        number += 1
    return number


def is_screen_name(name: str) -> bool:
    """Whether a channel is named like a DM screen DMbot makes (new or old style).

    DMbot only changes the permissions of such channels, never of an ordinary channel.
    """
    return any(
        name == prefix or name.startswith(f"{prefix}-")
        for prefix in (SCREEN_PREFIX, LEGACY_SCREEN_PREFIX)
    )
