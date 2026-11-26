"""The words the website sends to ask "Who knows this?" of a connection or an other name
(#2999, #2935 D2): `{who: "dm" | "some" | "everyone" | "common", characters: [ids]}` as the one
string the queued name action carries in its `text`. Pure; the door and the bot check it again."""

from __future__ import annotations

import re

from dmbot.web.recaps import RecapError

CHARACTERS_MAX = 50  # what the action's text can carry (the door's pattern allows the same)
_ID = re.compile(r"^[0-9a-f]{32}$")


SHOWN_MAX = 50  # the characters one list shows (the door allows as many)


def _shown_part(body: dict[str, object]) -> str:
    """` shown <ids>`: every character the page's list showed, ticked or not (#4968), or "" when the
    page sent none (an older page): the bot then neither confirms nor removes a DMbot guess."""
    shown = body.get("shown")
    if shown in (None, []):
        return ""
    if not isinstance(shown, list) or len(shown) > SHOWN_MAX:
        raise RecapError("bad_target")
    if not all(isinstance(c, str) and _ID.match(c) for c in shown) or len(set(shown)) != len(shown):
        raise RecapError("bad_target")
    return " shown " + " ".join(shown)


def who_text(body: dict[str, object]) -> str:
    """`dm`, `everyone`, `common`, or `some` and then the characters' ids, space apart, and then,
    when the page sends them, ` shown` and the ids its list showed. A pick of "some" with no
    character, a repeat, or an id that is not shaped like one is refused here, as the door will."""
    who = body.get("who")
    characters = body.get("characters")
    if who in ("dm", "everyone", "common"):  # (🌍 Common knowledge, #4559: no characters)
        if characters not in (None, []):
            raise RecapError("bad_target")
        return str(who) + _shown_part(body)
    if who != "some":
        raise RecapError("bad_action")
    if not isinstance(characters, list) or not 1 <= len(characters) <= CHARACTERS_MAX:
        raise RecapError("bad_target")
    if not all(isinstance(c, str) and _ID.match(c) for c in characters):
        raise RecapError("bad_target")
    if len(set(characters)) != len(characters):
        raise RecapError("bad_target")
    return "some " + " ".join(characters) + _shown_part(body)
