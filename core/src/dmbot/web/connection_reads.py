"""A connection said in the DM's own words, on the website (#2940, slice C; the Discord half is
`ui/connection_say.py`).

The DM writes up to 1,000 characters about two named entries; the bot reads them once into
proposals (`memory/connection_said.py`, ten reads an hour for a campaign) and the page shows each
as one plain sentence built from its fields, with who knows it. Nothing changes until the DM presses
Use this, which the bot checks again and writes in one change that Undo takes back whole. The
website has no AI key and never writes the story graph: it queues a row (`web_connection_reads`,
migration 0632) through four DM-only doors and reads where it stands in the shared progress shape
(`dmbot.progress`, #2564). The DM's words go in and are blanked by the bot when the read ends; what
comes back is stages, counts, ids, names and the proposals' fixed fields, never the DM's sentence.
"""

from __future__ import annotations

import re
import time
from typing import Any

from dmbot import progress
from dmbot.db import Database
from dmbot.memory import connection_said as said
from dmbot.web.names import _parse
from dmbot.web.recaps import RecapError, _guild_of, _scoped
from dmbot.web.sessions import Session

ID = re.compile(r"^[0-9a-f]{32}$")
WHO = ("everyone", "dm", "some", "keep")
# What a failed job says, as the code the page turns into words (the bot's own list,
# `memory/web_connection_reads`).
REASONS = {
    "not_yours",
    "not_available",
    "not_on_plan",
    "unreadable",
    "nothing",
    "too_long",
    "changed",
    "gone",
    "refused",
    "failed",
}


def _id(raw: object) -> str:
    if not isinstance(raw, str) or not ID.match(raw):
        raise RecapError("not_found")
    return raw


async def _door(
    db: Database, session: Session, ref: str, statement: str, args: tuple[Any, ...]
) -> Any:
    guild_id, campaign_id = _parse(ref)
    _guild_of(session, guild_id)
    async with db.user(session.user_id, session=session.id_hash) as conn:
        await _scoped(conn, guild_id)
        cur = await conn.execute(statement, (guild_id, campaign_id, *args))
        found = await cur.fetchone()
    return found["r"] if found else None


async def request(db: Database, session: Session, ref: str, body: object) -> str:
    """Queue a read of `{a, b, text, edit?}` (the two entries' ids, the DM's words, and the id of
    the connection being corrected) and answer its id. Refusals: `bad_text`, `bad_choice` (the two
    are one entry, or the connection does not join them), `busy` (a read of this campaign is
    running), `hour_full` (ten reads this hour); anyone but a DM of the campaign, or an entry that
    is not there, is `not_found`."""
    if not isinstance(body, dict):
        raise RecapError("bad_choice")
    one, two, text, edit = body.get("a"), body.get("b"), body.get("text"), body.get("edit")
    if not isinstance(text, str):
        raise RecapError("bad_text")
    if edit is not None:
        edit = _id(edit)
    if len(text) > 4 * said.TEXT_MAX or "\x00" in text:
        raise RecapError("bad_text")  # (the door's own limit is on the tidied words)
    got = await _door(
        db,
        session,
        ref,
        "SELECT dmbot_web_connection_read_add(%s, %s, %s, %s, %s, %s) AS r",
        (_id(one), _id(two), edit, text),
    )
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
    return str(got["id"])


def _stage(got: dict[str, Any]) -> str:
    """The shared stage words for where a job stands: waiting, reading (a read), fixing (a use
    or an undo), then done or failed."""
    if got["state"] == "failed":
        return "failed"
    if got["state"] in ("ready", "done", "undone"):
        return "done"
    if got["state"] == "waiting":
        return "waiting"
    return "reading" if got["job"] == "read" else "fixing"


async def status(
    db: Database, session: Session, ref: str, answer: str, *, moment: int | None = None
) -> dict[str, Any]:
    """Where the DM's own read stands: the shared progress shape plus `job`, and once the read is
    ready `proposals` (each `{number, first, second, kind, words, sentence, secret}`), `whoKnows`
    ("everyone", "dm" or "some"), `characters`, `leftOut` (the names that could not be placed),
    and `replacing`. After Use this, `canUndo`. Nobody else's, and never the DM's own sentence."""
    got = await _door(
        db,
        session,
        ref,
        "SELECT dmbot_web_connection_read_get(%s, %s, %s) AS r",
        (_id(answer),),
    )
    if got is None:
        raise RecapError("not_found")
    when = int(time.time()) if moment is None else moment
    reason = str(got["error"]) or None
    shape = progress.shape(
        _stage(got),
        started_at=int(got["createdAt"]),
        updated_at=int(got["updatedAt"]),
        now=when,
        reason=reason if reason in REASONS else ("failed" if reason else None),
    )
    out: dict[str, Any] = {**shape, "job": got["job"], "canUndo": bool(got["canUndo"])}
    if got["state"] == "ready" and got["job"] == "read":
        names = {got["first"]["id"]: got["first"], got["second"]["id"]: got["second"]}
        out["proposals"] = [
            {
                "number": i,
                "first": p["first"],
                "second": p["second"],
                "kind": p["kind"],
                "words": p["words"],
                "secret": bool(p.get("secret", False)),
                "sentence": said.plain_sentence(
                    str(names[p["first"]]["name"]),
                    str(names[p["second"]]["name"]),
                    str(p["kind"]),
                    str(p["words"]),
                    str(names[p["first"]]["kind"]),
                ),
            }
            for i, p in enumerate(got["proposals"], start=1)
            if p["first"] in names and p["second"] in names
        ]
        out.update(
            whoKnows=got["whoKnows"],
            characters=got["characters"],
            leftOut=got["leftOut"],
            replacing=got["replacing"],
        )
    if got["state"] == "undone":
        out["partly"] = bool(got["partly"])
    return out


async def use(db: Database, session: Session, ref: str, answer: str, body: object) -> None:
    """Use this: `{picks: [1, 2], who: "everyone" | "dm" | "some" | "keep", characters: [...]}`,
    queued for the bot, which checks it all again. Refusals: `bad_choice`, `not_ready` (the read is
    not waiting for an answer); anyone but the DM who asked is `not_found`."""
    if not isinstance(body, dict):
        raise RecapError("bad_choice")
    picks, who, chars = body.get("picks"), body.get("who"), body.get("characters", [])
    if (
        not isinstance(picks, list)
        or not all(type(n) is int for n in picks)
        or who not in WHO
        or not isinstance(chars, list)
        or not all(isinstance(c, str) and ID.match(c) for c in chars)
    ):
        raise RecapError("bad_choice")
    got = await _door(
        db,
        session,
        ref,
        "SELECT dmbot_web_connection_use(%s, %s, %s, %s, %s, %s) AS r",
        (_id(answer), picks, who, chars),
    )
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))


async def undo(db: Database, session: Session, ref: str, answer: str) -> None:
    """Take back what Use this wrote, whole. `not_ready` when there is nothing to take back."""
    got = await _door(
        db, session, ref, "SELECT dmbot_web_connection_undo(%s, %s, %s) AS r", (_id(answer),)
    )
    if got is None:
        raise RecapError("not_found")
    if got["status"] != "ok":
        raise RecapError(str(got["status"]))
