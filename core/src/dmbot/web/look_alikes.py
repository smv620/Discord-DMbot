"""The website's "Names that look alike" (#5259, Discord's 🔗 Look-alikes): the groups of
confirmed names that may be one name written several ways, for a DM of the campaign.

The database door (`dmbot_web_look_alike_names`, migration 0623) gives the names and the pairs the
DM said are "Not the same"; the groups come from the bot's own pure finder (`memory/lookalikes.py`),
so the two places list the same groups and the Questions count agrees with the list. The kind is
Discord's short word. Counts and names only, nothing logged.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from dmbot.db import Database
from dmbot.memory.lookalikes import Name, family_of
from dmbot.memory.lookalikes import find as find_look_alikes
from dmbot.memory.name_words import KIND_SHORT, kind_key
from dmbot.web import relationships
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


async def read_groups(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """`{groups: [[{id, name, kind, heard}, …], …], count}`, most heard first in each group, as
    Discord shows them. Anyone who is not a DM of the campaign (or in the server) gets "not
    found"."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_look_alike_names(%s, %s) AS r", (guild_id, campaign_id)
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_found")
    kinds: dict[str, str] = {}
    names: list[Name] = []
    for row in got["names"]:
        key = kind_key(str(row["type"]), row.get("role"))
        kinds[str(row["id"])] = KIND_SHORT.get(key, "other")
        names.append(
            Name(str(row["id"]), str(row["name"]), int(row["heard"]), family_of(row["type"]))
        )
    apart = {(str(a), str(b)) for a, b in got["apart"]}
    # Off the event loop: a campaign of thousands of names takes a moment (as the bot does it).
    groups = await asyncio.to_thread(lambda: find_look_alikes(names, apart=apart))
    return {
        "groups": [
            [
                {"id": n.id, "name": n.name, "kind": kinds.get(n.id, "other"), "heard": n.mentions}
                for n in group
            ]
            for group in groups
        ],
        "count": len(groups),
    }


_ID = re.compile(r"^[0-9a-f]{32}$")
_BATCH = re.compile(r"^[0-9]{1,15}$")
_MAX_IDS = 50  # (as many as `MERGE_MANY_MAX`: the queue door's own limit says the same)


def _ids(value: object, *, least: int = 0) -> list[str]:
    """A list of distinct name ids from the request, or "bad action"."""
    if not isinstance(value, list) or len(value) > _MAX_IDS or len(value) < least:
        raise RecapError("bad_action")
    if not all(isinstance(i, str) and _ID.fullmatch(i) for i in value):
        raise RecapError("bad_action")
    if len(set(value)) != len(value):
        raise RecapError("bad_action")
    return [str(i) for i in value]


async def queue_merge(db: Database, session: Session, ref: str, body: object) -> str:
    """🔗 Merge: `{"keep": id, "others": [id, …], "left": [id, …]}`. Every name in `others` goes
    into `keep` as one change; each in `left` (the names the DM unticked) is remembered as not the
    same as `keep`. The id of the answer the page polls; the queue door checks the caller is a DM
    and every name, and the bot checks again when it applies them."""
    data = body if isinstance(body, dict) else {}
    keep = data.get("keep")
    if not isinstance(keep, str) or not _ID.fullmatch(keep):
        raise RecapError("bad_action")
    others = _ids(data.get("others"), least=1)
    left = _ids(data.get("left", []))
    if keep in others or keep in left or set(others) & set(left):
        raise RecapError("bad_action")
    text = " ".join(others) + (" | " + " ".join(left) if left else "")
    return await relationships._queue(
        db, session, ref, entity=keep, action="lookalike-merge", target="", text=text, flag=False
    )


async def queue_apart(db: Database, session: Session, ref: str, body: object) -> str:
    """Not the same: `{"ids": [id, id, …]}`; every pair among them is remembered, so the group is
    not offered again."""
    data = body if isinstance(body, dict) else {}
    ids = _ids(data.get("ids"), least=2)
    return await relationships._queue(
        db, session, ref, entity=None, action="lookalike-apart", target="",
        text=" ".join(ids), flag=False,
    )  # fmt: skip


async def queue_undo(db: Database, session: Session, ref: str, batch: str) -> str:
    """↩ Undo of a merge made from the page, by the change number its answer carried. The bot reads
    the number again in the caller's own done merges before it undoes anything."""
    if not _BATCH.fullmatch(batch):
        raise RecapError("not_found")
    return await relationships._queue(
        db, session, ref, entity=None, action="lookalike-undo", target="", text=batch, flag=False
    )
