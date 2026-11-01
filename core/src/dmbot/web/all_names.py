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
from dmbot.memory import unnamed as unnamed_mod
from dmbot.memory.background import LABELS
from dmbot.memory.models import MERGE_MANY_MAX, PRONOUNCED
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session
from dmbot.web.who_knows import who_text

PAGE_SIZE = 50  # the page's usual size (it was Discord's 20 until the compact list, 2026-10-27)
PAGE_SIZES = (20, 50, 200, 500)  # what the page's "Names per page" offers; nothing else is asked
KINDS_MAX = 40  # most kinds ticked at once (there are fewer than this)
SEARCH_MAX = 100  # the longest name; a longer search matches nothing anyway
SORTS = ("name", "newest")
PAGE_LIMIT = 100_000  # far past any campaign: 2 million names

# The kinds Discord names in its own words ("people", "groups", "gods", "ideas": the plain words of
# `memory/background.LABELS`, as in 📝 Check new names) are shown in those words, so the website and
# Discord agree (#2376). The rest are the website's own.
KIND_LABELS: dict[str, str] = {
    "not_named": unnamed_mod.GROUP,  # the people the story never named (#2803), for a DM only
    "player_character": "PCs",
    "character": "Characters",
    "creature": "Creatures",
    "clue": "Clues",
    "people": "Folk and peoples",  # (not "People": that is the NPCs)
    "thread": "Story threads",
    "quest": "Quests",
    "mystery": "Mysteries",
    "hook": "Hooks",
    "goal": "Goals",
    "promise": "Promises",
    "deed": "Deeds",
}


def _label(key: str) -> str:
    if key in LABELS:
        plural = LABELS[key][1]
        return plural[:1].upper() + plural[1:]  # (not capitalize(): it would turn NPCs into Npcs)
    return KIND_LABELS.get(key) or key.replace("_", " ").capitalize()


def kinds_from(counts: dict[str, int]) -> list[dict[str, Any]]:
    """Every kind with its count, the biggest first (ties by label), as Discord's Browse does."""
    rows = [{"key": k, "label": _label(k), "count": int(n)} for k, n in counts.items()]
    # "Not named yet" comes first (it is the DM's to do), the rest by size.
    return sorted(
        rows,
        key=lambda r: (
            r["key"] != "not_named",
            -r["count"],
            str(r["label"]).casefold(),
            str(r["key"]),
        ),
    )


