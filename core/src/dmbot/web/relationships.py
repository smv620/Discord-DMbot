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
from collections.abc import Collection
from typing import Any

from dmbot.db import Database
from dmbot.memory import gate, kin
from dmbot.memory.claims import Extracted, condition_of
from dmbot.memory.link_kinds import link_kind
from dmbot.memory.link_words import back_words, link_words, reads_both_ways
from dmbot.memory.web_actions import RETAKE_AFTER_S
from dmbot.memory.who_resolve import checked_line
from dmbot.web import all_names, graph_view
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

# The website's steps spinner goes 1 to 8 (owner, #2934); MAX_NODES still bounds the work.
MAX_HOPS = 8
MAX_NODES = 1000  # the most names one answer draws, then `truncated`
OVERVIEW_NAMES = 5000  # the most names the overview lists, then `truncated` (the tray is not drawn)
OVERVIEW_PAIRS = 20000  # the most connected pairs it reads, in a fixed order, then `truncated`
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
# a secret other name's own node on the graph (#3037): a centre the page may ask for
ALIAS_NODE = re.compile(r"^alias:[0-9a-f]{32}$")


def label_of(links: list[dict[str, Any]]) -> dict[str, Any]:
    """The connection that names an edge: the latest confirmed one, and never a secret one while a
    known one exists. `links` are the door's, oldest first."""
    known = [x for x in links if not x["secret"]]
    return (known or links)[-1]


VIEWS = ("players", "full")  # the switch: what a player sees, or everything (a DM's, or a peek)


def check_words(decided: object) -> str:
    """DMbot's check of a pronoun, in the words Discord shows a DM ("" when there was none,
    #2859). The doors send the stored note (a fixed vocabulary, no names or text); the words come
    from the one function Discord uses, so the two cannot drift. Only a DM's door ever sends one."""
    return checked_line(decided).strip() if isinstance(decided, str) else ""


def who_knows(link: dict[str, Any]) -> dict[str, Any]:
    """ "Who knows this?" of one connection, for a DM (#2935).

    `who` is "all" when the table may know it (it is not held back), "some" when it is held back
    and some player characters know it, else "dm". `knownBy` lists those characters ({id, name},
    empty unless "some"). A connection is held back when it is secret or the party has not
    learned it (the door says so, by the rule Discord uses). Public readers never see either field.
    """
    held = bool(link["held"])
    known = list(link["known_by"] or [])
    who = "all" if not held else ("some" if known else "dm")
    return {"who": who, "knownBy": known if who == "some" else []}


def edges_from(
    rows: list[dict[str, Any]], *, can_edit: bool, is_dm: bool = False
) -> list[dict[str, Any]]:
    """The door's connections grouped one edge for each pair of names (`a` the smaller ID), each
    with its `label` (the words of the label connection), `more` (how many others there are) and
    every connection as `links`: {id, words, secret, revealed, direction, kind}. `direction` says
    which way the words read between the pair: "a-b", "b-a", or "both" for the ones that read
    the same either way. A connection's `secret` (and whether it is `revealed` yet) is only sent
    to a DM (the door sends none to anyone else). `kind` is for the map's colours (#2942)."""
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
                # how it reads from the second name when that differs ("has a niece", "has as a
                # member"), else None; every connection that has a reading back sends it (#2666),
                # so the page can write each line from the name in the middle
                "back": back_words(str(row["predicate"]), str(row["detail"] or ""), directional),
                "secret": bool(row["secret"]),  # (the real flag; masked on the way out)
                "revealed": bool(row.get("revealed", True)),  # (the party believes it; masked too)
                "pinned": bool(row.get("pinned", False)),  # (the DM's 📌; only a DM ever sees it)
                # DMbot's check, in words; the door sends the note to a DM only (#2859)
                "check": check_words(row.get("decided")),
                "direction": direction,
                "kind": link_kind(str(row["predicate"])),  # for the map's colours (#2942)
                # the player characters who know a held-back connection (#2935); only the DM's full
                # view carries them (the door sends nothing otherwise)
                "known_by": row.get("knownBy"),
                "held": row.get("held"),
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
                        "check": x["check"] if can_edit else "",
                        "direction": x["direction"],
                        "kind": x["kind"],
                        **(who_knows(x) if is_dm else {}),
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


