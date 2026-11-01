"""The words the website sends to ask "Who knows this?" of a connection or an other name
(#2999, #2935 D2): `{who: "dm" | "some" | "everyone", characters: [ids]}` as the one string the
queued name action carries in its `text`. Pure; the door and the bot check it again."""

from __future__ import annotations

import re

from dmbot.web.recaps import RecapError

CHARACTERS_MAX = 50  # what the action's text can carry (the door's pattern allows the same)
_ID = re.compile(r"^[0-9a-f]{32}$")


def who_text(body: dict[str, object]) -> str:
    """`dm`, `everyone`, or `some` and then the characters' ids, space apart. A pick of "some"
    with no character, a repeat, or an id that is not shaped like one is refused here, as the door
    will."""
    who = body.get("who")
    characters = body.get("characters")
    if who in ("dm", "everyone"):
        if characters not in (None, []):
            raise RecapError("bad_target")
        return str(who)
    if who != "some":
        raise RecapError("bad_action")
    if not isinstance(characters, list) or not 1 <= len(characters) <= CHARACTERS_MAX:
        raise RecapError("bad_target")
    if not all(isinstance(c, str) and _ID.match(c) for c in characters):
        raise RecapError("bad_target")
    if len(set(characters)) != len(characters):
        raise RecapError("bad_target")
    return "some " + " ".join(characters)
