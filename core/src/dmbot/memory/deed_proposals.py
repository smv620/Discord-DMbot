"""Deeds the session reader proposed, waiting for the DM to record them (#3175 slice 4).

A proposal is a row in `memory_deed_proposals`: who did it, what, whom it mattered to and how it
landed, with the numbers of the lines it came from and the people who said them. It is the
model's short telling and the names as said, so it is DM-only: never a transcript line (only line
numbers are kept), never on a player's screen, never sent to the AI for anything else.

Rules held here, not trusted:
- **Nothing is recorded by reading.** `record` is the DM's press and calls `add_deed` as the DM,
  once, through the store (every rule runs as for the DM's own button, one Undo). `dismiss` marks it
  turned down. Both rows stay, so a pair is never asked about twice in a session.
- **Consent.** Storing keeps only rows whose speakers are all recorded at that moment, a row goes
  when any of its speakers stops being recorded (`purge_stopped`, run before the DM is shown any,
  and `forget`, run by Stop recording me), and the card checks again on every press.
- **One deed for a pair per session, across re-runs.** Storing drops a (doer, viewer) pair the
  session already has a row for, whatever that row's status.
Counts only leave this module; the words are never logged.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dmbot.db import Conn
from dmbot.memory.deed_reading import DeedProposal
from dmbot.memory.models import DM, MemoryRuleError, new_id

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore

PROPOSED, RECORDED, DISMISSED = "proposed", "recorded", "dismissed"
GONE = "That deed is no longer waiting."
NOT_RECORDED_NOW = "Someone whose words it came from is no longer being recorded."
WAITING_MAX = 50  # one page for the DM; far more than anyone answers in a sitting


@dataclass(frozen=True, slots=True)
class Stored:
    """What a storing came to, counts only."""

    stored: int = 0
    repeated: int = 0  # a pair the session already has a row for (any status)
    not_recorded: int = 0  # a speaker stopped before it was stored


@dataclass(frozen=True, slots=True)
class Waiting:
    """A proposal as the DM sees it (DM-only words)."""

    id: str
    doer: str
    text: str
    viewers: tuple[str, ...]
    effect: str
    session_started_at: int | None
    kind: str = ""  # what kind of deed (#3236): one of `DEED_KINDS`, or empty


def pair_key(doer: str, viewer: str) -> tuple[str, str]:
    """A (doer, viewer) pair as the parser keys it: the party however written, then casefolded."""
    folded = doer.casefold()
    return ("the party" if folded in ("party", "the party") else folded, viewer.casefold())


class DeedProposals:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def store(
        self,
        guild_id: int,
        campaign_id: str,
        proposals: Sequence[tuple[DeedProposal, Sequence[int]]],
        *,
        opted_in: Collection[int],
        session_started_at: int | None,
        prompt_version: str,
        model: str,
    ) -> Stored:
        """Keep the proposals (each with the people whose lines it came from). A row whose speakers
        are not all recorded now is not kept; a pair the session already has a row for is dropped
        from a later one, and a deed left with nobody is not stored. (A session is told apart by
        its start time; sessions without one share a scope, which only ever holds back a repeat.
        Two extractions of one session are not expected to overlap; if they did, a pair could
        be stored twice, which the DM would simply answer twice.)"""
        if not proposals:
            return Stored()
        recorded = set(opted_in)
        async with self._store._read(guild_id, campaign_id) as scope:
            have = await scope.conn.execute(
                "SELECT doer, viewers FROM memory_deed_proposals WHERE guild_id = %s"
                " AND campaign_id = %s AND session_started_at IS NOT DISTINCT FROM %s",
                (*scope.ids, session_started_at),
            )
            covered = {
                pair_key(str(r["doer"]), str(v))
                for r in await have.fetchall()
                for v in r["viewers"]
            }
            stored = repeated = stopped = 0
            for deed, speakers in proposals:
                if not speakers or not set(speakers) <= recorded:
                    stopped += 1
                    continue
                fresh = [v for v in deed.viewers if pair_key(deed.doer, v) not in covered]
                if len(fresh) < len(deed.viewers):
                    repeated += 1
                if not fresh:
                    continue
                covered |= {pair_key(deed.doer, v) for v in fresh}
                await scope.conn.execute(
                    "INSERT INTO memory_deed_proposals (guild_id, campaign_id, id, doer, text,"
                    " viewers, effect, lines, speaker_ids, session_started_at, prompt_version,"
                    " model, kind, status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s,"
                    " %s, %s, %s, %s, %s, 'proposed', %s)",
                    (
                        *scope.ids, new_id(), deed.doer, deed.text, fresh, deed.effect,
                        list(deed.lines), sorted(set(speakers)), session_started_at,
                        prompt_version[:40], model[:80], deed.kind, int(self._store._clock()),
                    ),
                )  # fmt: skip
                stored += 1
        return Stored(stored, repeated, stopped)

    async def purge_stopped(
        self, guild_id: int, campaign_id: str, opted_in: Collection[int]
    ) -> int:
        """Delete the proposals (not the ones the DM recorded) with a speaker who is not recorded
        in this server now: the consent re-check on the read path, as for claims."""
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "DELETE FROM memory_deed_proposals WHERE guild_id = %s AND campaign_id = %s"
                " AND status <> 'recorded' AND NOT (speaker_ids <@ %s::bigint[])",
                (*scope.ids, sorted(set(opted_in))),
            )
            return cur.rowcount

    async def waiting(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int]
    ) -> list[Waiting]:
        """The proposals waiting for the DM, oldest first, after the consent re-check."""
        await self.purge_stopped(guild_id, campaign_id, opted_in)
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT id, doer, text, viewers, effect, session_started_at, kind"
                " FROM memory_deed_proposals WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'proposed' ORDER BY created_at, id LIMIT %s",
                (*scope.ids, WAITING_MAX),
            )
            return [
                Waiting(
                    str(r["id"]),
                    str(r["doer"]),
                    str(r["text"]),
                    tuple(r["viewers"]),
                    str(r["effect"]),
                    r["session_started_at"],
                    str(r["kind"]),
                )
                for r in await cur.fetchall()
            ]

    async def count_waiting(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int]
    ) -> int:
        """How many wait, counted without changing anything (rows with a speaker no longer
        recorded are not counted)."""
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT count(*) AS n FROM memory_deed_proposals WHERE guild_id = %s"
                " AND campaign_id = %s AND status = 'proposed' AND speaker_ids <@ %s::bigint[]",
                (*scope.ids, sorted(set(opted_in))),
            )
            row = await cur.fetchone()
            return 0 if row is None else int(row["n"])

    async def dismiss(
        self, guild_id: int, campaign_id: str, proposal_id: str, *, source: str
    ) -> None:
        """Not this: turned down, and the pair is not asked about again this session."""
        if source != DM:
            raise MemoryRuleError("Only the DM can answer this.")
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "UPDATE memory_deed_proposals SET status = 'dismissed', decided_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND status = 'proposed'",
                (int(self._store._clock()), *scope.ids, proposal_id),
            )
            if cur.rowcount == 0:
                raise MemoryRuleError(GONE)

    async def record(
        self,
        guild_id: int,
        campaign_id: str,
        proposal_id: str,
        *,
        opted_in: Collection[int],
        source: str,
    ) -> int | None:
        """Yes, record it: the DM's own `add_deed`, once, then the row is marked recorded. Refused
        when a speaker has stopped being recorded (the row is then gone), when a name is not one
        DMbot knows, or when the store's rules on deeds say no. Returns the change batch (one
        Undo)."""
        from dmbot.memory.claims import names_of

        if source != DM:
            raise MemoryRuleError("Only the DM can record a deed.")
        await self.purge_stopped(guild_id, campaign_id, opted_in)
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT doer, text, viewers, effect, session_started_at, kind"
                " FROM memory_deed_proposals"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND status = 'proposed'",
                (*scope.ids, proposal_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise MemoryRuleError(GONE)
            from dmbot.memory._changes import ALIASES, ENTITIES

            names = names_of(
                await scope.select(ENTITIES, " AND status IN ('proposed', 'confirmed')"),
                await scope.select(ALIASES, " AND status <> 'rejected'"),
            )
        doer = str(row["doer"])
        is_party = pair_key(doer, "")[0] == "the party"
        doer_id = ""
        if not is_party:
            found = names.one(doer)
            if isinstance(found, str):
                raise MemoryRuleError(f"DMbot doesn't know who “{doer}” is yet. {found}.")
            doer_id = found[0]
        viewer_ids: list[str] = []
        for words in row["viewers"]:
            seen = names.one(str(words))
            if isinstance(seen, str):
                raise MemoryRuleError(f"DMbot doesn't know who “{words}” is yet. {seen}.")
            viewer_ids.append(seen[0])
        # Claimed first, in one statement, so two presses (or a press and a retry) can never record
        # it twice: the one that finds it still waiting, with every speaker still recorded, wins.
        async with self._store._read(guild_id, campaign_id) as scope:
            claimed = await scope.conn.execute(
                "UPDATE memory_deed_proposals SET status = 'recorded', decided_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND status = 'proposed'"
                " AND speaker_ids <@ %s::bigint[]",
                (int(self._store._clock()), *scope.ids, proposal_id, sorted(set(opted_in))),
            )
            if claimed.rowcount == 0:
                raise MemoryRuleError(GONE)
        try:
            # The party is the standing party (`ensure_party`), the one `add_deed` takes as a doer:
            # made for the DM the first time it is needed (after the names are known to be right),
            # never by the reader.
            if is_party:
                doer_id = (await self._store.ensure_party(guild_id, campaign_id, source=source)).id
            written = await self._store.add_deed(
                guild_id, campaign_id, doer_id, str(row["text"]), viewer_ids, str(row["effect"]),
                source=source, from_session_at=row["session_started_at"], kind=str(row["kind"]),
            )  # fmt: skip
        except BaseException:
            # The store said no (or failed): it waits again, so the DM can fix it and press again.
            async with self._store._read(guild_id, campaign_id) as scope:
                await scope.conn.execute(
                    "UPDATE memory_deed_proposals SET status = 'proposed', decided_at = NULL"
                    " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                    (*scope.ids, proposal_id),
                )
            raise
        return written.batch


async def forget(conn: Conn, guild_id: int, user_id: int) -> int:
    """Stop recording me: delete, in every campaign of the server, the proposals that cite this
    person's words and were not recorded. A deed the DM recorded is the DM's and stays, without
    this person's id or line numbers. Returns how many went."""
    cur = await conn.execute(
        "DELETE FROM memory_deed_proposals WHERE guild_id = %s AND %s = ANY(speaker_ids)"
        " AND status <> 'recorded'",
        (guild_id, user_id),
    )
    gone = cur.rowcount
    await conn.execute(
        "UPDATE memory_deed_proposals SET speaker_ids = array_remove(speaker_ids, %s),"
        " lines = '{}' WHERE guild_id = %s AND %s = ANY(speaker_ids) AND status = 'recorded'",
        (user_id, guild_id, user_id),
    )
    return gone
