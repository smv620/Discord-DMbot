"""The website's All names page: the list a campaign's DM (or a server manager) browses (#2294).

Discord's name menus hold 25 choices, so the last one sends the DM here for the whole list, with
search, a filter for each kind and pages. The website's database role has no right on the memory
tables, so the list comes from one database door (`dmbot_web_all_names`, schema.py): a DM of the
campaign gets everything, a server manager who is not one gets the same names without what only a DM
sees (the entries the players don't know yet, secret spellings), and anyone else gets "not found".
Never a person at the table.

A campaign is named by `<server>.<campaign>`, as for the recap and transcript pages.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.memory.models import PRONOUNCED
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

PAGE_SIZE = 20  # the same as Discord's Browse pages
SEARCH_MAX = 100  # the longest name; a longer search matches nothing anyway
SORTS = ("name", "newest")
PAGE_LIMIT = 100_000  # far past any campaign: 2 million names

# The kinds as the page lists them, in the order to show when counts tie; Discord's Browse puts the
# biggest first. A kind the campaign added under a core one is shown by its own words.
KIND_LABELS: dict[str, str] = {
    "npc": "NPCs",
    "player_character": "Player characters",
    "character": "Characters",
    "place": "Places",
    "faction": "Groups",
    "creature": "Creatures",
    "item": "Items",
    "deity": "Gods",
    "event": "Events",
    "clue": "Clues",
    "people": "Peoples",
    "thread": "Story threads",
    "quest": "Quests",
    "mystery": "Mysteries",
    "hook": "Hooks",
    "goal": "Goals",
    "promise": "Promises",
    "deed": "Deeds",
    "concept": "Other",
}


def _label(key: str) -> str:
    return KIND_LABELS.get(key) or key.replace("_", " ").capitalize()


def kinds_from(counts: dict[str, int]) -> list[dict[str, Any]]:
    """Every kind with its count, the biggest first (ties by label), as Discord's Browse does."""
    rows = [{"key": k, "label": _label(k), "count": int(n)} for k, n in counts.items()]
    return sorted(rows, key=lambda r: (-r["count"], str(r["label"]).casefold(), str(r["key"])))


async def read_page(
    db: Database,
    session: Session,
    ref: str,
    *,
    q: str = "",
    kind: str = "",
    sort: str = "name",
    page: int = 1,
    only: str | None = None,
) -> dict[str, Any]:
    """One page of the campaign's confirmed names. "not_found" for anyone who may not see the
    list (or another campaign's ref); "bad_page" for a page past the end."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if sort not in SORTS:
        raise RecapError("bad_sort")
    if not 1 <= page <= PAGE_LIMIT:  # (also keeps the database's arithmetic well inside range)
        raise RecapError("bad_page")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_all_names(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
            (
                guild_id,
                campaign_id,
                q.strip()[:SEARCH_MAX],
                kind.strip()[:40],
                sort == "newest",
                page,
                PAGE_SIZE,
                only,
            ),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    total = int(got["total"])
    pages = max(1, -(-total // PAGE_SIZE))
    if page > pages:
        raise RecapError("bad_page")
    return {
        "campaign": got["campaign"],
        "canEdit": bool(got["canEdit"]),
        "names": got["names"],
        "kinds": kinds_from(got["kinds"]),
        "total": total,
        "page": page,
        "pages": pages,
        "pageSize": PAGE_SIZE,
    }


# ---- the DM's actions: queued for the bot, read back by polling ----------------------------------

NAME_MAX = 100  # a name or another name: the same limit as the bot's
KIND_PATTERN = re.compile(r"^[a-z][a-z _-]{0,39}$")
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
ACTIONS = ("spell", "kind", "add-other", "remove-other", "secret", "hide", "merge", "remove")


def _text(value: object) -> str:
    """A name the way the bot keeps it: tidy spaces, printable, within the limit."""
    if not isinstance(value, str):
        raise RecapError("bad_name")
    clean = " ".join(value.split())
    if not clean or not clean.isprintable():
        raise RecapError("bad_name")
    if len(clean) > NAME_MAX:
        raise RecapError("too_long")
    return clean


def _arguments(body: object) -> tuple[str, str, str, str, bool]:
    """(action, kind, target, text, flag) for the database door, or the refusal word."""
    if not isinstance(body, dict):
        raise RecapError("bad_action")
    action = body.get("action")
    if action not in ACTIONS:
        raise RecapError("bad_action")
    kind = target = text = ""
    flag = False
    if action == "spell":
        text = _text(body.get("name"))
        if PRONOUNCED.search(text):  # how to say it is not how to spell it (#2332)
            raise RecapError("bad_name")
    elif action == "kind":
        kind = str(body.get("kind") or "")
        if not KIND_PATTERN.match(kind):
            raise RecapError("bad_kind")
    elif action in ("add-other", "remove-other"):
        text = _text(body.get("other"))
    elif action == "secret":  # one other name; the main name can't be secret (as on the card)
        if not isinstance(body.get("secret"), bool):
            raise RecapError("bad_action")
        flag = bool(body["secret"])
        text = _text(body.get("other"))  # (missing: "bad_name")
    elif action == "hide":  # "Players don't know this one yet", the whole entry
        if not isinstance(body.get("hidden"), bool):
            raise RecapError("bad_action")
        flag = bool(body["hidden"])
    elif action == "merge":
        target = str(body.get("into") or "")
        if not ID_PATTERN.match(target):
            raise RecapError("bad_target")
    elif action == "remove":
        flag = body.get("confirm") is True
        if not flag:
            raise RecapError("needs_confirm")
    return str(action), kind, target, text, flag


async def queue_action(
    db: Database, session: Session, ref: str, entity_id: str, body: object
) -> str:
    """Queue one action of the signed-in DM for the bot and return its id. The words are checked
    here for the page's sake and again by the door and the bot. A server the person is not in, or
    a name that is not in this campaign, is "not found"; a member who is not a DM of it is "not
    allowed"."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not ID_PATTERN.match(entity_id):
        raise RecapError("not_found")
    action, kind, target, text, flag = _arguments(body)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, entity_id, action, kind, target, text, flag),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
        # The door's own "bad_text" is a word the page can't use: a name it refused.
        raise RecapError("bad_name" if got["status"] == "bad_text" else str(got["status"]))
    return str(got["id"])


async def read_action(db: Database, session: Session, ref: str, action_id: str) -> dict[str, Any]:
    """Where one of the caller's own actions stands: `state` (waiting, working, done, failed), the
    `error` word when it failed, the changed (or surviving) name's `row`, and `removed`."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not ID_PATTERN.match(action_id):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_name_action_read(%s, %s, %s) AS r", (guild_id, campaign_id, action_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    row = None
    if got["state"] == "done" and got["resultId"]:
        shown = await read_page(db, session, ref, only=got["resultId"])
        row = shown["names"][0] if shown["names"] else None
    return {
        "state": got["state"],
        "row": row,
        "removed": got["state"] == "done" and got["action"] == "remove",
        "error": got["error"],
    }
