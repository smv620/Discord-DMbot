"""What made each waiting name a name, on the website's name card (#5144, #5133 slice 3c): how it
was heard, DMbot's guess, and the line that made it a name, from the door `dmbot_recap_name_found`
(migration 0616), in the words and order Discord's card uses (`memory/name_words.py`).

Every waiting name gets `guess` (#5218): DMbot's best guess at what it is, a kind key, or null when
there is none (the scan's `concept` fallback means "no idea") and for a player character (who
plays them is asked in Discord). A name with no `name_found` row has no `found` key. The line's
words come from the
door only for a speaker who agrees now; one who no longer does is left out with nothing in its
place. Nothing here is kept or logged.
"""

from __future__ import annotations

from typing import Any

from dmbot.memory.models import PLAYER_CHARACTER, name_key
from dmbot.memory.name_words import card_guess, heard_line, heard_times, surest
from dmbot.memory.places import LINE_CHARS, cut, find_in, trimmed

SOMEONE = "Someone"  # a speaker whose name was not kept (as the place windows)


def _line(raw: dict[str, Any] | None, spellings: list[str]) -> dict[str, Any] | None:
    if not raw or not raw.get("agreed"):
        return None
    said = " ".join(str(raw.get("text", "")).split())
    if not said:
        return None
    spot = find_in(said, spellings)
    if spot:
        before, heard, after = cut(said, spot[0], spot[1], LINE_CHARS)
        said = f"{before}{heard}{after}"
    else:
        said = trimmed(said, LINE_CHARS)
    at = max(0, (int(raw.get("ms") or 0) - int(raw.get("startedAt") or 0) * 1000) // 1000)
    return {"speaker": str(raw.get("name") or SOMEONE), "at": at, "text": said}


def guess_of(raw: dict[str, Any]) -> str | None:
    """The kind menu's starting point: `guess_kind` (the rule Discord's card uses too), null when
    it has none and for a player character."""
    kind = card_guess(str(raw.get("source", "")), str(raw.get("type", "")), raw.get("role"))
    return None if kind in ("", PLAYER_CHARACTER) else kind


def attach(waiting: list[dict[str, Any]], found: dict[str, Any]) -> None:
    """Give each waiting name that has a found row its `found` key, and order the list as Discord
    does: introduced or typed first, then the most heard, then as made; names with no found row
    keep their order after them (their made-at is not sent)."""
    ranked: list[tuple[tuple[int, int, int, str], dict[str, Any]]] = []
    for card in waiting:
        raw = found.get(str(card.get("id")))
        if not isinstance(raw, dict):
            card["guess"] = None
            continue
        card["guess"] = guess_of(raw)
        if not raw.get("how"):
            continue  # (a waiting name with no found row: the guess only)
        name = str(card.get("name", ""))
        own = name_key(name)
        also = [str(a["text"]) for a in raw.get("also", []) if a.get("key") != own]
        times = heard_times(str(card.get("note", "")), int(raw.get("times") or 0))
        typed = int(raw.get("typed") or 0)
        written = raw.get("source") == "background"
        card["found"] = {
            "how": str(raw["how"]),
            "times": times,
            "typed": min(typed, times),
            "also": also,
            "heard": heard_line(times, typed, also, written=written),
            "guess": card["guess"],
            "line": _line(raw.get("line"), [name, *also]),
        }
        ranked.append((surest(str(raw["how"]), times, int(raw.get("createdAt") or 0), name), card))
    if not ranked:
        return
    ranked.sort(key=lambda r: r[0])
    first = {id(card) for _k, card in ranked}
    waiting[:] = [card for _k, card in ranked] + [c for c in waiting if id(c) not in first]
