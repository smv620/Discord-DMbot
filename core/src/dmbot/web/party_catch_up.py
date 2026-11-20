"""The website's "Catch up" on an entry's page (#4793, party knowledge slice 4b): who is
missing what on this card, and giving one party member what the rest of the party knows there.
Both go through the one queue door like every website action and the bot answers them with the
store's own `tell_choices` and `tell_member`, so the who-knows rule is not copied into SQL; the
queue door itself answers only a DM of the campaign. Ids and counts only: the page names the
members from the party list it already has. No word of the story."""

from __future__ import annotations

import re

from dmbot.db import Database
from dmbot.web import relationships
from dmbot.web.recaps import RecapError
from dmbot.web.sessions import Session

ID = re.compile(r"^[0-9a-f]{32}$")


async def queue_choices(db: Database, session: Session, ref: str, body: object) -> str:
    """Who is missing what on this entry's card: `{"entry": <32 hex>}`. The id of the answer the
    page polls, whose `choices` is `[{"id", "count"}]` in the party list's order."""
    entry = body.get("entry") if isinstance(body, dict) else None
    if not isinstance(entry, str) or not ID.fullmatch(entry):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="party-catch-up-choices", target="", text=entry,
        flag=False,
    )  # fmt: skip


async def queue_tell(db: Database, session: Session, ref: str, body: object) -> str:
    """ "Catch up": `{"member": <32 hex>, "entry": <32 hex>}`. The answer carries `told` (how many
    things) and `batch` (the number its Undo takes, through the party record's undo route)."""
    member = body.get("member") if isinstance(body, dict) else None
    entry = body.get("entry") if isinstance(body, dict) else None
    if not (isinstance(member, str) and isinstance(entry, str)):
        raise RecapError("bad_action")
    if not (ID.fullmatch(member) and ID.fullmatch(entry)):
        raise RecapError("bad_action")
    return await relationships._queue(
        db, session, ref, entity=None, action="party-catch-up", target="",
        text=f"{member} {entry}", flag=False,
    )  # fmt: skip
