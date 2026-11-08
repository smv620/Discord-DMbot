"""Who learned what, as the session reader proposed it, waiting for the DM (#2996, part 2 slice 1,
PR 3 of 4; Supervisor's ruling of 2026-11-06).

A proposal is a row in `memory_reveal_proposals`: one character, one statement DMbot already holds,
how (a check's name, or the teller), the numbers of the lines it came from and the people who said
them. **Ids only, no words**: the reader's words are placed on a stored statement here and then
dropped, so a row never holds a transcript's words or the model's.

Rules held here, not trusted (the parser, `reveal_reading`, held the ones that need no campaign):
- **Only what DMbot holds.** The words are placed on a live statement by the claims' name rules
  (`names_of` for the names, `resolve_relationship` for the relationship, or a catch-all's own
  words): an entry alone, or a confirmed connection between the two named ends. Not found, or more
  than one fits: dropped and counted (`unplaced`), never made: a new fact is the claims' job.
- **The one who learned it is one character**, and so is a teller (never a group or the party).
- **Something to learn.** A statement the character can already see (the gate, `gate.visible`) is
  dropped (`known`): there is nothing to reveal.
- **A teller tells only what they know.** A tell is kept only when the gate shows the teller the
  statement (`teller_ignorant` otherwise). Nothing spreads past the listener.
- **One proposal for a (character, statement) in a session**, whatever the row's status, across
  batches and re-runs: two wordings of one statement are one proposal.
- **Consent.** Storing keeps only rows whose speakers are all recorded at that moment; a waiting
  row goes when any of its speakers stops being recorded (`purge_stopped`, before the DM is shown
  any, and `forget`, run by Stop recording me).
Counts only leave this module.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dmbot.db import Conn
from dmbot.memory import gate
from dmbot.memory.models import CONFIRMED, new_id
from dmbot.memory.reveal_reading import TELL, RevealProposal

if TYPE_CHECKING:
    from dmbot.memory.claims import Names
    from dmbot.memory.ontology import Ontology
    from dmbot.memory.store import MemoryStore

PROPOSED, KEPT, DISMISSED = "proposed", "kept", "dismissed"
ENTRY, CONNECTION = gate.ENTRY, gate.CONNECTION


@dataclass(frozen=True, slots=True)
class Placed:
    """A proposal placed on a stored statement, ids only."""

    character_id: str
    kind: str  # gate.ENTRY or gate.CONNECTION
    statement_id: str
    how: str
    check_name: str
    teller_id: str | None
    lines: tuple[str, ...]


def place(
    reveal: RevealProposal,
    names: Names,
    relations: Sequence[Mapping[str, Any]],
    onto: Ontology,
    counts: Counter[str],
) -> Placed | None:
    """The stored statement the reader's words mean, or None (counted by why). Pure: the names,
    the campaign's confirmed connections and its ontology are given."""
    from dmbot.memory.claims import resolve_relationship  # (claims imports this module's kin)

    who = names.one(reveal.who)
    if isinstance(who, str) or who[1] != "character":
        counts["reveal_not_a_character"] += 1
        return None
    teller_id: str | None = None
    if reveal.how == TELL:
        teller = names.one(reveal.teller)
        if isinstance(teller, str) or teller[1] != "character" or teller[0] == who[0]:
            counts["reveal_not_a_character"] += 1
            return None
        teller_id = teller[0]
    subject = names.one(reveal.subject)
    if isinstance(subject, str):
        counts["reveal_unplaced"] += 1
        return None
    if not reveal.relationship:
        statement_kind, statement_id = ENTRY, subject[0]
    else:
        obj = names.one(reveal.object)
        if isinstance(obj, str):
            counts["reveal_unplaced"] += 1
            return None
        between = [
            r
            for r in relations
            if {r["subject_id"], r["object_id"]} == {subject[0], obj[0]}
            and r["status"] == CONFIRMED
        ]
        term = resolve_relationship(onto, reveal.relationship)
        if term is not None:
            key, backwards = term[0].key, term[1]
            start = obj[0] if backwards else subject[0]
            fits = [
                r
                for r in between
                if r["predicate"] == key
                and (r["subject_id"] == start or not r.get("directional", False))
            ]
        else:  # (words DMbot has no term for: a catch-all connection carrying those words)
            said = reveal.relationship.casefold()
            fits = [r for r in between if str(r.get("detail") or "").casefold() == said]
        if len(fits) != 1:
            counts["reveal_unplaced"] += 1
            return None
        statement_kind, statement_id = CONNECTION, str(fits[0]["id"])
    if statement_id == who[0]:
        counts["reveal_unplaced"] += 1  # (learning that oneself exists)
        return None
    return Placed(
        who[0], statement_kind, statement_id, reveal.how, reveal.check, teller_id, reveal.lines
    )


@dataclass(frozen=True, slots=True)
class Stored:
    """What a storing came to, counts only."""

    stored: int = 0
    counts: Counter[str] | None = None  # why the others were dropped