def refuse_bad(center: str | None, hops: int, view: str | None = None) -> None:
    if view is not None and view not in VIEWS:
        raise RecapError("bad_view")
    if center is not None and not (ID_PATTERN.match(center) or ALIAS_NODE.match(center)):
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
    refuse_bad(center, hops, view)
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
        pages = set() if size_only else await _page_ids(conn, guild_id, campaign_id)
    got = found["r"] if found else None
    if got is not None and got["status"] == "peek_off":
        raise RecapError("peek_off")  # a peek the DM screen does not allow
    if got is None or got["status"] != "ok":
        raise RecapError("not_found")
    return {**got, "pageIds": pages}


async def _page_ids(conn: Any, guild_id: int, campaign_id: str) -> set[str]:
    """The entries that have an NPC page, from the same source as the page's own door (#2887):
    ids only. Empty when the door does not answer."""
    cur = await conn.execute(
        "SELECT dmbot_web_npc_page_ids(%s, %s) AS ids", (guild_id, campaign_id)
    )
    row = await cur.fetchone()
    ids = row["ids"] if row else None
    return {str(i) for i in ids} if isinstance(ids, list) else set()


async def is_dm(db: Database, session: Session, ref: str) -> bool:
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT 1 FROM campaign_dms WHERE guild_id = %s AND campaign_id = %s AND user_id = %s",
            (guild_id, campaign_id, session.user_id),
        )
        return await cur.fetchone() is not None


async def gated(db: Database, session: Session, ref: str, view: str | None) -> bool:
    """Whether this look at the graph is worked out by the gate (#3143) rather than read straight
    from the graph door: everything but the full view. A DM asking for no view sees everything; a
    DM asking for Players View sees what the party knows; a player sees what their own characters
    know; a peek ("full") is still the DM's graph, behind its own switch."""
    if view == "full":
        return False
    if view == "players":
        return True
    return not await is_dm(db, session, ref)


