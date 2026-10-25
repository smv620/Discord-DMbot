"""The recap page's "Names fixed this session" (#2684, #2628): what PlotBot's name pass did to the
recap's session, for a DM of the campaign only.

The read comes from one database door (`dmbot_recap_names_fixed`): the counts and each fix come
from the pass's own record, and the spellings as heard are read from the transcript's respell
records while a fix stands. Those spellings are words from a transcript: they are sent to this
campaign's DM and no one else, and nothing here logs them. The website has no right on the tables;
the door checks the caller is a DM of the campaign, and anyone else (a player, another DM, an
unknown recap) is told "not found"."""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.web import recaps
from dmbot.web.recaps import RecapError
from dmbot.web.sessions import Session


async def read_names_fixed(db: Database, session: Session, ref: str) -> dict[str, Any]:
    async with db.user(session.user_id, session=session.id_hash) as conn:
        gid, cid, at, _row = await recaps._one(conn, ref, session, marks=False)
        cur = await conn.execute("SELECT dmbot_recap_names_fixed(%s, %s, %s) AS r", (gid, cid, at))
        got = (await cur.fetchone() or {}).get("r")
    if got is None:
        raise RecapError("not_found")
    return {
        "fixed": [
            {
                "id": str(f["id"]),
                "name": f["name"],
                "times": int(f["times"]),
                "forms": [{"heard": x["heard"], "times": int(x["times"])} for x in f["forms"]],
                "undone": bool(f["undone"]),
            }
            for f in got["fixed"]
        ],
        "unsure": int(got["unsure"]),
        "new": int(got["new"]),
        "guarded": int(got["guarded"]),
    }
