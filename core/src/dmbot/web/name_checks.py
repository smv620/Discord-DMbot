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
from dmbot.names import not_names
from dmbot.recaps import name_answers
from dmbot.web import recaps
from dmbot.web.recaps import RecapError
from dmbot.web.sessions import Session

ACTIONS = ("confirm", "same", "respell", "not_a_name", "explain")
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
    if got is None:
        raise RecapError("not_found")
    # Suggestions made before pronouns were kept out ("We", "I'll") are still stored: they are
    # not shown (#2358), by the same rule as Discord's lists (`hidden_old_pronoun`): only a
    # pronoun the database marks as made before the gate. The marker is not passed on.
    waiting = [
        {k: v for k, v in n.items() if k != "old"}
        for n in got["names"]
        if not (n.get("old") and not_names.is_not_name(str(n.get("name", ""))))
    ]
    return {"names": waiting, "known": got["known"], "kinds": name_answers.kinds()}


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
