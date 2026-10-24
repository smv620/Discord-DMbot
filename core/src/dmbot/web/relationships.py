"""The relationship web on the website (#2323): the names around one name and how they are
connected, for the page that draws them.

The website's database role has no right on the memory tables, so the data comes from one database
door (`dmbot_web_relationships`, schema.py). A DM of the campaign gets everything confirmed, secret
connections and names included (flagged); any other member of the server gets the players' view, the
same rule as the All names page; anyone not in the server, another campaign's ref or a name the
caller may not see is "not found". This module only shapes the door's rows: the connections between
two names become one edge with a label, and the words for each come from `memory/link_words.py`, the
same words as the Discord name card.

A campaign is named by `<server>.<campaign>`, as for the recap and transcript pages.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from dmbot.db import Database
from dmbot.memory.link_words import back_words, link_words, reads_both_ways
from dmbot.web import all_names
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

MAX_HOPS = 4
MAX_NODES = 1000  # the most names one answer draws, then `truncated`
OVERVIEW_NAMES = 5000  # the most names the overview lists, then `truncated` (the tray is not drawn)
OVERVIEW_PAIRS = 20000  # the most connected pairs it reads, in a fixed order, then `truncated`
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def label_of(links: list[dict[str, Any]]) -> dict[str, Any]:
    """The connection that names an edge: the latest confirmed one, and never a secret one while a
    known one exists. `links` are the door's, oldest first."""
    known = [x for x in links if not x["secret"]]
    return (known or links)[-1]


VIEWS = ("players", "full")  # the switch: what a player sees, or everything (a DM's, or a peek)


def edges_from(rows: list[dict[str, Any]], *, can_edit: bool) -> list[dict[str, Any]]:
    """The door's connections grouped one edge for each pair of names (`a` the smaller ID), each
    with its `label` (the words of the label connection), `more` (how many others there are) and
    every connection as `links`: {id, words, secret, revealed, direction}. `direction` says which
    way the words read between the pair: "a-b", "b-a", or "both" for the ones that read the same
    either way. A connection's `secret` (and whether it is `revealed` yet) is only sent to a DM
    (the door sends none to anyone else)."""
    pairs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        a, b = sorted((str(row["a"]), str(row["b"])))
        directional = bool(row.get("directional", False))
        if reads_both_ways(str(row["predicate"]), str(row["detail"] or ""), directional):
            direction = "both"
        else:
            direction = "a-b" if str(row["a"]) == a else "b-a"
        pairs.setdefault((a, b), []).append(
            {
                "id": row["id"],
                "words": link_words(str(row["predicate"]), str(row["detail"] or ""), directional),
                # how it reads from the second name when that differs ("has a niece"), else None
                "back": back_words(str(row["predicate"]), str(row["detail"] or ""), directional)
                if str(row["predicate"]) == "kin_of"
                else None,
                "secret": bool(row["secret"]),  # (the real flag; masked on the way out)
                "revealed": bool(row.get("revealed", True)),  # (the party believes it; masked too)
                "pinned": bool(row.get("pinned", False)),  # (the DM's 📌; only a DM ever sees it)
                "direction": direction,
                "at": int(row["at"]),
            }
        )
    out = []
    for (a, b), links in sorted(pairs.items()):
        links.sort(key=lambda x: (x["at"], x["id"]))
        chosen = label_of(links)
        out.append(
            {
                "a": a,
                "b": b,
                "label": chosen["words"],
                "more": len(links) - 1,
                "links": [
                    {
                        "id": x["id"],
                        "words": x["words"],
                        "back": x["back"],
                        "secret": x["secret"] if can_edit else False,
                        "revealed": x["revealed"] if can_edit else True,
                        "pinned": x["pinned"] if can_edit else False,
                        "direction": x["direction"],
                    }
                    for x in links
                ],
            }
        )
    return out


def visit_of(session: Session) -> str:
    """The visit a peek belongs to: one sign-in. A digest of the session's lookup key, so nothing
    that finds a session is kept in the peek table."""
    return hashlib.sha256(f"{session.id_hash}:peek".encode()).hexdigest()


def _refuse_bad(center: str | None, hops: int, view: str | None = None) -> None:
    if view is not None and view not in VIEWS:
        raise RecapError("bad_view")
    if center is not None and not ID_PATTERN.match(center):
        raise RecapError("not_found")
    if not 1 <= hops <= MAX_HOPS:
        raise RecapError("bad_hops")


