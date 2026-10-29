"""The improvement opt-out (#1609; docs/PLAN.md, "Data, ownership and deletion"): a person may
choose, on the website, that nothing they said or typed is used to improve DMbot. Running their
table is unaffected (their lines are still transcribed, cleaned and shown), and nothing is
deleted by the choice: Delete my transcripts stays the way to remove lines.

This is the one place that knows the choice. **Every path that takes people's words out of the
tables for anything but running a table asks here, at the time it runs** (never from a copy made
earlier): `allowed()` for a set of people, `filter_lines()` for lines. The paths found, and what
each does (the list is also in the pull request that added this):

- the live test recorder (`test_recording`; bot.py `_save_test_audio`): an opted-out speaker is
  not saved, and what was already saved of them in the running session is dropped;
- `devtools.recap_run --session` (a stored session read for a test run): their lines are left out;
- the test recorder also asks again when a session ends, for everyone saved in it, and drops
  anyone who opted out meanwhile; it will not start at all without this store;
- the replay tools (`test_library run`, `session_replay`, `recheck`, `stt_bakeoff`) read kept
  recordings and the operator's own audio. Their speakers are made-up numbers by design
  (nothing names a person), so a later opt-out CANNOT find someone in a recording already kept:
  they are covered at the source (the recorder) only. That is a decision for Supervisor and the
  owner, recorded in the pull request; `library.delete_person` removes a person by voice code.

A path that can read the choice must refuse to run rather than read around it; `filter_lines`
refuses a line that does not say whose it is.

The choice is stored per Discord account, across servers, in `improvement_opt_outs` (migration
0110); a row means opted out. It is read one person at a time through `Database.user()`, so
the table's row-level security never shows one person's choice to another.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Protocol

from dmbot.db import Database

CACHE_S = 20  # how long the live recorder trusts a person's last answer. Each process has its
# own cache, so a choice made on the website reaches the bot's recorder within this long.
MAX_SEEN = 1000


class HasUser(Protocol):
    user_id: int


class OptOutStore:
    """Reads and writes the choice. `clock` is for tests."""

    def __init__(self, db: Database, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._db = db
        self._now = clock
        self._seen: dict[int, tuple[bool, float]] = {}  # person -> (opted out, read at)

    async def is_opted_out(self, user_id: int) -> bool:
        """A fresh read of one person's choice."""
        async with self._db.user(user_id) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM improvement_opt_outs WHERE user_id = %s", (user_id,)
            )
            out = await cur.fetchone() is not None
        self._remember(user_id, out)
        return out

    def _remember(self, user_id: int, out: bool) -> None:
        if len(self._seen) >= MAX_SEEN:  # bounded: the oldest answers are simply read again
            self._seen.clear()
        self._seen[user_id] = (out, self._now())

    async def opted_out(self, user_ids: Iterable[int]) -> set[int]:
        """Which of these people have opted out, read now (one small read each)."""
        return {u for u in set(user_ids) if await self.is_opted_out(u)}

    async def allowed(self, user_ids: Iterable[int]) -> set[int]:
        """The people whose words may be used to improve DMbot: everyone but the opted out."""
        wanted = set(user_ids)
        return wanted - await self.opted_out(wanted)

    async def is_opted_out_recently(self, user_id: int) -> bool:
        """For the live recorder, which asks for every utterance: the last read, if it is
        under CACHE_S old. A person who opts out is skipped within that long."""
        seen = self._seen.get(user_id)
        if seen is not None and self._now() - seen[1] < CACHE_S:
            return seen[0]
        return await self.is_opted_out(user_id)

    async def set(self, user_id: int, *, improve: bool) -> bool:
        """Save the choice (`improve` False is the opt-out). Returns `improve`."""
        async with self._db.user(user_id) as conn:
            if improve:
                await conn.execute(
                    "DELETE FROM improvement_opt_outs WHERE user_id = %s", (user_id,)
                )
            else:
                await conn.execute(
                    "INSERT INTO improvement_opt_outs (user_id, opted_out_at) VALUES (%s, %s)"
                    " ON CONFLICT (user_id) DO NOTHING",
                    (user_id, int(self._now())),
                )
        self._remember(user_id, not improve)
        return improve


async def allowed(db: Database, user_ids: Iterable[int]) -> set[int]:
    """The shared function the exports call: of these people, who has not opted out (read now)."""
    return await OptOutStore(db).allowed(user_ids)


async def filter_lines(db: Database, lines: Sequence[Any]) -> list[Any]:
    """The lines (anything with a `user_id`) of people who have not opted out, in order. Lines
    with no person (a narrator's note) are kept: they are nobody's words."""
    # (a line with no `user_id` attribute at all raises: it can't say whose it is)
    people = {int(line.user_id) for line in lines if line.user_id is not None}
    keep = await allowed(db, people)
    return [line for line in lines if line.user_id is None or int(line.user_id) in keep]