class RevealProposals:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def store(
        self,
        guild_id: int,
        campaign_id: str,
        proposals: Sequence[tuple[RevealProposal, Sequence[int]]],
        *,
        opted_in: Collection[int],
        session_started_at: int | None,
        prompt_version: str,
        model: str,
    ) -> Stored:
        """Place and keep the proposals (each with the people whose lines it came from). Dropped,
        and counted by why: a speaker not recorded now, words that place on no single stored
        statement, a character who can already see it, a teller who can't, and a (character,
        statement) the session already has a row for."""
        counts: Counter[str] = Counter()
        if not proposals:
            return Stored(0, counts)
        from dmbot.memory._changes import ALIASES, ENTITIES, RELATIONS
        from dmbot.memory.claims import names_of
        from dmbot.memory.store import _load_ontology

        recorded = set(opted_in)
        stored = 0
        async with self._store._read(guild_id, campaign_id) as scope:
            names = names_of(
                await scope.select(ENTITIES, " AND status = %s", [CONFIRMED]),
                await scope.select(ALIASES, " AND status <> 'rejected'"),
            )
            relations = await scope.select(RELATIONS, " AND status = %s", [CONFIRMED])
            onto = await _load_ontology(scope)
            data = await gate.load(scope)
            cur = await scope.conn.execute(
                "SELECT character_id, statement_kind, statement_id FROM memory_reveal_proposals"
                " WHERE guild_id = %s AND campaign_id = %s"
                " AND session_started_at IS NOT DISTINCT FROM %s",
                (*scope.ids, session_started_at),
            )
            have = {
                (str(r["character_id"]), str(r["statement_kind"]), str(r["statement_id"]))
                for r in await cur.fetchall()
            }
            for reveal, speakers in proposals:
                if not speakers or not set(speakers) <= recorded:
                    counts["reveal_not_recorded"] += 1
                    continue
                placed = place(reveal, names, relations, onto, counts)
                if placed is None:
                    continue
                key = (placed.character_id, placed.kind, placed.statement_id)
                if key in have:
                    counts["reveal_repeated"] += 1
                    continue
                learner = gate.visible(gate.Viewer.character(placed.character_id), data)
                if learner.knows(placed.kind, placed.statement_id):
                    counts["reveal_known"] += 1  # (nothing to learn)
                    continue
                if placed.teller_id is not None and not gate.visible(
                    gate.Viewer.character(placed.teller_id), data
                ).knows(placed.kind, placed.statement_id):
                    counts["reveal_teller_ignorant"] += 1
                    continue
                have.add(key)
                await scope.conn.execute(
                    "INSERT INTO memory_reveal_proposals (guild_id, campaign_id, id,"
                    " character_id, statement_kind, statement_id, how, check_name, teller_id,"
                    " lines, speaker_ids, session_started_at, prompt_version, model, status,"
                    " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                    " %s, 'proposed', %s)",
                    (
                        *scope.ids, new_id(), placed.character_id, placed.kind,
                        placed.statement_id, placed.how, placed.check_name, placed.teller_id,
                        list(placed.lines), sorted(set(speakers)), session_started_at,
                        prompt_version[:40], model[:80], int(self._store._clock()),
                    ),
                )  # fmt: skip
                stored += 1
        return Stored(stored, counts)

    async def purge_stopped(
        self, guild_id: int, campaign_id: str, opted_in: Collection[int]
    ) -> int:
        """Delete the proposals (not the ones the DM kept) with a speaker who is not recorded in
        this server now: the consent re-check on the read path, as for claims and deeds."""
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "DELETE FROM memory_reveal_proposals WHERE guild_id = %s AND campaign_id = %s"
                " AND status <> 'kept' AND NOT (speaker_ids <@ %s::bigint[])",
                (*scope.ids, sorted(set(opted_in))),
            )
            return cur.rowcount

    async def count_waiting(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int]
    ) -> int:
        """How many wait, counted without changing anything (rows with a speaker no longer
        recorded are not counted)."""
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT count(*) AS n FROM memory_reveal_proposals WHERE guild_id = %s"
                " AND campaign_id = %s AND status = 'proposed' AND speaker_ids <@ %s::bigint[]",
                (*scope.ids, sorted(set(opted_in))),
            )
            row = await cur.fetchone()
            return 0 if row is None else int(row["n"])


async def forget(conn: Conn, guild_id: int, user_id: int) -> int:
    """Stop recording me: delete, in every campaign of the server, the proposals that cite this
    person's words and were not kept. A kept one is the DM's knowledge row and stays, without this
    person's id or line numbers. Returns how many went."""
    cur = await conn.execute(
        "DELETE FROM memory_reveal_proposals WHERE guild_id = %s AND %s = ANY(speaker_ids)"
        " AND status <> 'kept'",
        (guild_id, user_id),
    )
    gone = cur.rowcount
    await conn.execute(
        "UPDATE memory_reveal_proposals SET speaker_ids = array_remove(speaker_ids, %s),"
        " lines = '{}' WHERE guild_id = %s AND %s = ANY(speaker_ids) AND status = 'kept'",
        (user_id, guild_id, user_id),
    )
    return gone


__all__ = [
    "DISMISSED", "KEPT", "PROPOSED", "Placed", "RevealProposals", "Stored", "forget", "place",
]  # fmt: skip
