"""The Story Graph's player character icons (#3621): what each character draws, and who may change
it. The picture comes from a database door (`dmbot_web_pc_icons`) that resolves the character's
choice against the stored Discord avatar hash and D&D Beyond portrait link (both checked there
again) and builds the address; only a link on Discord's avatar address or D&D Beyond's own hosts
can come out, never one typed by a user. The door's answer is added only to the nodes the graph
already lets this person see, so a hidden node never carries a picture. The choice is one instant
write by `dmbot_web_pc_icon_set`, which decides in SQL that the caller is the campaign's DM or the
character's own player. The image is never fetched or kept here.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

PAGE_CHOICES = (
    "default",
    "discord",
    "dndbeyond",
    "hidden",
)  # what the page may send (custom: #3623)


def attach(page: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Adds `icon` (`{kind, url}`, or None for the page's own 👤), `iconChoice` and `canSetIcon` to
    each player character node of a graph page that has a row, in place; `iconHas` (which pictures
    exist) only where the caller may change it."""
    by_id = {str(r["id"]): r for r in rows}
    for node in page.get("nodes", []):
        row = by_id.get(str(node["id"]))
        if row is None:
            continue
        node["icon"] = row.get("icon")
        node["iconChoice"] = row.get("choice", "default")
        node["canSetIcon"] = bool(row.get("canSet"))
        if row.get("canSet"):
            node["iconHas"] = row.get("has") or {"discord": False, "dndbeyond": False}


async def with_icons(db: Database, session: Session, ref: str, page: dict[str, Any]) -> None:
    """Reads the door and adds the icons to `page`. A campaign that vanished meanwhile has none."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute("SELECT dmbot_web_pc_icons(%s, %s) AS r", (guild_id, campaign_id))
        found = await cur.fetchone()
    rows = found["r"] if found is not None and isinstance(found["r"], list) else []
    attach(page, rows)


async def set_icon(
    db: Database, session: Session, ref: str, entity_id: str, body: object
) -> dict[str, str]:
    """Choose a character's icon: `{choice}` is `default` (Remove), `discord`, `dndbeyond` or
    `hidden`. Answers `{previous, now}`, so the page's Undo is writing `previous` back. Refusals:
    `bad_choice`, `not_found` (not a confirmed player character here), `not_allowed` (neither the
    campaign's DM nor that character's player), `unavailable` (that picture does not exist now)."""
    choice = body.get("choice") if isinstance(body, dict) else None
    if choice not in PAGE_CHOICES:
        raise RecapError("bad_choice")
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_pc_icon_set(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, entity_id, choice),
        )
        found = await cur.fetchone()
    got = found["r"] if found is not None else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return {"previous": str(got["previous"]), "now": str(got["now"])}
