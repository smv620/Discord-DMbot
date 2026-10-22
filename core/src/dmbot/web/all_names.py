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

from typing import Any

from dmbot.db import Database
from dmbot.memory.background import LABELS
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

PAGE_SIZE = 20  # the same as Discord's Browse pages
SEARCH_MAX = 100  # the longest name; a longer search matches nothing anyway
SORTS = ("name", "newest")
PAGE_LIMIT = 100_000  # far past any campaign: 2 million names

# The kinds Discord names in its own words ("people", "groups", "gods", "ideas": the plain words of
# `memory/background.LABELS`, as in 📝 Check new names) are shown in those words, so the website and
# Discord agree (#2376). The rest are the website's own.
KIND_LABELS: dict[str, str] = {
    "player_character": "Player characters",
    "character": "Characters",
    "creature": "Creatures",
    "clue": "Clues",
    "people": "Peoples",
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
        return LABELS[key][1].capitalize()
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
