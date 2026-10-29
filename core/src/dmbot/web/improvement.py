"""`GET /me/improvement` and `PUT /me/improvement` (#1609): the signed-in person's choice about
having their words used to improve DMbot. Body and answer: `{"improve": true | false}` (true,
the default, means DMbot may use their words). Only their own choice, ever: the route takes no
person, and the table's row-level security holds it to the signed-in one."""

from __future__ import annotations

from typing import Any

from dmbot.db import Database
from dmbot.transcript.improvement import OptOutStore
from dmbot.web.sessions import Session


async def read_choice(db: Database, session: Session) -> dict[str, Any]:
    out = await OptOutStore(db).is_opted_out(session.user_id)
    return {"improve": not out}


async def save_choice(db: Database, session: Session, improve: bool) -> dict[str, Any]:
    await OptOutStore(db).set(session.user_id, improve=improve)
    return {"improve": improve}