async def source_of(
    db: Database, session: Session, ref: str
) -> tuple[dict[str, Any], dict[str, Any], set[str]]:
    """The graph's source and the knowers (two doors, one statement, so one snapshot), and the
    names that have a page. They go to the gate and no further: nothing here is sent to a browser,
    logged or kept, and an error never carries them."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_graph_source(%s, %s) AS s, dmbot_web_knowers_data(%s, %s) AS k",
            (guild_id, campaign_id, guild_id, campaign_id),
        )
        found = await cur.fetchone()
        pages = await _page_ids(conn, guild_id, campaign_id)
    if found is None or found["s"] is None or found["k"] is None:
        raise RecapError("not_found")
    return dict(found["s"]), dict(found["k"]), pages


def viewer_of(known: dict[str, Any]) -> gate.Viewer:
    """Who is looking, when nobody asked to see as someone: the party for a DM in Players View,
    else the caller's own characters (none: nothing). The characters come from who plays them,
    in the database, never from the request."""
    if known["isDm"]:
        return gate.Viewer.the_party()
    return gate.Viewer.player(str(i) for i in known["mine"])


async def graph_of(db: Database, session: Session, ref: str) -> tuple[graph_view.Graph, set[str]]:
    """The graph this caller may see through the gate, and the names that have a page."""
    src, known, pages = await source_of(db, session, ref)
    return graph_view.build(src, known, viewer_of(known)), pages


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
    if await gated(db, session, ref, view):
        refuse_bad(center, hops, view)
        graph, found = await graph_of(db, session, ref)
        shaped = graph_view.web_of(graph, center, hops, MAX_NODES)
        if shaped is None:
            raise RecapError("not_found")
        got = {**shaped, "pageIds": found}
    else:
        got = await _door(db, session, ref, center, hops, size_only=False, view=view)
    page = page_of(got)
    if got["canEdit"] and got["seesAll"]:
        await with_know_words(db, session, ref, page)
    return page


def know_words(known: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """The word for each name and each connection, worked out from who knows it (§9b item 3) by
    the one function the bot uses (`gate.word_of`), never from a stored flag. Returns
    (names by entry id, connections by connection id). Ids only; the words are fixed vocabulary."""
    who: dict[tuple[str, str], set[str]] = {}
    for kind, statement, character, _sure in known["knowers"]:
        who.setdefault((str(kind), str(statement)), set()).add(str(character))
    party = [str(i) for i in known["playerCharacters"]]
    entries = {k[1]: gate.word_of(v, party) for k, v in who.items() if k[0] == "entry"}
    for entry in known["liveEntries"]:
        entries.setdefault(str(entry), gate.word_of((), party))
    links = {k[1]: gate.word_of(v, party) for k, v in who.items() if k[0] == "connection"}
    for link in known["connections"]:
        links.setdefault(str(link), gate.word_of((), party))
    return entries, links


async def with_know_words(db: Database, session: Session, ref: str, page: dict[str, Any]) -> None:
    """Adds `knowWord` to each name and each connection of a DM's full view (#2999, §9b), so the
    page says "secret" only when nobody but the DM knows it. DM only (the door gives nobody else
    its data); the knowers are read, worked into words here and not sent on."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_knowers_data(%s, %s) AS k", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    if found is None or found["k"] is None:
        raise RecapError("not_found")
    names, links = know_words(dict(found["k"]))
    for node in page["nodes"]:
        node["knowWord"] = names.get(str(node["id"]), gate.SECRET)
    for edge in page["edges"]:
        for link in edge["links"]:
            link["knowWord"] = links.get(str(link["id"]), gate.SECRET)


