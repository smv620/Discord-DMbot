"""Per-server recording consent, stored in Postgres.

Consent is opt-in and per player per server. Core is the source of truth and pushes
the consenting set to ears, which refuses to capture anyone else. A cache keeps the
audio path's `has_consent` check instant.

Revoking must never be undone by a race or a database failure:
- `stop_now` takes the player out of the cache at once, before any database work.
- Each change and the reload that follows it run in one transaction, under a lock per
  server, so an older reload can't overwrite a newer one.
- A revoked player is held back from every reload until the revoke is saved (or they
  give consent again), so a failed save can't quietly bring them back. A grant that was
  already running when the stop came doesn't count as giving consent again.

A pause is "off the record" (#2466): a person, or the whole table, asked not to be recorded
for a while. It is held here beside consent and subtracted from it everywhere (`has_consent`,
`consenting`, `consenting_now`), so the audio allowlist, the transcript, the sidebar, memory and
the recap all see a paused person exactly as one who never agreed, and every re-check after an
await honours it with no extra code. It is kept in memory only: a pause ends with the listening
session (`end_session`), and a restart ends the session too. Consent itself is untouched.

Each consent records the wording it was given under (`TERMS_VERSION`) and how (#35).
Only consent under the current wording counts: when what DMbot does with recordings
changes, the version goes up, and everyone who agreed before is asked again. Until they
say yes again they are not recorded.

While the server sends voices to an outside company (TRANSCRIBER=deepgram or cloud),
a yes also has to name that company's engine (#170): a yes given under local Whisper,
or for another company, doesn't count, and that person is asked again.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from dmbot.db import Conn, Database

# Bump whenever the consent request's wording changes what people agree to
# (consent_dm.request_text; a test pins that wording to this number), and rewrite
# consent_dm.RENEWED to say what changed. Version 2: anyone in the server can read and
# download the transcript (#124, #125, #132). Every yes saved before versions were
# recorded counts as version 1, even the few given under the version-2 wording on the
# test server: we can't tell them apart, so asking again is the safe choice. Version 3:
# DMbot's helpers have an AI company (Anthropic) read the written-down text, with who said
# it, to give the DM notes, not used to train their AI (#52; for every helper, not one
# feature). If a provider or key ever trains on the data, the wording changes and this
# goes up again. Version 4: DMbot's minimum age is 16 and the button confirms it (owner,
# 2026-10-10; #1018): no age or birthdate is stored, the yes under this wording is the
# confirmation. Version 5: DMbot also reads what is typed in the table's chat while it is
# listening (owner, 2026-10-13; #1118), so everyone is asked again. Version 6: it also reads
# what is typed in the campaign's DMbot chat channel, where anyone at the table may ask it a
# question and the answer is posted for everyone (owner, 2026-10-14; #1187).
TERMS_VERSION = 6
# How someone said yes: the button in DMbot's private message, or in the reply to
# /consent give in the server.
ConsentMethod = Literal["private_message", "consent_command"]
PRIVATE_MESSAGE: ConsentMethod = "private_message"
CONSENT_COMMAND: ConsentMethod = "consent_command"


@dataclass(frozen=True, slots=True)
class ConsentStatus:
    granted: dict[int, int]  # user → when they said yes under the current wording
    outdated: set[int]  # said yes only under older wording: ask again
    # Said yes under the current wording, but not for the outside company in use now
    # (or under local Whisper): ask again (#170).
    other_company: set[int] = field(default_factory=set)

    @property
    def ask_again(self) -> set[int]:
        return self.outdated | self.other_company


class ConsentStore:
    def __init__(
        self,
        db: Database,
        *,
        outside: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        """`outside`: the outside engine (deepgram, cloud) the server sends voices to, or
        None for local Whisper. Then only yeses given for that engine count. `clock`: what a
        pause counts its minutes on (monotonic seconds)."""
        self._db = db
        self._clock = clock
        self._wall = wall  # what a saved pause counts its end on (it must outlive a restart)
        # Off the record (#2466): server → person → when it ends (None: until they resume).
        self._paused: dict[int, dict[int, float | None]] = {}
        # The whole table, paused by a DM: server → (ends at or None) while it is on.
        self._table_paused: dict[int, tuple[float | None]] = {}
        self._outside = outside
        self._cache: dict[int, frozenset[int]] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        # Revoked here but not (yet) saved: never trust the database for these.
        self._held_back: dict[int, set[int]] = {}
        # How many stops each player has had, so a grant that was already running when a
        # stop arrived can't undo that stop.
        self._stops: dict[tuple[int, int], int] = {}

    @property
    def outside(self) -> str | None:
        return self._outside

    @outside.setter
    def outside(self, value: str | None) -> None:
        """Changing which yeses count drops the cache, so nothing stale is trusted."""
        if value != self._outside:
            self._outside = value
            self._cache.clear()

    async def grant(
        self,
        guild_id: int,
        user_id: int,
        *,
        method: ConsentMethod = PRIVATE_MESSAGE,
        outside_to: str | None = None,
    ) -> frozenset[int]:
        """Save consent under the current wording. `method`: how they said yes.
        `outside_to`: the outside engine the request they agreed to named, if any."""
        if method not in (PRIVATE_MESSAGE, CONSENT_COMMAND):
            raise ValueError(f"Unknown consent method: {method!r}")
        stops_before = self._stops.get((guild_id, user_id), 0)
        async with self._lock(guild_id):
            async with self._db.guild(guild_id) as conn:
                await conn.execute(
                    "INSERT INTO consent"
                    " (guild_id, user_id, granted_at, terms_version, method, outside_to)"
                    " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (guild_id, user_id)"
                    " DO UPDATE SET granted_at = EXCLUDED.granted_at,"
                    " terms_version = EXCLUDED.terms_version, method = EXCLUDED.method,"
                    " outside_to = EXCLUDED.outside_to",
                    (guild_id, user_id, int(time.time()), TERMS_VERSION, method, outside_to),
                )
                users = await _select(conn, guild_id, self._outside)
            if self._stops.get((guild_id, user_id), 0) == stops_before:
                self._held_back.get(guild_id, set()).discard(user_id)
            # Otherwise a stop arrived while saving: it wins, and its revoke follows.
            return self._store(guild_id, users)

    def stop_now(self, guild_id: int, user_id: int) -> None:
        """Stop trusting this player's consent immediately, without the database."""
        key = (guild_id, user_id)
        self._stops[key] = self._stops.get(key, 0) + 1
        self._held_back.setdefault(guild_id, set()).add(user_id)
        self._paused.get(guild_id, {}).pop(user_id, None)  # a stop is final: no pause carries over
        if guild_id in self._cache:
            self._cache[guild_id] = self._cache[guild_id] - {user_id}

    async def revoke(
        self,
        guild_id: int,
        user_id: int,
        *,
        in_transaction: Callable[[Conn], Awaitable[None]] | None = None,
    ) -> bool:
        """Revoke and save; True if a saved consent was removed.

        If saving fails, the player stays stopped in this process. `in_transaction` runs in
        the same transaction as the delete, only when a row was removed: the website sends its
        notification to the bot there, so it is delivered when the delete commits and never
        without it (#1151).
        """
        self.stop_now(guild_id, user_id)
        async with self._lock(guild_id):
            async with self._db.guild(guild_id) as conn:
                cur = await conn.execute(
                    "DELETE FROM consent WHERE guild_id = %s AND user_id = %s RETURNING user_id",
                    (guild_id, user_id),
                )
                removed = await cur.fetchone() is not None
                if removed and in_transaction is not None:
                    await in_transaction(conn)
                users = await _select(conn, guild_id, self._outside)
            self._held_back[guild_id].discard(user_id)
            self._store(guild_id, users)
            return removed

    def cached_guilds(self) -> list[int]:
        """Servers whose consent is held in memory."""
        return list(self._cache)

    async def stored(self, guild_id: int) -> frozenset[int]:
        """Who the database says consents here now, without touching the cache: the bot compares
        it with what it holds, so it can stop (and tell the DM about) whoever is gone (#1151)."""
        async with self._db.guild(guild_id) as conn:
            return await _select(conn, guild_id, self._outside)

    def consenting_now(self, guild_id: int) -> frozenset[int]:
        """Who the cache says is consenting here and is not off the record, without reading the
        database."""
        return self._recordable(guild_id, self._cache.get(guild_id, frozenset()))

    async def consenting(self, guild_id: int) -> frozenset[int]:
        """Who may be recorded now: those who agreed, minus anyone off the record."""
        return self._recordable(guild_id, await self._agreed(guild_id))

    async def _agreed(self, guild_id: int) -> frozenset[int]:
        if guild_id not in self._cache:
            async with self._lock(guild_id):
                if guild_id not in self._cache:
                    async with self._db.guild(guild_id) as conn:
                        users = await _select(conn, guild_id, self._outside)
                    self._store(guild_id, users)
        return self._cache[guild_id]

    # ---- off the record (#2466) -----------------------------------------------------------

    def _recordable(self, guild_id: int, agreed: frozenset[int]) -> frozenset[int]:
        if self.table_paused(guild_id):
            return frozenset()
        gone = {u for u in self._paused.get(guild_id, {}) if self.is_paused(guild_id, u)}
        return agreed - gone if gone else agreed

    def _ends(self, seconds: float | None) -> float | None:
        return None if seconds is None else self._clock() + seconds

    def _live(self, ends: float | None) -> bool:
        return ends is None or ends > self._clock()

    def pause(self, guild_id: int, user_id: int, seconds: float | None) -> None:
        """Take this person off the record at once, for `seconds` (None: until they resume).
        Only someone who agreed can be paused; a pause never grants anything."""
        if user_id in self._cache.get(guild_id, frozenset()):
            self._paused.setdefault(guild_id, {})[user_id] = self._ends(seconds)

    def resume(self, guild_id: int, user_id: int) -> bool:
        """Back on the record; True if they were paused (and it had not already run out)."""
        was = self.is_paused(guild_id, user_id)
        self._paused.get(guild_id, {}).pop(user_id, None)
        return was

    def is_paused(self, guild_id: int, user_id: int) -> bool:
        """Off the record now, by their own pause. (An ended pause is dropped as it is seen.)"""
        held = self._paused.get(guild_id)
        if held is None or user_id not in held:
            return False
        if self._live(held[user_id]):
            return True
        del held[user_id]
        return False

    def paused_until(self, guild_id: int, user_id: int) -> float | None:
        """When their pause ends, on this store's clock; None if it lasts until they resume."""
        return self._paused.get(guild_id, {}).get(user_id)

    def pause_table(self, guild_id: int, seconds: float | None) -> None:
        """The DM's pause: nobody is recorded until it ends."""
        self._table_paused[guild_id] = (self._ends(seconds),)

    def resume_table(self, guild_id: int) -> bool:
        return self._table_paused.pop(guild_id, None) is not None

    def table_paused(self, guild_id: int) -> bool:
        held = self._table_paused.get(guild_id)
        if held is None:
            return False
        if self._live(held[0]):
            return True
        self._table_paused.pop(guild_id, None)
        return False

    def paused_people(self, guild_id: int) -> list[int]:
        return [u for u in list(self._paused.get(guild_id, {})) if self.is_paused(guild_id, u)]

    async def agreed(self, guild_id: int) -> frozenset[int]:
        """Everyone who agreed here, paused or not (loaded if need be). For the checks that ask
        "did they agree?", not "is DMbot recording them now?": a pause is not a refusal."""
        return await self._agreed(guild_id)

    def snapshot(self, guild_id: int) -> dict[str, Any]:
        """The pauses of this server, to save with its session: when each ends in Unix seconds
        (None: until resumed). People who are not paused any more are left out."""
        now_c, now_w = self._clock(), self._wall()

        def end(ends: float | None) -> float | None:
            return None if ends is None else round(now_w + (ends - now_c))

        out: dict[str, Any] = {}
        people = {str(u): end(self._paused[guild_id][u]) for u in self.paused_people(guild_id)}
        if people:
            out["people"] = people
        if self.table_paused(guild_id):
            out["table"] = end(self._table_paused[guild_id][0])
        return out

    def restore(self, guild_id: int, saved: dict[str, Any]) -> list[int]:
        """Put saved pauses back after a restart, before anyone's audio is let in. A pause whose
        time ran out meanwhile is not restored. Returns the people (0: the table) whose pause is
        back, with their time left in `remaining`."""
        back: list[int] = []
        now_c, now_w = self._clock(), self._wall()

        def left(end: object) -> tuple[bool, float | None]:
            if end is None:
                return True, None
            if isinstance(end, int | float) and end > now_w:
                return True, float(end) - now_w
            return False, None

        for user, end in dict(saved.get("people") or {}).items():
            ok, seconds = left(end)
            if ok and int(user) in self._cache.get(guild_id, frozenset()):
                self._paused.setdefault(guild_id, {})[int(user)] = (
                    None if seconds is None else now_c + seconds
                )
                back.append(int(user))
        if "table" in saved:
            ok, seconds = left(saved["table"])
            if ok:
                self._table_paused[guild_id] = (None if seconds is None else now_c + seconds,)
                back.append(0)
        return back

    def end_session(self, guild_id: int) -> None:
        """The listening session is over: every pause ends with it."""
        self._paused.pop(guild_id, None)
        self._table_paused.pop(guild_id, None)

    def agreed_set(self, guild_id: int) -> frozenset[int]:
        """Everyone who agreed here, paused or not."""
        return self._cache.get(guild_id, frozenset())

    def agreed_now(self, guild_id: int, user_id: int) -> bool:
        """Whether they agreed, paused or not (for what is not recording: a question to DMbot)."""
        return user_id in self._cache.get(guild_id, frozenset())

    async def status(self, guild_id: int, user_ids: Iterable[int]) -> ConsentStatus:
        """For these players, in one query: when each said yes under the current wording,
        and who said yes only under older wording (they're asked again, with a note).

        Players whose revoke isn't saved yet count as neither. A row with a *newer*
        version (only after rolling DMbot back) also counts as neither: they're simply
        asked again.
        """
        held = self._held_back.get(guild_id, set())
        ids = [u for u in set(user_ids) if u not in held]
        if not ids:
            return ConsentStatus({}, set())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "SELECT user_id, granted_at, terms_version, outside_to FROM consent"
                " WHERE guild_id = %s AND user_id = ANY(%s)",
                (guild_id, ids),
            )
            rows = await cur.fetchall()
        # A yes under the current wording that names another company (or none, while an
        # outside engine is in use) is neither granted nor outdated: they simply get
        # the question again, which names the company now in use.
        granted = {
            int(r["user_id"]): int(r["granted_at"])
            for r in rows
            if r["terms_version"] == TERMS_VERSION
            and (self._outside is None or r["outside_to"] == self._outside)
        }
        outdated = {int(r["user_id"]) for r in rows if r["terms_version"] < TERMS_VERSION}
        other_company = {
            int(r["user_id"])
            for r in rows
            if r["terms_version"] == TERMS_VERSION and int(r["user_id"]) not in granted
        }
        return ConsentStatus(granted, outdated, other_company)

    async def granted_times(self, guild_id: int, user_ids: Iterable[int]) -> dict[int, int]:
        """When each of these players said yes here under the current wording (Unix
        seconds); absent if they haven't. Players whose revoke isn't saved are absent."""
        return (await self.status(guild_id, user_ids)).granted

    async def outdated(self, guild_id: int, user_ids: Iterable[int]) -> set[int]:
        """Which of these players said yes only under older wording."""
        return (await self.status(guild_id, user_ids)).outdated

    async def granted_at(self, guild_id: int, user_id: int) -> int | None:
        """When this player consented here (Unix seconds), or None if they haven't."""
        return (await self.granted_times(guild_id, [user_id])).get(user_id)

    def has_consent(self, guild_id: int, user_id: int) -> bool:
        """Fast, synchronous check for the audio path. Unknown servers deny, and so does anyone
        off the record."""
        return user_id in self.consenting_now(guild_id)

    def _lock(self, guild_id: int) -> asyncio.Lock:
        return self._locks.setdefault(guild_id, asyncio.Lock())

    def _store(self, guild_id: int, users: frozenset[int]) -> frozenset[int]:
        users = users - self._held_back.get(guild_id, set())
        self._cache[guild_id] = users
        return users


async def _select(conn: Conn, guild_id: int, outside: str | None) -> frozenset[int]:
    cur = await conn.execute(
        "SELECT user_id FROM consent WHERE guild_id = %s AND terms_version = %s"
        " AND (%s::text IS NULL OR outside_to = %s)",
        (guild_id, TERMS_VERSION, outside, outside),
    )
    return frozenset(int(r["user_id"]) for r in await cur.fetchall())
