"""The Questions page's read (#3438): what waits for the signed-in game master, by campaign.

The numbers are the bot's own (`question_counts`), read through a DM-only door, so the website and
Discord's list cannot disagree. Counts and times only; never a name or a word of any question."""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.questions import kinds
from dmbot.web.me import MAX_GUILDS
from dmbot.web.sessions import Session

# The kinds the page knows (`kinds.ORDER`); a stored kind outside it is left out of the total, as
# the Discord list leaves it out, so the two totals agree.
KNOWN = frozenset(kinds.SHOWN)


def shape(guild_id: int, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The door's campaigns for one server in the page's shape: a ref, the name, the total over
    the known kinds, each kind with its count and the time its oldest began, and when the bot
    counted."""
    out: list[dict[str, Any]] = []
    for row in rows:
        waiting = [
            {"kind": str(k["kind"]), "count": int(k["count"]), "oldest": k.get("oldest")}
            for k in row["kinds"]
            if str(k["kind"]) in KNOWN and int(k["count"]) > 0
        ]
        out.append(
            {
                "ref": f"{guild_id}.{row['id']}",
                "name": str(row["name"]),
                "total": sum(k["count"] for k in waiting),
                "kinds": waiting,
                "countedAt": row.get("countedAt"),
            }
        )
    return out


async def read_questions(
    db: Database, session: Session, *, now: float, guesses: bool = True
) -> dict[str, Any]:
    """Every campaign the signed-in person is a game master of, in the servers of this session's
    list, with its waiting questions by kind. A player has none. `guesses` adds, for each campaign,
    how many of DMbot's own matches wait for the DM (#3701): read from the very door that lists
    them (`dmbot_web_waiting_matches`, 0372), so the count and the list cannot differ. A count
    only, never a name; it is not part of the total, which is the bot's own count of questions.
    One extra read for each campaign the person runs (a handful); the door lists at most 200, so the
    count stops there, as the Names page's list does. Needs migration 0372 deployed with the API."""
    campaigns: list[dict[str, Any]] = []
    async with db.user(session.user_id, session=session.id_hash) as conn:
        for guild in session.guilds[:MAX_GUILDS]:
            await conn.execute("SELECT set_config('dmbot.guild_id', %s, true)", (str(guild.id),))
            cur = await conn.execute(
                "SELECT dmbot_web_question_counts(%s) AS found", (int(guild.id),)
            )
            found = await cur.fetchone()
            if found and found["found"]:
                shaped = shape(int(guild.id), found["found"])
                if guesses:
                    for row, made in zip(found["found"], shaped, strict=True):
                        cur = await conn.execute(
                            "SELECT dmbot_web_waiting_matches(%s, %s) AS m",
                            (int(guild.id), str(row["id"])),
                        )
                        got = (await cur.fetchone() or {}).get("m")
                        made["guesses"] = len(got["matches"]) if got else 0
                campaigns += shaped
    campaigns.sort(key=lambda c: (-c["total"], c["name"].lower(), c["ref"]))
    return {"campaigns": campaigns, "serverNow": int(now)}


async def read_total(db: Database, session: Session, *, now: float) -> dict[str, int]:
    """Just the total, for the badge on the account page."""
    got = await read_questions(db, session, now=now, guesses=False)  # (a badge needs no guesses)
    return {"total": sum(int(c["total"]) for c in got["campaigns"])}