def page_of(got: dict[str, Any]) -> dict[str, Any]:
    """A graph answer (the door's, or the gate's in the door's shape) as the page gets it: each
    name with whether it has an NPC page, and the connections between two names grouped into one
    edge with its words. `pageIds` is dropped."""
    pages: set[str] = got["pageIds"]
    nodes = [
        {
            **{k: v for k, v in n.items() if k not in ("secret", "quests") or v is not None},
            "page": str(n["id"]) in pages,
        }
        for n in got["nodes"]
    ]
    return {
        "center": got["center"],
        "nodes": nodes,
        "edges": edges_from(
            got["edges"],
            can_edit=bool(got["seesAll"]),
            # "Who knows this?" is the DM's alone, in the full view: a player who peeks sees
            # everything the DM screen shows, but not which characters know what (#2935)
            is_dm=bool(got["canEdit"]) and bool(got["seesAll"]),
        ),
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
    if await gated(db, session, ref, view):
        refuse_bad(center, hops, view)
        graph, _found = await graph_of(db, session, ref)
        shaped = graph_view.web_of(graph, center, hops, MAX_NODES)
        if shaped is None:
            raise RecapError("not_found")
        return {"names": int(shaped["names"])}
    got = await _door(db, session, ref, center, hops, size_only=True, view=view)
    return {"names": int(got["names"])}


# The overview's order for the "Not connected yet" tray: the kinds a table asks about first.
TRAY_KIND_ORDER = {"place": 0, "npc": 1, "player_character": 2, "character": 3, "organization": 4}


def overview_of(got: dict[str, Any], pages: Collection[str] = ()) -> dict[str, Any]:
    """The door's names and pairs shaped for the page (#2632): the counts for the line at the top,
    the names with no connection yet (the tray, by kind then name), and every group of two or more
    linked names with the name to centre it on (its most connected one, counting the names it is
    joined to; the graph door counts connections, so a tie can break differently). Pure: the
    groups are found here, so the door stays a plain read. The tray lists names only, so it is not
    drawn and does not count against `MAX_NODES`."""
    names: list[dict[str, Any]] = [
        {
            **{k: v for k, v in n.items() if k != "secret" or v is not None},
            "page": str(n["id"]) in pages,  # an NPC page exists for it (#2887)
        }
        for n in got["names"]
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
        groups.append(
            {
                "center": centre,
                "centerName": by_id[centre]["name"],
                "size": len(ids),
                "page": centre in pages,  # the centre name has a page
            }
        )
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


async def _overview_door(
    db: Database, session: Session, ref: str, view: str | None, max_names: int, max_pairs: int
) -> tuple[dict[str, Any], set[str]]:
    """The overview door's answer for the caller, and the ids of names with a page; the refusals
    are the same for every reader of it."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if view is not None and view not in VIEWS:
        raise RecapError("bad_view")
    if await gated(db, session, ref, view):
        graph, with_pages = await graph_of(db, session, ref)
        return graph_view.overview_of_graph(graph, max_names, max_pairs), with_pages
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
        pages = await _page_ids(conn, guild_id, campaign_id)
    got = found["r"] if found else None
    if got is not None and got["status"] in ("peek_off", "bad_visit"):
        raise RecapError(str(got["status"]))
    if got is None or got["status"] != "ok":
        raise RecapError("not_found")
    return dict(got), pages


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
    got, pages = await _overview_door(db, session, ref, view, max_names, max_pairs)
    return overview_of(got, pages)


SEARCH_MAX = 100  # the longest name; a longer search matches nothing anyway
FIND_LIMIT = 8  # names offered under the "Find a name" box


async def find_names(
    db: Database, session: Session, ref: str, query: str, *, view: str | None = None
) -> list[dict[str, Any]]:
    """The names the caller may see whose main name contains `query`, for the Story graph's "Find a
    name" box (#2989). It reads the graph's own door, so someone looking as the players do (a
    player, or a DM in Players View) is offered only what the players know: never a hidden, limited
    or secret name. (The All names list is the DM's whole list, so it cannot serve that view.)"""
    needle = " ".join(query.split()).casefold()[:SEARCH_MAX]
    if not needle:
        return []
    got, _pages = await _overview_door(db, session, ref, view, OVERVIEW_NAMES, 1)
    found = [
        {"id": n["id"], "name": n["name"], "kind": n["kind"]}
        for n in got["names"]
        if needle in str(n["name"]).casefold()
    ]
    return found[:FIND_LIMIT]


async def read_characters(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The campaign's player characters, for "Who knows this?" (#2935).

    They are the only ones a DM may say know something (the tick boxes). DM only: anyone else, and
    a campaign that is not theirs, is "not found", so nothing hints at who plays what. At most one
    menu's worth (25), by name.
    """
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_player_characters(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    return {"characters": [{"id": str(c["id"]), "name": str(c["name"])} for c in got]}


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


async def queue_in(conn: Any, guild_id: int, campaign_id: str, action: str, target: str) -> str:
    """Queue one target-only action for a campaign the caller has already been matched to (the
    recap page's Undo of a name-pass fix): the one place besides `_queue` that calls the door.
    The door itself checks the caller is a DM and the target is theirs."""
    cur = await conn.execute(
        "SELECT dmbot_web_name_action_add(%s, %s, %s, %s, %s, %s, %s, %s) AS r",
        (guild_id, campaign_id, None, action, "", target, "", False),
    )
    found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError("not_found" if got["status"] == "not_found" else str(got["status"]))
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
# `keep_all` keeps the ones named in `ids` (at most 50) that are ready now, and no others), or
# "which" (a "Which Mara?" question, #2652; actions pick, with `entity` the candidate chosen, and
# neither).
WAITING_QUEUE = {
    ("claim", "confirm"): "claim-confirm",
    ("claim", "reject"): "claim-reject",
    ("backstory", "keep"): "backstory-keep",
    ("backstory", "skip"): "backstory-skip",
    ("backstory", "keep_all"): "backstory-keep-all",
    ("which", "pick"): "backstory-which-pick",
    ("which", "neither"): "backstory-which-neither",
}


async def queue_waiting(db: Database, session: Session, ref: str, body: object) -> str:
    """Queue the DM's answer to one waiting connection, or Keep all. DM only (the door checks), and
    the bot applies the very calls the DM screen's buttons make. Returns the answer's id.

    A claim that cannot be kept ends with one of four `error` words, read back by `read_answer`:
    `gone` (no longer waiting, or dropped for reading like an instruction: final),
    `name_first` (a new name in it must be answered first), `stopped_recording` (someone it cites
    is no longer recorded: the claim is deleted) and `cannot_write` (the rules refuse that fact
    now, for example a plan: it stays waiting). A "Which Mara?" answer ends `gone` (answered or
    dropped already, or the pick is not one of its candidates) or `queue_full` (too many
    connections wait: answer some first; the question stays)."""
    if not isinstance(body, dict):
        raise RecapError("bad_action")
    action = WAITING_QUEUE.get((str(body.get("kind")), str(body.get("action"))))
    if action is None:
        raise RecapError("bad_action")
    secret = body.get("secret", False)
    if not isinstance(secret, bool):
        raise RecapError("bad_action")
    text = ""
    target = "" if action == "backstory-keep-all" else str(body.get("id") or "")
    if action == "backstory-keep-all":
        # Keep all keeps exactly the connections the page showed (ids), never one that turned
        # ready while the DM was looking
        ids = body.get("ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 50:
            raise RecapError("bad_action")
        if not all(isinstance(i, str) and ID_PATTERN.match(i) for i in ids):
            raise RecapError("not_found")
        text = " ".join(dict.fromkeys(ids))
    elif not ID_PATTERN.match(target):
        raise RecapError("not_found")
    if action == "backstory-which-pick":
        picked = str(body.get("entity") or "")  # one of the question's candidates (the door checks)
        if not ID_PATTERN.match(picked):
            raise RecapError("not_found")
        text = picked
    return await _queue(
        db, session, ref, entity="", action=action, target=target, text=text,
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
    stands (or `removed` when its last connection went). An answer to a waiting fact, connection or
    question (`claim-*`, `backstory-*`) also carries `waits` (stage, how long, when it last moved)
    and, for Keep all, `progress` (`{done, total}`), counts and times only (#2921)."""
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
    if got is None or not str(got["action"]).startswith(
        ("link-", "claim-", "backstory-", "reading-", "names-fixed-")
    ):
        raise RecapError("not_found")
    if str(got["action"]).startswith("reading-"):
        return _reading_answer(got)
    if str(got["action"]).startswith("names-fixed-"):
        # the recap page's Undo of a name-pass fix (#2684): how many lines went back, and the wait
        waited = _reading_answer(got)
        return {
            "state": got["state"],
            "error": got["error"],
            "putBack": int(got["resultOther"])
            if got["state"] == "done" and str(got["resultOther"] or "").isdigit()
            else None,
            "waits": waited["waits"],
        }
    edge = None
    if str(got["action"]).startswith(("claim-", "backstory-")):
        # a waiting connection answered: how it ended, and for Keep all how many were kept, and
        # how far it has got while it works (#2921: the stage, the wait and "12 of 50")
        total = int(got.get("progressTotal") or 0)
        keeping = got["action"] == "backstory-keep-all"
        return {
            "state": got["state"],
            "error": got["error"],
            "edge": None,
            "removed": False,
            "kept": int(got["resultId"]) if keeping and got["resultId"] else None,
            "waits": _reading_answer(got)["waits"],
            "progress": {"done": int(got.get("progressDone") or 0), "total": total}
            if keeping and total > 0
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


WAITING_COUNT_KEYS = ("claims", "backstory", "which")

# What people believe (#2412 slice 3b). A reading's rule, in the words the page shows, from the
# status code sets (never the model) and the view that holds it.
READING_RULES = {
    "theory": "theory",
    "accusation": "accusation",
    "suspected_lie": "lie",
    "revealed_fact": "reveal",
    "magically_verified": "confession",
    "evasion_under_magic": "evasion",
    "false_evidence": "false_clue",
    "secret_plot": "plot",
}
READING_QUEUE = {"place": "reading-place", "no": "reading-no", "undo": "reading-undo"}
READINGS_SHOWN = 100


def _who_believes(view: str) -> str:
    if view == "party":
        return "the party"
    if view == "truth":
        return "the DM's view"
    return view.removeprefix("pc:")


def _sureness(status: str, confidence: object) -> str:
    """How sure the belief is, in words (the numbers are set by code from the rule)."""
    if status in ("theory", "accusation", "suspected_lie"):
        value = confidence if isinstance(confidence, (int, float)) else 0.5
        return "fairly sure" if value > 0.5 else "doubtful" if value < 0.5 else "a guess"
    return {
        "revealed_fact": "seen",
        "magically_verified": "seen",
        "evasion_under_magic": "dodged",
        "false_evidence": "false",
        "secret_plot": "secret plot",
    }.get(status, "a guess")


async def read_readings(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """What people believe, waiting for the signed-in DM (#2412): claims that carry a reading, not
    yet kept as a belief or turned down. DM only (the door answers anyone else with nothing, here
    `not_found`). The words are the claim's own (names and a relationship), never a transcript
    line or a quote. Read-only."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute("SELECT dmbot_web_readings(%s, %s) AS r", (guild_id, campaign_id))
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None or got.get("status") != "ok":
        raise RecapError("not_found")
    readings = [
        {
            "id": r["id"],
            "rule": READING_RULES.get(str(r["status"]), "theory"),
            "who": _who_believes(str(r["view"] or "")),
            "statement": _claim_statement(r),
            "sureness": _sureness(str(r["status"]), r["confidence"]),
            # who lied, for a suspected lie only: a name word, or None (the page says "someone")
            "liar": r.get("liar") or None,
            "sessionAt": r["at"],
            # a new name in it has to be answered first (in Check new names)
            "canPlace": not r["waitingOn"],
            "why": "name_first" if r["waitingOn"] else None,
            "waitingOn": r["waitingOn"],
        }
        for r in got["readings"]
    ]
    total = max(int(got.get("total", 0)), len(readings))
    return {"readings": readings, "counts": {"readings": total}, "capped": total > len(readings)}


async def queue_reading(db: Database, session: Session, ref: str, body: object) -> str:
    """Queue the DM's answer to a reading: `{action: "place" | "no" | "undo", id}`; `id` is the
    reading's claim, or for `undo` the answer of the keep it undoes. The bot makes the calls the
    DM screen would (`ClaimStore.place`, `reject`, the store's Undo), so consent and every rule
    are checked there again. Returns the answer's id. The answer ends with an `error` word the
    page can word: `gone` (not waiting any more, or nothing left to undo), `name_first`,
    `stopped_recording`, `cannot_write`, `bad_reading`, `unknown_character`, `unknown_liar` (the
    one who lied is unknown, or is not a character), `waiting_already`, `plot_is_known` or
    `already_known`."""
    if not isinstance(body, dict):
        raise RecapError("bad_action")
    action = READING_QUEUE.get(str(body.get("action")))
    target = str(body.get("id") or "")
    if action is None:
        raise RecapError("bad_action")
    if not ID_PATTERN.match(target):
        raise RecapError("not_found")
    return await _queue(
        db, session, ref, entity="", action=action, target=target, text="", flag=False
    )


def _reading_answer(got: dict[str, Any]) -> dict[str, Any]:
    """Where one answer about a reading stands, with the wait shown (CLAUDE.md: every wait says
    what stage it is at, how long it has run and when it last moved): counts and stages only."""
    state = str(got["state"])
    created = int(got.get("createdAt") or 0)
    claimed = int(got.get("claimedAt") or 0) // 1000  # (the queue marks pickups in milliseconds)
    # The bot also stamps its progress as it goes (Keep all, one connection at a time, #2921).
    moved = max(claimed, int(got.get("progressAt") or 0))
    now = int(got.get("now") or created)
    stage = {"waiting": "queued", "working": "working"}.get(state, state)
    return {
        "state": state,
        "error": got["error"],
        "undoable": got["action"] == "reading-place"
        and state == "done"
        and str(got.get("resultOther") or "").isdigit(),
        "waits": {
            "stage": stage,
            "waitedSeconds": max(0, now - created) if created else 0,
            "lastMovedAt": moved or created,
            "now": now,  # the server's clock, so the page need not guess it (seconds)
            # nothing has moved for longer than a stalled worker is given before it is retaken
            "stalled": state in ("waiting", "working")
            and now - (moved or created) > RETAKE_AFTER_S,
        },
    }


def _can_be_secret(row: dict[str, Any]) -> bool:
    """Whether "truth but secret" is offered: not for a condition ("Roan is dead"), by the DM
    screen's own rule (`claims.condition_of`), so both read it the same way."""
    said = Extracted(
        str(row["subject"]), str(row["relationship"]), str(row["object"]),
        str(row.get("details") or ""), (), str(row["how"]),
    )  # fmt: skip
    return condition_of(said) is None


def _claim_statement(row: dict[str, Any]) -> str:
    """A claim as one plain line, "Mira Voss knows Far Hall" (the DM's page shows the line and the
    session's date; the words are the claim's own, as the DM screen shows them)."""
    base = f"{row['subject']} {row['relationship']} {row['object']}".strip()
    details = str(row.get("details") or "").strip()
    return f"{base} ({details})" if details else base


async def read_waiting(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """What waits for the signed-in DM of this campaign (#2652): the claims PlotBot found at the
    table, the connections a backstory found (ready, or waiting on a name, with the upload each came
    from) and the "Which Mara?" questions, with the counts the overview's "waiting for you" needs.
    DM only (the door answers anyone else with nothing, `not_found` here). Read-only, and nothing
    from a transcript: a name, a short quote the DM's own text, a label the DM typed."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute("SELECT dmbot_web_waiting(%s, %s) AS r", (guild_id, campaign_id))
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None or got.get("status") != "ok":
        raise RecapError("not_found")
    claims = [
        {**c, "statement": _claim_statement(c), "canSecret": _can_be_secret(c)}
        for c in got["claims"]
    ]
    backstory = [
        {
            **{k: v for k, v in b.items() if k != "decided"},
            "how": link_words(
                str(b["predicate"]), str(b["detail"]), kin.directional(str(b["detail"]))
            ),
            "check": check_words(b.get("decided")),
        }
        for b in got["backstory"]
    ]
    which = [
        {
            **{k: v for k, v in w.items() if k != "decided"},
            "how": link_words(
                str(w["predicate"]), str(w["detail"]), kin.directional(str(w["detail"]))
            ),
            "check": check_words(w.get("decided")),
        }
        for w in got["which"]
    ]
    ready = sum(1 for b in backstory if b["ready"])
    totals = got.get("totals") or {}
    total_claims = max(int(totals.get("claims", 0)), len(claims))  # (the lists are cut at caps)
    total_backstory = max(int(totals.get("backstory", 0)), len(backstory))
    return {
        "claims": claims,
        "backstory": backstory,
        "which": which,
        "counts": {
            "claims": total_claims,
            "backstory": total_backstory,
            "ready": ready,
            "which": len(which),
            "all": total_claims + total_backstory + len(which),
        },
    }
