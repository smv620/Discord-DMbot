"""A session's theatrical script, as a text file for the campaign's game masters (#3244).

The route is `GET /me/campaigns/{ref}/transcripts/{number}/script`. The script is a working copy of
a session made from its cleaned transcript, with who and what is meant written in brackets
(`transcript/theatre_*`, made from Discord). It is the game master's alone: a player, or someone
whose campaign it is not, gets `not_allowed`; a session with no script gets `none`. The website's
database role has no right on `theatre_lines`, so the words come through one door
(`dmbot_web_theatre_script`, migration 0323). Each line is written "Name: words", the name being the
one the bot kept at the session (`Speaker N` for a person whose name was never kept, in the order
they first appear), and the file says plainly what it is. Nothing is stored or changed here.
"""

from __future__ import annotations

import re
from typing import Any

from dmbot.db import Database
from dmbot.web.recaps import RecapError, _scoped
from dmbot.web.sessions import Session
from dmbot.web.transcripts import FILE_LIMIT, _sessions, _speakers

NAME_MAX = 40  # the campaign's name in the file's name, as `export.file_name`

HEAD = (
    "{campaign}, session {number}: the script\n"
    "\n"
    "DMbot made this copy from the cleaned transcript. Where a line says he, she or it, [brackets] "
    "say who or what is meant. Only the game master should have it. DMbot can make it again, it is "
    "not in a backup, and a player's lines leave it when they delete their transcripts (a copy "
    "you already saved does not change).\n"
    "\n"
)


def file_name(campaign: str, number: int) -> str:
    """'rime-of-the-frostmaiden-session-7-script.txt': safe on every system."""
    slug = re.sub(r"[^a-z0-9]+", "-", campaign.lower()).strip("-")[:NAME_MAX].strip("-")
    return f"{slug or 'campaign'}-session-{number}-script.txt"


def render_text(
    campaign: str, number: int, lines: list[dict[str, Any]], names: dict[int, str]
) -> str:
    """The file's text: the heading, then one line for each line of the script."""
    shown = dict(names)
    unnamed = 0
    out = [HEAD.format(campaign=" ".join(campaign.split()), number=number)]
    # "Speaker N" counts per file: this file's order of first appearance, which can differ from the
    # transcript download's numbering.
    for line in lines:
        user = int(line["user"])
        if user not in shown:
            unnamed += 1
            shown[user] = f"Speaker {unnamed}"
        words = " ".join(str(line["text"]).split())
        who = " ".join(shown[user].split()) or "Speaker"  # (a name cannot start a fake line)
        out.append(f"{who}: {words}\n")
    return "".join(out)


async def render_script(
    db: Database, session: Session, ref: str, number: int, *, now: int, enforce_plans: bool
) -> tuple[str, str]:
    """(the file's name, the file's text) for the script of session `number`. `not_found` for a
    server the person is not in, a missing campaign or session; `not_allowed` for anyone who is
    not a DM of the campaign; `no_script` when none has been made; `too_big` past the transcript
    file's limit."""
    guild_id, campaign_id, found = await _sessions(
        db, session, ref, now=now, enforce_plans=enforce_plans
    )
    wanted = next((s for s in found["sessions"] if int(s["number"]) == number), None)
    if wanted is None:
        raise RecapError("not_found")
    sid = str(wanted["id"])
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(
            "SELECT dmbot_web_theatre_script(%s, %s, %s) AS r", (guild_id, campaign_id, sid)
        )
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_allowed")
    if got["status"] != "ok":
        raise RecapError("no_script")
    kept, _dms = await _speakers(db, session, guild_id, campaign_id, sid)
    text = render_text(str(found["name"]), number, got["lines"], kept)
    if len(text.encode("utf-8")) > FILE_LIMIT:
        raise RecapError("too_big")
    return file_name(str(found["name"]), number), text
