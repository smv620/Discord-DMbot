"""A player's character sheets, for the website (#2816; the character-sheets row of the parity
table on #2554; dmbot.ui.sheets is the Discord side).

A player sees the sheets of the characters they play; a DM sees every sheet in the campaign, read
only (Discord's DM view has no buttons but "Read again"). Linking a D&D Beyond sheet and forgetting
one are the player's own choices, and "Read again" is a DM's, once a minute per campaign. Every
choice goes through a queue the bot applies with the store calls Discord uses, and the bot fetches
any link itself: the website never reads a D&D Beyond page, and never keeps a password or a cookie.

The link is shown to the player and the campaign's DMs only, as in Discord. The door checks the
shape of a link before anything is queued (`bad_link`); the bot checks the rest again.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from dmbot import progress
from dmbot.db import Database
from dmbot.memory.sheets import character_id, sheet_url
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

ACTIONS = frozenset({"link", "forget", "read_again", "typed", "document"})
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
# The campaign-wide choice has no character: the route takes "-" for it, as Discord's ids do.
NO_CHARACTER = "-"
LINK_MAX = 80
# The limits of Discord's own forms (`ui.sheets.TypedForm`, `ui.sheet_import.DocumentLinkForm`), so
# a choice that Discord would refuse is refused here too. The door checks them again.
DOCUMENT_LINK_MAX = 400
TYPED_NAME_MAX = 60
TYPED_NAMES_TEXT_MAX = 1500
TYPED_PAYLOAD_MAX = 4000  # (JSON escapes can double a long list of names)
# A link the bot may fetch (its fetch is https only, `dmbot.fetch`): a web address and nothing that
# could end the line or the address early.
DOCUMENT_LINK = re.compile(r"^https://[^\s\x00-\x1f\x7f<>\"]+$")


def shape(got: dict[str, Any]) -> dict[str, Any]:
    """The door's answer as the page's: whether the caller is a DM of the campaign, and the sheets
    they may see, each with its character's name, whether it is theirs, where it came from, its
    link (when it has one), when it was read and the summary of its numbers."""
    return {
        "isDm": bool(got["isDm"]),
        "sheets": [
            {
                "entityId": str(s["entityId"]),
                "name": str(s["name"]),
                "mine": bool(s["mine"]),
                "source": s["source"],
                "url": s["url"],
                "fetchedAt": None if s["fetchedAt"] is None else int(s["fetchedAt"]),
                "summary": s["summary"],
            }
            for s in got["sheets"]
        ],
    }


async def read_sheets(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The character sheets the signed-in person may see in this campaign. A server the person is
    not in, or a campaign where they are neither a DM nor a player, is "not found"."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_sheet_read(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    return shape(got)


def _typed_payload(body: dict[str, Any]) -> str:
    """The typed form as the one small JSON text the door keeps (`bad_typed` for anything else):
    class and species up to 60 characters, a level from 1 to 20, the names up to 1,500."""
    class_name, species, names = (
        body.get("className"),
        body.get("species", ""),
        body.get("names", ""),
    )
    level = body.get("level")
    if not (isinstance(class_name, str) and isinstance(species, str) and isinstance(names, str)):
        raise RecapError("bad_typed")
    class_name = class_name.strip()
    if not 1 <= len(class_name) <= TYPED_NAME_MAX or len(species.strip()) > TYPED_NAME_MAX:
        raise RecapError("bad_typed")
    if len(names) > TYPED_NAMES_TEXT_MAX:
        raise RecapError("bad_typed")
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 20:
        raise RecapError("bad_typed")
    payload = json.dumps(
        {"className": class_name, "level": level, "species": species.strip(), "names": names},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(payload) > TYPED_PAYLOAD_MAX:
        raise RecapError("bad_typed")
    return payload


def _choice(body: object) -> tuple[str, str]:
    """The action and what goes with it for the door: a link's one address (spaces taken off the
    ends), a document's web address, or the typed form as JSON text; empty for the others."""
    if not isinstance(body, dict):
        raise RecapError("bad_action")
    action = body.get("action")
    if not isinstance(action, str) or action not in ACTIONS:
        raise RecapError("bad_action")
    if action == "typed":
        return action, _typed_payload(body)
    link = body.get("url", "")
    if action == "document":
        if not isinstance(link, str):
            raise RecapError("bad_document")
        link = link.strip()
        if len(link) > DOCUMENT_LINK_MAX or not DOCUMENT_LINK.match(link):
            raise RecapError("bad_document")
        return action, link
    if not isinstance(link, str) or len(link) > LINK_MAX:
        raise RecapError("bad_link")
    if action == "link":
        # Any link Discord accepts (no www, a share path, a query) becomes the one address the
        # door and the bot keep, so the door's own shape check and the bot's agree.
        number = character_id(link.strip())
        if number is None:
            raise RecapError("bad_link")
        return action, sheet_url(number)
    return action, ""


async def queue_action(db: Database, session: Session, ref: str, entity: str, body: object) -> str:
    """Queue one sheet choice for the bot and return its id. `entity` is the character's id, or
    "-" for the campaign-wide read again. The door checks who may make the choice; the bot checks
    it again when it applies the choice."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    action, link = _choice(body)
    who = "" if entity == NO_CHARACTER else entity
    if who and not ID_PATTERN.match(who):
        raise RecapError("not_found")
    if action != "read_again" and not who:
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_sheet_action_add(%s, %s, %s, %s, %s) AS r",
            (guild_id, campaign_id, who, action, link),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return str(got["id"])


async def read_action(
    db: Database, session: Session, ref: str, answer: str, *, now: int | None = None
) -> dict[str, Any]:
    """Where one of the caller's own sheet choices stands, in the shared progress shape, with the
    character it was about. Nobody else's choice."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    if not ID_PATTERN.match(answer):
        raise RecapError("not_found")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_sheet_action_read(%s, %s, %s) AS r",
            (guild_id, campaign_id, answer),
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_found")
    reason = got["error"] or None
    shaped = progress.shape(
        got["stage"],
        started_at=int(got["createdAt"]),
        updated_at=int(got["updatedAt"]),
        now=int(time.time()) if now is None else now,
        reason="failed" if reason else None,
    )
    return {**shaped, "action": got["action"], "entityId": got["entityId"], "error": reason}
