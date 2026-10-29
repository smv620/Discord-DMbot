"""The names a recap asks the DM to check, answered on its page (#2113).

The list comes from a database door (`dmbot_recap_names_read`): the recap's waiting suggestions,
the known names one can be said to be, and never a secret name. An answer is queued by another
door (`dmbot_name_answer_add`) for the bot, which applies it through the same MemoryStore paths as
Discord's 📝 Check new names (`recaps.name_answers`), so an answer on either answers both. The
website has no right on the memory tables; both doors check the caller is a DM of the campaign.
The DM's words (a spelling, a note) are held in the answer row for an hour at most and never
logged.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.memory.models import NAME_MAX
from dmbot.memory.name_longer import MAX_OFFERED, longer_names
from dmbot.memory.places import LINE_CHARS, cut, find_in, trimmed
from dmbot.names import not_names
from dmbot.recaps import name_answers
from dmbot.web import recaps
from dmbot.web.recaps import RecapError
from dmbot.web.sessions import Session

ACTIONS = ("confirm", "same", "respell", "not_a_name", "explain", "change_words")
SPELLING_MAX = 80  # as the database's door and MemoryStore.rename_entity
NOTE_MAX = name_answers.NOTE_MAX
ID_LENGTH = 32


def _entity_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != ID_LENGTH
        or not set(value) <= set("0123456789abcdef")
    ):
        raise RecapError("not_found")
    return value


async def read_checks(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The recap's names to check, the known names to pick from, and the kinds. Only a DM of the
    campaign; anyone else gets "not found"."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await recaps._one(conn, ref, session, marks=False)
        cur = await conn.execute("SELECT dmbot_recap_names_read(%s, %s, %s) AS r", (gid, cid, at))
        got = (await cur.fetchone() or {}).get("r")
        if got is not None:
            cur = await conn.execute(
                "SELECT dmbot_recap_name_places(%s, %s, %s) AS r", (gid, cid, at)
            )
            found = (await cur.fetchone() or {}).get("r") or {}
    if got is None:
        raise RecapError("not_found")
    # Suggestions made before pronouns were kept out ("We", "I'll") are still stored: they are
    # not shown (#2358), by the same rule as Discord's lists (`hidden_old_pronoun`): only a
    # pronoun the database marks as made before the gate. The marker is not passed on.
    waiting = [
        _with_places(
            _with_longer_names({k: v for k, v in n.items() if k != "old"}), found.get(n["id"])
        )
        for n in got["names"]
        if not (n.get("old") and not_names.is_not_name(str(n.get("name", ""))))
    ]
    return {"names": waiting, "known": got["known"], "kinds": name_answers.kinds()}


def _with_longer_names(card: dict[str, Any]) -> dict[str, Any]:
    """The likely longer names for one waiting name (#2593), worked out now from the lines that say
    it, which the door gives only for people who agree at this moment (so a speaker who stops being
    recorded drops out at once, and nothing of their words is kept). The lines are not passed on."""
    lines = [str(x) for x in card.pop("lines", [])]
    name = str(card.get("name", ""))
    quotes = []
    for said in lines:
        said = " ".join(said.split())
        found = find_in(said, [name])
        if found:
            before, heard, after = cut(said, found[0], found[1], 120)
            quotes.append(f"{before}**{heard}**{after}")
    card["longerNames"] = longer_names(quotes, name)[:MAX_OFFERED]
    return card


SOMEONE = "Someone"  # as Discord's card: a speaker whose name was not kept


def _with_places(card: dict[str, Any], found: dict[str, Any] | None) -> dict[str, Any]:
    """Where the name was said (#2403): the door's windows made ready for the page. The door only
    marks which lines' speakers agree now; a line of someone who does not is `{"removed": true}`
    with no speaker, time or words, and a window with nothing left to show says so with no lines.
    The words are drawn from this answer only: nothing is kept, cached or logged."""
    if not found or not (found.get("windows") or found.get("more")):
        return card  # nothing said anywhere: the card is as before, and the page shows no places
    name = str(card.get("name", ""))
    places: list[dict[str, Any]] = []
    for window in found.get("windows", []):
        lines: list[dict[str, Any]] = []
        started = int(window.get("startedAt") or 0) * 1000
        for line in window.get("lines", []):
            if not line.get("agreed"):
                lines.append({"removed": True})
                continue
            said = " ".join(str(line.get("text", "")).split())
            spot = find_in(said, [name]) if line.get("hit") else None
            if spot:
                before, heard, after = cut(said, spot[0], spot[1], LINE_CHARS)
                said = f"{before}{heard}{after}"
            else:
                said = trimmed(said, LINE_CHARS)
            lines.append(
                {
                    "speaker": str(line.get("name") or SOMEONE),
                    "at": max(0, (int(line.get("ms") or 0) - started) // 1000),
                    "text": said,
                }
            )
        shown = any(not x.get("removed") for x in lines)
        places.append(
            {"session": int(window["session"]), "available": shown, "lines": lines if shown else []}
        )
    card["places"] = places
    card["morePlaces"] = int(found.get("more", 0))
    return card


async def answer(
    db: Database, session: Session, ref: str, entity_id: object, body: object
) -> dict[str, Any]:
    """Queue one answer for the bot. The words are checked here for the page's sake and again by
    the door and the bot; nothing about the recap changes."""
    ent = _entity_id(entity_id)
    if not isinstance(body, dict):
        raise RecapError("bad_action")
    action = body.get("action")
    if action not in ACTIONS:
        raise RecapError("bad_action")
    kind = _text(body.get("kind")) if action == "confirm" else ""
    if kind and kind not in {k["key"] for k in name_answers.kinds()}:
        raise RecapError("bad_text")  # not one of the kinds the page offers
    target = _known_id(body.get("known")) if action == "same" else ""
    text = ""
    if action in ("respell", "explain"):
        text = _text(body.get("text"))
        if not text or len(text) > (SPELLING_MAX if action == "respell" else NOTE_MAX):
            raise RecapError("bad_text")
    if action == "confirm" and not kind:
        raise RecapError("bad_text")
    if action == "change_words":
        # ✏️ Change the words (#2593): the whole name, and how it was heard when one of the longer
        # names was picked (carried in the door's target). The page's words for each refusal.
        text = _text(body.get("words"))
        target = _text(body.get("heard"))
        if not text:
            raise RecapError("empty")
        if len(text) > NAME_MAX or len(target) > NAME_MAX:
            raise RecapError("too_long")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await recaps._one(conn, ref, session, marks=False)
        cur = await conn.execute(
            "SELECT dmbot_name_answer_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
            (gid, cid, at, ent, action, kind, target, text),
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_found")  # not a DM: nothing is said about the recap
    if got["status"] != "ok":
        raise RecapError(got["status"])
    return {"answerId": got["id"]}


def _known_id(value: object) -> str:
    try:
        return _entity_id(value)
    except RecapError:
        raise RecapError("bad_target") from None


def _text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or any(
        ord(c) < 32 or ord(c) == 127 for c in value if c not in " \t\n"
    ):
        raise RecapError("bad_text")
    return " ".join(value.split())
