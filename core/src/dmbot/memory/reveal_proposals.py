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
- **Something to learn.** A statement the character already holds as sure (the gate,
  `gate.visible`) is dropped (`known`): there is nothing to reveal. One they hold only as a guess
  (below 1.0) can still be proposed: keeping it makes it sure (Supervisor's ruling on #2996).
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

import logging
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dmbot.db import Conn
from dmbot.memory import gate
from dmbot.memory.link_words import reads_both_ways
from dmbot.memory.models import CONFIRMED, DM, KNOWN, MemoryRuleError, name_key, new_id
from dmbot.memory.ontology import FALLBACK_PREDICATE
from dmbot.memory.reveal_reading import TELL, RevealProposal

if TYPE_CHECKING:
    from dmbot.memory.claims import Names
    from dmbot.memory.ontology import Ontology
    from dmbot.memory.store import MemoryStore

log = logging.getLogger(__name__)

PROPOSED, KEPT, DISMISSED = "proposed", "kept", "dismissed"
ENTRY, CONNECTION = gate.ENTRY, gate.CONNECTION
GONE = "That one is no longer waiting."
STATEMENT_GONE = (
    "That character or what they learned is no longer in DMbot's memory, so there is nothing "
    "to keep."
)
ONLY_THE_DM = "Only the DM can answer this."
WAITING_MAX = 50  # one page for the DM; far more than anyone answers in a sitting


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
    # (imported here: claims imports the store, which this module's callers already hold)
    from dmbot.memory.claims import _phrase, feeling_of, resolve_relationship

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
    secret = {name_key(reveal.subject), name_key(reveal.object)} & names.secret
    if isinstance(subject, str) or secret:
        # (a secret other name is a statement of its own, not the entry: until learning one by a
        # reveal is built, it is counted, never placed as "learned that the entry exists")
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
        # Which connection the words mean, and from which end they read. A feeling between two
        # characters is `knows` with its word (#3175); a relationship DMbot has a term for is that
        # term (said from the object's side for an inverse wording); anything else can only be a
        # catch-all connection carrying those same words.
        feeling = feeling_of(onto, reveal.relationship, "", (subject[1], obj[1]))
        term = None if feeling is not None else resolve_relationship(onto, reveal.relationship)
        if feeling is not None:
            key, backwards, words = feeling[0].key, False, _phrase(feeling[1])
        elif term is not None:
            key, backwards, words = term[0].key, term[1], ""
        else:
            key, backwards, words = FALLBACK_PREDICATE, False, _phrase(reveal.relationship)
        start = obj[0] if backwards else subject[0]
        fits = [
            r
            for r in between
            if r["predicate"] == key
            and (not words or _phrase(str(r.get("detail") or "")) == words)
            # (a one-way connection said from the wrong end is a different statement)
            and (
                r["subject_id"] == start
                or reads_both_ways(
                    str(r["predicate"]),
                    str(r.get("detail") or ""),
                    bool(r.get("directional")),
                    r.get("both_ways"),  # (the DM's direction for it, #4154)
                )
            )
        ]
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
    counts: Counter[str] = field(default_factory=Counter)  # why the others were dropped


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

        # (the store's own ontology reader; claims uses it the same way)
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
                sure = learner.how_sure(placed.kind, placed.statement_id)
                # (a guess below sure can still be confirmed: the DM's Keep makes it sure,
                # Supervisor's ruling on #2996; known without a certainty is known outright)
                if learner.knows(placed.kind, placed.statement_id) and (
                    sure is None or sure >= KNOWN
                ):
                    counts["reveal_known"] += 1  # (nothing to learn)
                    continue
                if placed.teller_id is not None and not gate.visible(
                    gate.Viewer.character(placed.teller_id), data
                ).knows(placed.kind, placed.statement_id):
                    counts["reveal_teller_ignorant"] += 1
                    continue
                have.add(key)
                cur = await scope.conn.execute(
                    "INSERT INTO memory_reveal_proposals (guild_id, campaign_id, id,"
                    " character_id, statement_kind, statement_id, how, check_name, teller_id,"
                    " lines, speaker_ids, session_started_at, prompt_version, model, status,"
                    " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
                    " %s, 'proposed', %s) ON CONFLICT DO NOTHING",
                    (
                        *scope.ids, new_id(), placed.character_id, placed.kind,
                        placed.statement_id, placed.how, placed.check_name, placed.teller_id,
                        list(placed.lines), sorted(set(speakers)), session_started_at,
                        prompt_version[:40], model[:80], int(self._store._clock()),
                    ),
                )  # fmt: skip
                if cur.rowcount:
                    stored += 1
                else:  # (another run of the same session stored it meanwhile)
                    counts["reveal_repeated"] += 1
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

    async def waiting(
        self, guild_id: int, campaign_id: str, *, opted_in: Collection[int]
    ) -> list[Waiting]:
        """The proposals waiting for the DM, oldest first, after the consent re-check."""
        await self.purge_stopped(guild_id, campaign_id, opted_in)
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT id, character_id, statement_kind, statement_id, how, check_name, teller_id"
                " FROM memory_reveal_proposals WHERE guild_id = %s AND campaign_id = %s"
                " AND status = 'proposed' ORDER BY created_at, id LIMIT %s",
                (*scope.ids, WAITING_MAX),
            )
            return [_waiting(r) for r in await cur.fetchall()]

    async def describe(self, guild_id: int, campaign_id: str, proposal: Waiting) -> Shown | None:
        """The proposal in words for the DM's card, read now (names change). None when there is
        nothing left to keep: the character or the statement is no longer a confirmed, live one
        (the same test `knowers.learn` makes), or the character is sure of it by now. A teller
        gone since does not matter: the card says "another character told them"."""
        from dmbot.memory._changes import ENTITIES, RELATIONS
        from dmbot.memory.continuity import _predicate_words
        from dmbot.memory.store import _load_ontology

        async with self._store._read(guild_id, campaign_id) as scope:
            needed = [proposal.character_id]
            relation = None
            if proposal.kind == CONNECTION:
                relation = await scope.get(RELATIONS, proposal.statement_id)
                if relation is None or relation["status"] != CONFIRMED:
                    return None
                needed += [str(relation["subject_id"]), str(relation["object_id"])]
            else:
                needed.append(proposal.statement_id)
            asked = [*needed, *([proposal.teller_id] if proposal.teller_id else [])]
            rows = await scope.select(
                ENTITIES, " AND id = ANY(%s) AND status = %s AND merged_into IS NULL",
                [asked, CONFIRMED],
            )  # fmt: skip
            entities = {str(e["id"]): e for e in rows}
            if any(i not in entities for i in needed):
                return None
            if entities[proposal.character_id]["type"] != "character":
                return None  # (made a group since: only a character learns, `knowers.learn`)
            name = {i: str(e["name"]) for i, e in entities.items()}
            if relation is not None:
                onto = await _load_ontology(scope)
                key, detail = str(relation["predicate"]), str(relation["detail"] or "")
                words = _predicate_words(onto, key, detail, bool(relation.get("directional")))
                a, b = name[str(relation["subject_id"])], name[str(relation["object_id"])]
                # (a feeling word reads after the names: "Bruno knows Shela (distrusts)")
                what = f"{a} {words} {b}" + (f" ({detail})" if key == "knows" and detail else "")
            else:
                what = f"{name[proposal.statement_id]} exists"
            data = await gate.load(scope)
        sure = gate.visible(gate.Viewer.character(proposal.character_id), data).how_sure(
            proposal.kind, proposal.statement_id
        )
        if sure is not None and sure >= KNOWN:
            return None  # (sure of it by now: nothing to learn)
        if proposal.teller_id is None:
            how = f"a successful {proposal.check_name} check"
        else:
            how = f"{name.get(proposal.teller_id, 'another character')} told them"
        player = entities[proposal.character_id]["role"] == "player_character"
        guessing = sure is not None
        return Shown(name[proposal.character_id], what, how, player, guessing)

    async def dismiss(
        self, guild_id: int, campaign_id: str, proposal_id: str, *, source: str
    ) -> None:
        """Not this: turned down, and not asked about again this session."""
        if source != DM:
            raise MemoryRuleError(ONLY_THE_DM)
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "UPDATE memory_reveal_proposals SET status = 'dismissed', decided_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND status = 'proposed'",
                (int(self._store._clock()), *scope.ids, proposal_id),
            )
            if cur.rowcount == 0:
                raise MemoryRuleError(GONE)

    async def keep(
        self,
        guild_id: int,
        campaign_id: str,
        proposal_id: str,
        *,
        opted_in: Collection[int],
        source: str,
    ) -> int | None:
        """Keep it: the character learns the statement (`MemoryStore.learn_statement`, as the DM,
        one Undo), then the row is marked kept. Claimed first in one statement, so two presses keep
        it once. A speaker who stopped means it is gone; a statement deleted since is told in
        plain words and the row is turned down. Returns the change batch (None: nothing changed,
        they already knew it surely)."""
        if source != DM:
            raise MemoryRuleError(ONLY_THE_DM)
        await self.purge_stopped(guild_id, campaign_id, opted_in)
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "UPDATE memory_reveal_proposals SET status = 'kept', decided_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s AND status = 'proposed'"
                " AND speaker_ids <@ %s::bigint[]"
                " RETURNING character_id, statement_kind, statement_id",
                (int(self._store._clock()), *scope.ids, proposal_id, sorted(set(opted_in))),
            )
            row = await cur.fetchone()
        if row is None:
            raise MemoryRuleError(GONE)
        try:
            written = await self._store.learn_statement(
                guild_id, campaign_id, str(row["statement_kind"]), str(row["statement_id"]),
                str(row["character_id"]), source=source,
            )  # fmt: skip
        except MemoryRuleError as exc:
            # (the statement or the character is gone: nothing to keep, never asked again)
            await self._set(guild_id, campaign_id, proposal_id, DISMISSED)
            raise MemoryRuleError(STATEMENT_GONE) from exc
        except BaseException:
            # (the store failed): it waits again, so the DM can press again. If putting it back
            # fails too (the database is down), the original error is the one that is raised.
            try:
                await self._set(guild_id, campaign_id, proposal_id, PROPOSED)
            except Exception as again:
                log.warning("Reveals: couldn't put a proposal back (%s)", type(again).__name__)
            raise
        return written.batch if written.value else None

    async def _set(self, guild_id: int, campaign_id: str, proposal_id: str, status: str) -> None:
        async with self._store._read(guild_id, campaign_id) as scope:
            await scope.conn.execute(
                "UPDATE memory_reveal_proposals SET status = %s, decided_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND id = %s",
                (
                    status, None if status == PROPOSED else int(self._store._clock()),
                    *scope.ids, proposal_id,
                ),
            )  # fmt: skip

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


@dataclass(frozen=True, slots=True)
class Waiting:
    """A proposal as the store holds it: ids only."""

    id: str
    character_id: str
    kind: str
    statement_id: str
    how: str
    check_name: str
    teller_id: str | None


@dataclass(frozen=True, slots=True)
class Shown:
    """A proposal in words, for the DM's card only (never a player's screen, never logged)."""

    who: str  # the character who learned it
    what: str  # "Bruno knows Shela", or "The Sword exists" (the card says "learned that ...")
    how: str  # "a successful Insight check" or "Clyde told them"
    player: bool  # the character is a player's: keeping it lets that player see it
    guessing: bool  # they hold it only as a guess now: keeping it makes it sure


def _waiting(r: Mapping[str, Any]) -> Waiting:
    return Waiting(
        str(r["id"]), str(r["character_id"]), str(r["statement_kind"]), str(r["statement_id"]),
        str(r["how"]), str(r["check_name"]),
        None if r["teller_id"] is None else str(r["teller_id"]),
    )  # fmt: skip


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
    "DISMISSED", "GONE", "KEPT", "PROPOSED", "STATEMENT_GONE", "Placed", "RevealProposals",
    "Shown", "Stored", "Waiting", "forget", "place",
]  # fmt: skip
