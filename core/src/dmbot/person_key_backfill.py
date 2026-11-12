"""Give the trial and email rows written before the secret was set their keyed hash (#4145).

A bot job, run at start-up and every few hours: it asks the database for the Discord IDs that have a
row with no key (`dmbot_person_key_todo`, in pages by ID), hashes them in Python
(`dmbot.person_key`) and hands the pairs back (`dmbot_person_key_fill`). It does nothing while no
secret is set. Counts only in the log: no ID, key or address.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from dmbot import person_key
from dmbot.db import Database

log = logging.getLogger(__name__)

BATCH = 500  # IDs in one round trip (the doors accept up to 1,000)
MAX_PAGES = 200  # a run stops after this many pages (100,000 people), the next run goes on


@dataclass(frozen=True, slots=True)
class Result:
    people: int  # IDs that had a row with no key
    rows: int  # rows given a key
    complete: bool  # False: the run stopped at its page limit


async def run(db: Database, *, batch: int = BATCH, max_pages: int = MAX_PAGES) -> Result:
    """One pass. Nothing is written while no secret is configured."""
    people = rows = 0
    after = 0
    for _ in range(max_pages):
        async with db.unscoped() as conn:
            cur = await conn.execute("SELECT dmbot_person_key_todo(%s, %s) AS ids", (after, batch))
            found = list((await cur.fetchone() or {}).get("ids") or [])
        if not found:
            return Result(people, rows, True)
        keys = [person_key.key_for(int(user_id)) for user_id in found]
        if any(key is None for key in keys):
            return Result(people, rows, True)  # (no secret set, or an ID that is not one)
        async with db.unscoped() as conn:
            cur = await conn.execute("SELECT dmbot_person_key_fill(%s, %s) AS n", (found, keys))
            rows += int((await cur.fetchone() or {}).get("n") or 0)
        people += len(found)
        after = int(found[-1])
    return Result(people, rows, False)


async def run_logged(db: Database) -> None:
    """`run`, with one line of counts in the log when anything was done."""
    result = await run(db)
    if result.people:
        log.info(
            "Person keys: %d people, %d rows given a key%s",
            result.people,
            result.rows,
            "" if result.complete else " (more next time)",
        )
