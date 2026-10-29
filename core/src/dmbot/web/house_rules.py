"""A campaign's house rules on the website (#1521): read-only, for anyone in the server, with the
download of the same file Discord gives.

House rules are the table's rules: every member may read them with `/dmbot houserules` (the bot
does not gate that, or its download button, by plan; the plan rule of the transcripts is NOT applied
here). Two database doors (`dmbot_web_house_rules`, `dmbot_web_house_rules_campaigns`, schema.py)
hand the stored rows to any signed-in person whose session lists the server; the website's role
has no grant on the tables. The DM changes the rules on the DM screen, never here.

The page lists the campaign's own rules the way the bot's list does (newest number first, each rule
with its own number and its "instead of"), then the house-rules sets the campaign follows, each
cited as the bot cites it ("<set> house rules, v4"). The download is `rules/house_file.write`'s
text and `house_file.filename`, the file the bot's Download button sends: the campaign's own rules
only, lowest number first, none of a followed set's ("no one else's").

Never shown: a rule's scenario (the story event behind it, not in the Discord list), who wrote it,
a set's publisher or share code, and the campaign's linked rules document, which can be a private
share link the bot shows only to its DMs.

A campaign is named by `<server>.<campaign>`, as for the recap and transcript pages.
"""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.recaps.notify import parse_ref
from dmbot.rules import house_file
from dmbot.rules.followed import cite
from dmbot.rules.house import HouseRule
from dmbot.web.me import MAX_GUILDS
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session


def _parse(ref: str) -> tuple[int, str]:
    parsed = parse_ref(f"{ref}.0")
    if parsed is None:
        raise RecapError("not_found")
    return parsed[0], parsed[1]


async def list_campaigns(db: Database, session: Session) -> list[dict[str, Any]]:
    """The campaigns with a house rule or a followed set, in every server of this session's list:
    the address, the name, the server's name and how many of each."""
    out: list[dict[str, Any]] = []
    async with db.user(session.user_id, session=session.id_hash) as conn:
        for guild in session.guilds[:MAX_GUILDS]:
            await _scoped(conn, int(guild.id))
            cur = await conn.execute(
                "SELECT dmbot_web_house_rules_campaigns(%s) AS found", (int(guild.id),)
            )
            row = await cur.fetchone()
            for item in (row["found"] if row else None) or []:
                out.append(
                    {
                        "ref": f"{guild.id}.{item['id']}",
                        "name": item["name"],
                        "serverName": guild.name,
                        "rules": int(item["rules"]),
                        "sets": int(item["sets"]),
                    }
                )
    return out


async def _read(db: Database, session: Session, ref: str) -> tuple[int, str, dict[str, Any]]:
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_house_rules(%s, %s) AS found", (guild_id, campaign_id)
        )
        row = await cur.fetchone()
    found = row["found"] if row else None
    if found is None:
        raise RecapError("not_found")
    return guild_id, campaign_id, found


def _followed(raw: Any) -> list[dict[str, Any]]:
    """A followed set's rules, lowest number first; a row that does not fit is skipped rather than
    failing the page, as `rules.followed.from_follow` does."""
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            out.append(
                {
                    "number": int(item["number"]),
                    "rule": str(item["rule"]),
                    "supersedes": str(item["instead"]) if item.get("instead") else None,
                }
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return sorted(out, key=lambda r: r["number"])


async def read_rules(db: Database, session: Session, ref: str) -> dict[str, Any]:
    """The campaign's house rules and the sets it follows. Anyone outside the server, and a
    campaign that is not there, get "not found"."""
    _g, _c, found = await _read(db, session, ref)
    return {
        "name": found["name"],
        # What the Download button saves it as (the bot's own name for the file).
        "fileName": house_file.filename(found["name"]),
        "rules": [
            {
                "number": int(r["number"]),
                "rule": r["rule"],
                "supersedes": r["supersedes"],
                "fromSet": r["fromSet"],
            }
            for r in found["rules"]
        ],
        "sets": [
            {
                "name": s["name"],
                "version": int(s["version"]),
                "cite": cite(s["name"], int(s["version"])),
                "rules": _followed(s["rules"]),
            }
            for s in found["sets"]
        ],
    }


async def render(db: Database, session: Session, ref: str) -> tuple[str, str]:
    """(the file's name, the file's text): what the bot's Download button sends. No rules, no file:
    the bot offers none before the first rule either."""
    _g, campaign_id, found = await _read(db, session, ref)
    if not found["rules"]:
        raise RecapError("not_found")
    rules = [
        HouseRule(
            number=int(r["number"]),
            campaign_id=campaign_id,
            rule=r["rule"],
            supersedes=r["supersedes"],
            scenario=None,
            session_id=None,
            created_by=0,
            created_at=0,
            updated_at=0,
            from_set=r["fromSet"],
        )
        for r in found["rules"]
    ]
    return house_file.filename(found["name"]), house_file.write(found["name"], rules)
