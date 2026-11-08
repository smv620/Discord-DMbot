"""The campaign's names on the website, with the narrator's "said as" spelling (#1451, on #1419).

The DM reads how the narrator will say things on the recap page, so that is where they can fix a
spelling ("Kah-zeth" for Ka'zeth). The website's database role has no right on the memory tables
(secret names live there), so both the read and the write go through two database doors
(`dmbot_web_names`, `dmbot_web_set_said_as`, schema.py). They answer only a DM of the campaign, in a
server on this session's list, and never return or accept a secret name or a person at the table.
The write is logged like every other memory change, so Undo on the Discord name card still works.

A campaign is named by `<server>.<campaign>` (the recap's address without its last part).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from dmbot import plan_rules
from dmbot.ai import TierClient
from dmbot.db import Database
from dmbot.entitlements import plan_grant_access
from dmbot.memory.link_words import quoted
from dmbot.recaps.notify import parse_ref
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

log = logging.getLogger(__name__)

SAID_AS_MAX = 80  # the same limit as the bot's MemoryStore.set_said_as and the database's check
ALIAS_ID_LENGTH = 32


def _parse(ref: str) -> tuple[int, str]:
    """(server, campaign) from `<server>.<campaign>`, or a "not found". The campaign half goes
    through the recap address's own checks (ASCII, letters and digits, bounded)."""
    parsed = parse_ref(f"{ref}.0")
    if parsed is None:
        raise RecapError("not_found")
    return parsed[0], parsed[1]


def _alias_id(text: str) -> str:
    if len(text) != ALIAS_ID_LENGTH or not set(text) <= set("0123456789abcdef"):
        raise RecapError("not_found")
    return text


async def list_names(db: Database, session: Session, ref: str) -> list[dict[str, Any]]:
    """The campaign's confirmed public names, for a DM of it. Anyone else gets "not found"."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute("SELECT dmbot_web_names(%s, %s) AS names", (guild_id, campaign_id))
        found = await cur.fetchone()
    names = found["names"] if found else None
    if names is None:
        raise RecapError("not_found")
    return list(names)


async def set_said_as(
    db: Database, session: Session, ref: str, alias_id: str, spelling: str | None
) -> None:
    """Set or clear how the narrator says one name. The database door holds it to the rules."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    alias = _alias_id(alias_id)
    # The bot's own rule first (MemoryStore.set_said_as): tidy spaces, up to 80 printable letters.
    # This also keeps a NUL, which the database driver refuses, from becoming a server error.
    clean = " ".join((spelling or "").split()) or None
    if clean is not None and (len(clean) > SAID_AS_MAX or not clean.isprintable()):
        raise RecapError("bad_text")
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_set_said_as(%s, %s, %s, %s) AS r",
            (guild_id, campaign_id, alias, clean),
        )
        found = await cur.fetchone()
    outcome = found["r"] if found else "not_allowed"
    if outcome == "ok":
        return
    # Not this campaign's DM, no such name and a secret name are all told as "not found": nobody
    # learns that a secret name with that id exists. Only a too-long spelling is its own answer.
    raise RecapError("bad_text" if outcome == "too_long" else "not_found")


# What a suggestion may be made of (#3806): letters, then more letters joined by a hyphen, a space
# or an apostrophe ("Kah-zeth", "Ka'zeth"). The AI's answer is only ever shown to the DM,
# who presses Save or doesn't, but a sentence, a number or a refusal is not a spelling.
_SPELLING = re.compile(r"[^\W\d_]+(?:[-' \u2019][^\W\d_]+)*")
_SAID_AS_SYSTEM = (
    "You help a Dungeon Master tell a text-to-speech narrator how to say a name from a fantasy "
    "story. Reply with only the name respelled the way it sounds, in plain letters, with hyphens "
    "between the parts to say (for example Ka'zeth -> Kah-zeth). No quotes, no explanation. If the "
    "name is already easy to say, reply with the name unchanged."
)


async def suggest_said_as(
    db: Database,
    session: Session,
    ref: str,
    alias_id: str,
    ai: TierClient | None,
    *,
    now: int,
    enforce_plans: bool,
) -> str:
    """One suggested spelling for a name, or "" when there is none to give (#3806). DM only, with
    the same checks as the save: a name that is not on this campaign's list (another campaign's, a
    secret one) is "not found". Only the name and its kind go to the AI, never the campaign's story.
    Nothing is called without the AI set up and a working plan on the campaign owner's account (the
    bot's `can_use_ai` rule), and an answer that is not a plain spelling comes back empty: the page
    then says DMbot has no suggestion. The suggestion is not saved; the DM decides."""
    guild_id, campaign_id = _parse(ref)
    alias = _alias_id(alias_id)
    found = next((n for n in await list_names(db, session, ref) if n.get("id") == alias), None)
    if found is None:
        raise RecapError("not_found")
    if ai is None:
        return ""
    if enforce_plans:
        # The owner comes from the import page's DM-only door, which answers the campaign's facts
        # for a DM and nothing for anyone else (we already know the caller is a DM).
        async with db.user(session.user_id, session=session.id_hash) as conn:
            await _scoped(conn, guild_id)
            cur = await conn.execute(
                "SELECT dmbot_import_agreed(%s, %s, 0, NULL) AS r", (guild_id, campaign_id)
            )
            row = await cur.fetchone()
        owner = ((row or {}).get("r") or {}).get("owner")
        access = None
        if isinstance(owner, str) and owner.isdecimal():
            _plan, _grant, access = await plan_grant_access(db, int(owner), now)
        if not plan_rules.can_use_ai(access):
            return ""
    name = " ".join(str(found["text"]).split())  # one line, so a name can't forge the next
    text = f"Name: {quoted(name)}\nKind: {found['kind']}"
    try:
        reply = await ai.complete(_SAID_AS_SYSTEM, text, max_tokens=200)
    except Exception as err:
        # The type only: a provider's message may echo the request, which holds the name.
        log.warning("said-as suggestion failed: %s", type(err).__name__)
        raise RecapError("server") from None  # not "no suggestion": the AI did not answer
    if reply.cut:  # a cut answer is never read as "no suggestion" (the AI-limits rule)
        log.warning("said-as suggestion was cut off")
        raise RecapError("server")
    answer = unicodedata.normalize("NFC", " ".join(reply.text.split()))
    if reply.refused or len(answer) > SAID_AS_MAX or not _SPELLING.fullmatch(answer):
        return ""
    return answer