async def read_page(
    db: Database,
    session: Session,
    ref: str,
    *,
    q: str = "",
    kind: str = "",
    sort: str = "name",
    page: int = 1,
    size: int = PAGE_SIZE,
    only: str | None = None,
    unnamed: bool = False,
) -> dict[str, Any]:
    """One page of the campaign's confirmed names, `size` to a page (one of `PAGE_SIZES`); `kind` is
    one kind or several joined with commas. "not_found" for anyone who may not see the list (or
    another campaign's ref); "bad_page" for a page past the end or a size the page never offers."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if sort not in SORTS:
        raise RecapError("bad_sort")
    if size not in PAGE_SIZES:
        raise RecapError("bad_page")
    kinds = [k.strip() for k in kind.split(",") if k.strip()]
    if len(kinds) > KINDS_MAX or any(len(k) > 40 for k in kinds):
        raise RecapError("bad_page")  # (a list no page makes: the same plain refusal)
    if not 1 <= page <= PAGE_LIMIT:  # (also keeps the database's arithmetic well inside range)
        raise RecapError("bad_page")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        # "Not named yet" (#2803) is in the list, the counts and the totals only when the page asks
        # for it, so a DM's numbers are right whatever the page's switch says; the door still lets
        # only a DM of the campaign see them. Local to this transaction, like the guild.
        await conn.execute(
            "SELECT set_config('dmbot.names_unnamed', %s, true)", ("1" if unnamed else "",)
        )
        cur = await conn.execute(
            "SELECT dmbot_web_all_names(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
            (
                guild_id,
                campaign_id,
                q.strip()[:SEARCH_MAX],
                ",".join(kinds),
                sort == "newest",
                page,
                size,
                only,
            ),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    total = int(got["total"])
    pages = max(1, -(-total // size))
    if page > pages:
        raise RecapError("bad_page")
    names = got["names"]
    for row in names:
        _label_unnamed(row)
    if got["canEdit"] and names:
        await _add_said_as(db, session, guild_id, campaign_id, names)
    return {
        "campaign": got["campaign"],
        "canEdit": bool(got["canEdit"]),
        "names": names,
        "kinds": kinds_from(got["kinds"]),
        "total": total,
        "page": page,
        "pages": pages,
        "pageSize": size,
    }


def _label_unnamed(row: dict[str, Any]) -> None:
    """A person the story never named: their name is the label built from the parts with the
    anchor's name as it is now ("Ken's sister (name unknown)"), as Discord builds it; the anchor
    goes to the page as a plain name, and the raw parts stay out of the row. An anchor that is
    itself unnamed is built the same way ("the king's sister")."""
    anchor = row.pop("unnamedAnchor", None)
    if not row.get("unnamed"):
        row["unnamed"] = False
        row.pop("unnamedWhat", None)
        return
    anchor_name = None
    if isinstance(anchor, dict):
        anchor_name = str(anchor["name"]) if anchor.get("name") else None
        if anchor.get("what"):
            anchor_name = unnamed_mod.label(str(anchor["what"]), anchor.get("anchorName"))
    what = str(row.get("unnamedWhat") or "")
    row["name"] = unnamed_mod.label(what, anchor_name)
    row["unnamedAnchor"] = anchor_name


async def _add_said_as(
    db: Database, session: Session, guild_id: int, campaign_id: str, names: list[dict[str, Any]]
) -> None:
    """Give a DM's rows `aliasId` and `saidAs` (#2317): the main name's id, which the said-as
    routes take, and how the narrator says it. A separate door, so the list's own door and its
    cache of secret names stay as they are; a row with no such name gets neither key."""
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_all_names_said(%s, %s, %s) AS r",
            (guild_id, campaign_id, [str(n["id"]) for n in names]),
        )
        found = await cur.fetchone()
    said = {str(r["id"]): r for r in (found["r"] if found and found["r"] else [])}
    for row in names:
        mine = said.get(str(row["id"]))
        if mine is not None:
            row["aliasId"] = mine["aliasId"]
            row["saidAs"] = mine["saidAs"]


# ---- the DM's actions: queued for the bot, read back by polling ----------------------------------

NAME_MAX = 100  # a name or another name: the same limit as the bot's
KIND_PATTERN = re.compile(r"^[a-z][a-z _-]{0,39}$")
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
PLAYER_ID = re.compile(r"^[1-9][0-9]{14,18}$")  # a Discord user ID (a snowflake)
ACTIONS = (
    "spell",
    "kind",
    "add-other",
    "remove-other",
    "secret",
    "hide",
    "merge",
    "merge-many",
    "remove",
    "name",
    "name-join",
    "name-undo",
    "alias-who",  # who knows an other name (#2999): {alias, who, characters}
    "confirm-kinds",  # "Add all" for what an upload found (#3207): {upload, kinds | "all"}
    "confirm-undo",  # takes back one press of it: {answer}
)

KIND_WORD = re.compile(r"^[a-z_]{2,20}$")  # the reader's kind words ("place", "npc"); never "idea"
KINDS_MAX = 8


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
        player = body.get("player")
        if player is not None and player != "":
            # Who plays a player character: a Discord ID, digits only (a name is not one). Asking
            # who an ID is (`player_lookup`) writes nothing; saving it needs the DM's confirmation
            # of the name Discord showed (`confirmed`, sent only after "That's them", #3004).
            # The door and the bot check it again, and the bot asks Discord again before linking.
            if kind not in ("player_character", "player_lookup") or not isinstance(player, str):
                raise RecapError("bad_player")
            text = player.strip()
            if not PLAYER_ID.match(text):
                raise RecapError("bad_player")
            if kind == "player_character":
                if body.get("confirmed") is not True:
                    raise RecapError("bad_player")
                flag = True
        elif kind == "player_lookup":
            raise RecapError("bad_player")
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
    elif action == "merge-many":  # the other names' ids ride in the text, space apart
        others = body.get("others")
        if not isinstance(others, list) or not others or len(others) > MERGE_MANY_MAX:
            raise RecapError("bad_target")
        if not all(isinstance(o, str) and ID_PATTERN.match(o) for o in others):
            raise RecapError("bad_target")
        if len(set(others)) != len(others):
            raise RecapError("bad_target")
        text = " ".join(others)
    elif action == "name":  # a name for a person the story never named (#2803)
        text = _text(body.get("name"))
    elif action == "name-join":  # "yes, the same person": joined into the named one, keeping it
        target = str(body.get("into") or "")
        if not ID_PATTERN.match(target):
            raise RecapError("bad_target")
    elif action == "name-undo":  # undoes a done `name` or `name-join`, named by its answer id
        target = str(body.get("answer") or "")
        if not ID_PATTERN.match(target):
            raise RecapError("bad_target")
    elif action == "alias-who":  # the other name's id, and the answer in the text (#2999)
        target = str(body.get("alias") or "")
        if not ID_PATTERN.match(target):
            raise RecapError("not_found")
        text = who_text(body)
    elif action == "confirm-kinds":  # the upload is the target; the kinds, or all, are the text
        target = str(body.get("upload") or "")
        if not ID_PATTERN.match(target):
            raise RecapError("not_found")
        kinds = body.get("kinds")
        if kinds == "all":
            text = "all"
        else:
            if not isinstance(kinds, list) or not 1 <= len(kinds) <= KINDS_MAX:
                raise RecapError("bad_target")
            if not all(
                isinstance(k, str) and KIND_WORD.match(k) and k not in ("idea", "all")
                for k in kinds
            ):
                raise RecapError("bad_target")
            if len(set(kinds)) != len(kinds):
                raise RecapError("bad_target")
            text = " ".join(kinds)
    elif action == "confirm-undo":  # the answer of the press to take back
        target = str(body.get("answer") or "")
        if not ID_PATTERN.match(target):
            raise RecapError("not_found")
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


async def queue_confirm(db: Database, session: Session, ref: str, body: object) -> str:
    """Queue "Add all" for the names an upload found, or the Undo of one press (#3207), for the
    signed-in DM: the same door and the same answer as any name action, with no name to file it
    under. Only the two actions are taken here."""
    if not isinstance(body, dict) or body.get("action") not in ("confirm-kinds", "confirm-undo"):
        raise RecapError("bad_action")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    action, kind, target, text, flag = _arguments(body)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, None, action, kind, target, text, flag),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
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
    # A name DMbot already knows (#2803): the bot wrote nothing and says which entry it is, by id;
    # the name comes from the list, as a person would see it, never from the answer row.
    other = None
    if got["state"] == "failed" and got["error"] == "known_name" and got["resultId"]:
        shown = await read_page(db, session, ref, only=got["resultId"])
        if shown["names"]:
            other = {"id": shown["names"][0]["id"], "name": shown["names"][0]["name"]}
    # Who an ID is (#3004): a `kind` action that wrote nothing and left the answer in `resultOther`,
    # a letter for "in this server" or not, then the name Discord shows. Shown once, never kept.
    asked = None
    if got["state"] == "done" and got["action"] == "kind" and got["resultOther"]:
        asked = {"name": got["resultOther"][1:], "inServer": got["resultOther"][0] == "i"}
    # What an "Add all" press did, counts only (#3207): {"place": 5, "npc": 7}; empty otherwise.
    # The door's `counts` is the bot's own text for it, "place:5 npc:7" (one pair per kind).
    counts = {
        k: int(n)
        for k, _, n in (part.partition(":") for part in str(got.get("counts") or "").split())
        if n.isdigit()
    }
    return {
        "state": got["state"],
        "counts": counts,
        "progress": {
            "done": got["progressDone"],
            "total": got["progressTotal"],
            "at": got["progressAt"],
            "createdAt": got["createdAt"],
            "now": got["now"],
        },
        "row": row,
        "player": asked,
        "removed": got["state"] == "done" and got["action"] == "remove",
        "error": got["error"],
        "other": other,
        # Undo is offered for a name just given (or a join), until the bot's record of it is gone.
        "undo": got["state"] == "done"
        and (
            (got["action"] in ("name", "name-join") and bool(got["resultOther"]))
            or (got["action"] == "confirm-kinds" and bool(counts))
        ),
    }
