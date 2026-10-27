"""A campaign's optional rules for the website's Settings tab (#2753; docs/PLAN.md, "Everything on
the website except picking Discord channels").

Read only here: which optional rules from other books apply to the campaign's main rules, and which
are on. The website's role cannot read the campaign tables, so one DM-only database door
(`dmbot_web_optional_rules`) gives the main rules and the choices, and this module joins them with
the rules catalog (`dmbot.rules.optional`) using the same functions as Discord's
`/dmbot optionalrules`, so the list and its words cannot drift apart. A choice is made through the
settings queue (`web/campaign_settings.py`) and applied by the bot. Only the catalog's own text
leaves (rule names and summaries); nothing of the campaign but its choices.

A campaign is named by `<server>.<campaign>`, as for the other pages.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.rules import optional
from dmbot.rules.optional import covered_note, main_rules
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


def shape(found: dict[str, Any]) -> dict[str, Any]:
    """The page's answer from the door's: each rule that applies, with whether it is on and whether
    the DM chose that (`changed`) or it follows the campaign-wide default."""
    target = str(found["target"])
    default = bool(found["default"])
    overrides = {str(k): bool(v) for k, v in dict(found["overrides"]).items()}
    rules = [
        {
            "id": r.id,
            "book": r.source,
            "name": r.name,
            "summary": r.summary,
            "on": optional.is_on(r.id, overrides, default),
            "changed": r.id in overrides,
        }
        for r in optional.applying(target)
    ]
    return {
        "main": main_rules(target),
        "rules": rules,
        "note": covered_note(target),
        "default": default,
    }


async def read_rules(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The campaign's optional rules for a DM of it. A server the person is not in is "not found";
    anyone else, a player included, is "not allowed"."""
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_optional_rules(%s, %s) AS r", (guild_id, campaign_id)
        )
        found = await cur.fetchone()
    got = found["r"] if found else None
    if got is None:
        raise RecapError("not_allowed")
    return shape(got)