async def _door(
    db: Database,
    session: Session,
    ref: str,
    center: str | None,
    hops: int,
    *,
    size_only: bool,
    view: str | None = None,
) -> dict[str, Any]:
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    _refuse_bad(center, hops, view)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        if view is not None:  # asked for; the door decides whether this person may have it
            await conn.execute("SELECT set_config('dmbot.web_view', %s, true)", (view,))
            # which visit a peek belongs to (the door tells the DM once for each)
            await conn.execute(
                "SELECT set_config('dmbot.web_visit', %s, true)", (visit_of(session),)
            )
        cur = await conn.execute(
            "SELECT dmbot_web_relationships(%s, %s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, center, hops, MAX_NODES, size_only),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is not None and got["status"] == "peek_off":
        raise RecapError("peek_off")  # a peek the DM screen does not allow
    if got is None or got["status"] != "ok":
        raise RecapError("not_found")
    return dict(got)


async def read_web(
    db: Database,
    session: Session,
    ref: str,
    *,
    center: str | None = None,
    hops: int = 2,
    view: str | None = None,
) -> dict[str, Any]:
    """The names within `hops` steps of `center` (the most connected name when none is given) and
    the confirmed connections among them. Past `MAX_NODES` the nearest are kept and `truncated` is
    true. `view`: "players" (what a player sees, for anyone) or "full" (everything: a DM's, or a
    player's peek, refused with `peek_off` unless the campaign's DM screen lets players peek);
    none means the person's own. `visibility` and `canPeek` let the page grey out the switch."""
    got = await _door(db, session, ref, center, hops, size_only=False, view=view)
    nodes = [{k: v for k, v in n.items() if k != "secret" or v is not None} for n in got["nodes"]]
    return {
        "center": got["center"],
        "nodes": nodes,
        "edges": edges_from(got["edges"], can_edit=bool(got["seesAll"])),
        "truncated": bool(got["truncated"]),
        "maxHops": MAX_HOPS,
        "canEdit": bool(got["canEdit"]),
        "view": got["view"],
        "visibility": got["visibility"],
        "canPeek": bool(got["canPeek"]),
    }


async def read_size(
    db: Database,
    session: Session,
    ref: str,
    *,
    center: str | None = None,
    hops: int = 2,
    view: str | None = None,
) -> dict[str, int]:
    """How many names are within `hops` steps, so the page can warn before drawing."""
    got = await _door(db, session, ref, center, hops, size_only=True, view=view)
    return {"names": int(got["names"])}


# The overview's order for the "Not connected yet" tray: the kinds a table asks about first.
TRAY_KIND_ORDER = {"place": 0, "npc": 1, "player_character": 2, "character": 3, "organization": 4}


def overview_of(got: dict[str, Any]) -> dict[str, Any]:
    """The door's names and pairs shaped for the page (#2632): the counts for the line at the top,
    the names with no connection yet (the tray, by kind then name), and every group of two or more
    linked names with the name to centre it on (its most connected one, counting the names it is
    joined to; the graph door counts connections, so a tie can break differently). Pure: the
    groups are found here, so the door stays a plain read. The tray lists names only, so it is not
    drawn and does not count against `MAX_NODES`."""
    names: list[dict[str, Any]] = [
        {k: v for k, v in n.items() if k != "secret" or v is not None} for n in got["names"]
    ]
    by_id = {str(n["id"]): n for n in names}
    parent = {i: i for i in by_id}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    degree = dict.fromkeys(by_id, 0)
    for a, b in got["pairs"]:
        if a not in by_id or b not in by_id or a == b:
            continue
        degree[a] += 1
        degree[b] += 1
        parent[find(a)] = find(b)
    members: dict[str, list[str]] = {}
    for i in by_id:
        if degree[i]:
            members.setdefault(find(i), []).append(i)

    def order(i: str) -> tuple[int, str, str]:
        return (-degree[i], str(by_id[i]["name"]).lower(), i)

    groups = []
    for ids in members.values():
        centre = min(ids, key=order)
        groups.append({"center": centre, "centerName": by_id[centre]["name"], "size": len(ids)})
    groups.sort(key=lambda g: (-int(g["size"]), str(g["centerName"]).lower(), str(g["center"])))
    tray = sorted(
        (n for n in names if not degree[str(n["id"])]),
        key=lambda n: (
            TRAY_KIND_ORDER.get(str(n["kind"]), 9),
            str(n["name"]).lower(),
            str(n["id"]),
        ),
    )
    return {
        "counts": {
            "names": int(got["total"]),
            "connections": int(got["connections"]),
            "connectedNames": sum(1 for i in by_id if degree[i]),
            "notConnected": len(tray),
        },
        "tray": tray,
        "groups": groups,
        # either cap cut something: the numbers are then a little low, and the page says so
        "truncated": int(got["total"]) > len(names) or bool(got.get("pairsMore")),
        "canEdit": bool(got["canEdit"]),
        "view": got["view"],
        "visibility": got["visibility"],
        "canPeek": bool(got["canPeek"]),
    }


async def read_overview(
    db: Database,
    session: Session,
    ref: str,
    *,
    view: str | None = None,
    max_names: int = OVERVIEW_NAMES,
    max_pairs: int = OVERVIEW_PAIRS,
) -> dict[str, Any]:
    """Every name the caller may see, which are connected and in which groups (#2632): the numbers
    for the Story graph's count line, its "Not connected yet" tray and its other groups. The same
    people see the same names as in `read_web` (the door shares that door's checks); `view` as
    there."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if view is not None and view not in VIEWS:
        raise RecapError("bad_view")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        if view is not None:
            await conn.execute("SELECT set_config('dmbot.web_view', %s, true)", (view,))
            await conn.execute(
                "SELECT set_config('dmbot.web_visit', %s, true)", (visit_of(session),)
            )
        cur = await conn.execute(
            "SELECT dmbot_web_graph_overview(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, max_names, max_pairs),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is not None and got["status"] in ("peek_off", "bad_visit"):
        raise RecapError(str(got["status"]))
    if got is None or got["status"] != "ok":
        raise RecapError("not_found")
    return overview_of(dict(got))


async def peek(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """A player turned the peek on (after the page's warning): the DM is told once for this visit,
    on the DM screen, by the bot. Refused with `peek_off` when the campaign's DM screen is private;
    a DM needs no peek. The first call for a visit says `noted`; a visit is one sign-in."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_peek(%s, %s, %s) AS r", (guild_id, campaign_id, visit_of(session))
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return {"noted": bool(got["noted"])}


# ---- the DM's edits: queued for the bot, read back by polling (#2323) ---------------------------

WORDS_MAX = 100  # a connection's words: the same limit as a name


def _words(value: object) -> str:
    if not isinstance(value, str):
        raise RecapError("bad_name")
    clean = " ".join(value.split())
    if not clean or not clean.isprintable():
        raise RecapError("bad_name")
    if len(clean) > WORDS_MAX:
        raise RecapError("too_long")
    return clean


async def _queue(
    db: Database,
    session: Session,
    ref: str,
    *,
    entity: str | None,
    action: str,
    target: str,
    text: str,
    flag: bool,
) -> str:
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, entity, action, "", target, text, flag),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
        raise RecapError("bad_name" if got["status"] == "bad_text" else str(got["status"]))
    return str(got["id"])


async def queue_add(db: Database, session: Session, ref: str, body: object) -> str:
    """Queue a new connection `{a, b, words, secret}` and return the answer's id. DM only; the
    names, the words and the secret switch are checked here, by the door and again by the bot."""
    if not isinstance(body, dict):
        raise RecapError("bad_action")
    a, b = str(body.get("a") or ""), str(body.get("b") or "")
    if not ID_PATTERN.match(a) or not ID_PATTERN.match(b):
        raise RecapError("not_found")
    if not isinstance(body.get("secret"), bool):
        raise RecapError("bad_action")
    return await _queue(
        db, session, ref, entity=a, action="link-add", target=b,
        text=_words(body.get("words")), flag=bool(body["secret"]),
    )  # fmt: skip


async def queue_edit(db: Database, session: Session, ref: str, link_id: str, body: object) -> str:
    """Queue a change to one connection: `{action: "words", words}`, `{action: "secret",
    secret}`, `{action: "pin", pinned}` or `{action: "remove", confirm: true}`. Returns the
    answer's id."""
    if not ID_PATTERN.match(link_id):
        raise RecapError("not_found")
    if not isinstance(body, dict):
        raise RecapError("bad_action")
    action = body.get("action")
    if action == "words":
        return await _queue(
            db, session, ref, entity=None, action="link-words", target=link_id,
            text=_words(body.get("words")), flag=False,
        )  # fmt: skip
    if action == "secret":
        if not isinstance(body.get("secret"), bool):
            raise RecapError("bad_action")
        return await _queue(
            db, session, ref, entity=None, action="link-secret", target=link_id,
            text="", flag=bool(body["secret"]),
        )  # fmt: skip
    if action == "pin":  # 📌 100% truth (#2447); the door lets only a DM queue it
        if not isinstance(body.get("pinned"), bool):
            raise RecapError("bad_action")
        return await _queue(
            db, session, ref, entity=None, action="link-pin", target=link_id,
            text="", flag=bool(body["pinned"]),
        )  # fmt: skip
    if action == "remove":
        if body.get("confirm") is not True:
            raise RecapError("needs_confirm")
        return await _queue(
            db, session, ref, entity=None, action="link-remove", target=link_id,
            text="", flag=True,
        )  # fmt: skip
    raise RecapError("bad_action")


# Answers to the connections that wait for the DM (#2635, #2642): body `{kind, action, id, secret}`.
# `kind` "claim" (said at the table; actions confirm, reject; `secret` is "truth but secret") or
# "backstory" (from the DM's own backstory; actions keep, skip; `secret` is "keep as secret";
# `keep_all` keeps every one that is ready and takes no id).
WAITING_QUEUE = {
    ("claim", "confirm"): "claim-confirm",
    ("claim", "reject"): "claim-reject",
    ("backstory", "keep"): "backstory-keep",
    ("backstory", "skip"): "backstory-skip",
    ("backstory", "keep_all"): "backstory-keep-all",
}


async def queue_waiting(db: Database, session: Session, ref: str, body: object) -> str:
    """Queue the DM's answer to one waiting connection, or Keep all. DM only (the door checks), and
    the bot applies the very calls the DM screen's buttons make. Returns the answer's id."""
    if not isinstance(body, dict):
        raise RecapError("bad_action")
    action = WAITING_QUEUE.get((str(body.get("kind")), str(body.get("action"))))
    if action is None:
        raise RecapError("bad_action")
    secret = body.get("secret", False)
    if not isinstance(secret, bool):
        raise RecapError("bad_action")
    target = "" if action == "backstory-keep-all" else str(body.get("id") or "")
    if action != "backstory-keep-all" and not ID_PATTERN.match(target):
        raise RecapError("not_found")
    return await _queue(
        db, session, ref, entity="", action=action, target=target, text="",
        flag=secret and action in ("claim-confirm", "backstory-keep"),
    )  # fmt: skip


async def queue_reveal(db: Database, session: Session, ref: str, body: object) -> str:
    """Queue the DM's 👁️ Reveal to players on one name or connection (#2405): `{kind: "name"|"link",
    id}`. It makes the same calls as Discord's. A name stops being held back from the players
    through the All names page's own `hide` action switched off (the Names card's switch), so its
    answer is read at the All names answers; a connection stops being unrevealed through
    `link-secret` switched off (the one switch the connection card has), read at this web's
    answers. DM only, re-checked by the door; an id from another campaign is `not_found`. The
    change log and Undo are Discord's own, since the bot makes those calls."""
    if not isinstance(body, dict):
        raise RecapError("bad_action")
    kind, target = body.get("kind"), str(body.get("id") or "")
    if kind not in ("name", "link") or not ID_PATTERN.match(target):
        raise RecapError("not_found")
    if kind == "name":
        return await all_names.queue_action(
            db, session, ref, target, {"action": "hide", "hidden": False}
        )
    return await _queue(
        db, session, ref, entity=None, action="link-secret", target=target, text="", flag=False
    )


async def read_answer(db: Database, session: Session, ref: str, answer: str) -> dict[str, Any]:
    """Where one of the caller's own connection edits stands: `state` (waiting, working, done,
    failed), the `error` word when it failed, and, when it is done, the pair's `edge` as it now
    stands (or `removed` when its last connection went)."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not ID_PATTERN.match(answer):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_name_action_read(%s, %s, %s) AS r", (guild_id, campaign_id, answer)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None or not str(got["action"]).startswith(("link-", "claim-", "backstory-")):
        raise RecapError("not_found")
    edge = None
    if str(got["action"]).startswith(("claim-", "backstory-")):
        # a waiting connection answered: how it ended, and for Keep all how many were kept
        return {
            "state": got["state"],
            "error": got["error"],
            "edge": None,
            "removed": False,
            "kept": int(got["resultId"])
            if got["action"] == "backstory-keep-all" and got["resultId"]
            else None,
        }
    if got["state"] == "done" and got["resultId"] and got["resultOther"]:
        around = await read_web(db, session, ref, center=got["resultId"], hops=1)
        pair = sorted((got["resultId"], got["resultOther"]))
        edge = next((e for e in around["edges"] if [e["a"], e["b"]] == pair), None)
    return {
        "state": got["state"],
        "error": got["error"],
        "edge": edge,
        "removed": got["state"] == "done" and edge is None,
    }
