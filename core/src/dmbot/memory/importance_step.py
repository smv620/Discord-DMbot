"""The importance reading after a session (#3172, slice 3): gather the entries the session
touched, ask the AI about them in parts of `importance_read.ITEMS_PER_CALL`, and write the ranks
and roles it gives.

Held here, not trusted:
- **Inside the extractor's call limit.** Each call is counted before it is made and the step
  stops at `max_calls`; what is left is counted as not read, never dropped silently (a later
  session's reading sees those entries again when it touches them). `renew` is awaited before
  each call, so an import's claim stays fresh while it runs (as the extractor's does).
- **The DM's word stays.** A rank the DM corrected last is never overwritten by a reading, and a
  role the DM set or corrected is never ended by one (Supervisor's ruling on #3172), checked again
  in the statement that ends a role.
- **Consent after every wait.** Quotes come only from people recorded now (`TranscriptStore.around`
  skips the DM sidebar and table questions, and reads only them); who is recorded is asked again
  right before each call and after the answer, and an item whose quoted speaker stopped meanwhile
  is not written.
- **Never fatal.** A failed call is counted and the other parts go on; out of funds stops the
  step; a cut or unreadable answer is asked again in two halves (down to one entry), never read as
  "nothing found" (#2596).
- A role a connection already shows is not stored a second time.
- **A new role that means one the campaign has** is not added (`role_match`, only with
  `DMBOT_ROLE_MATCH` on): the existing role is used instead. A weak or close match, or a failed
  call, adds it as before (counted).
Counts only leave this module.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from dmbot.ai import AIOutOfFunds, Reply
from dmbot.db import Database
from dmbot.memory import importance_read as ir
from dmbot.memory import roles
from dmbot.memory.importance_rank import DM_CORRECTED, Assessment, ImportanceStore, roots
from dmbot.memory.role_match import Placed, RoleMatcher
from dmbot.memory.role_store import NewRole, RoleStore
from dmbot.memory.roles_backed import backed_roles
from dmbot.memory.scene_step import parse_ref
from dmbot.transcript.store import TranscriptStore

log = logging.getLogger(__name__)

CALL_TIMEOUT_S = 300.0  # above the slowest tier's own request limit, as the extractor's
MAX_TOKENS = 16_000  # the background floor (#2596)
Consenting = Callable[[], Awaitable[Collection[int]]]


@dataclass(slots=True)
class Report:
    """What the step came to. Counts only."""

    entries: int = 0
    calls: int = 0
    failed: int = 0
    split: int = 0  # answers cut or unreadable, asked again in halves
    ranked: int = 0
    roles_added: int = 0
    roles_ended: int = 0
    codes_added: int = 0
    codes_matched: int = 0  # new roles that meant one the campaign has: that one was used
    match_failed: int = 0  # the match couldn't be asked: the new roles were added as before
    dropped_for_consent: int = 0
    private_raised: int = 0  # ranks the private-scene floor raised to Recurring (#2996 rule 5)
    unsent: int = 0  # entries not read this time: the call limit, or the run stopped
    stopped: str = ""
    lost: Counter[str] = field(default_factory=Counter)

    def line(self) -> str:
        lost = ", ".join(f"{k} {v}" for k, v in sorted(self.lost.items()) if v)
        return (
            f"Importance: {self.entries} entr(ies), {self.ranked} ranked, {self.roles_added} "
            f"role(s) added, {self.roles_ended} ended, {self.codes_added} new code(s), "
            f"{self.codes_matched} matched to one held, {self.match_failed} match(es) failed, "
            f"{self.calls} call(s), {self.failed} failed, {self.split} split, "
            f"{self.dropped_for_consent} dropped for consent, {self.unsent} not read"
            + (f", {self.private_raised} raised for a private scene" if self.private_raised else "")
            + (f", stopped: {self.stopped}" if self.stopped else "")
            + (f"; refused: {lost}" if lost else "")
        )


async def gather(
    db: Database,
    transcripts: TranscriptStore,
    *,
    guild_id: int,
    campaign_id: str,
    session_id: str,
    session_started_at: int,
    agreed: Collection[int],
    mentions: Callable[[str], Awaitable[list[tuple[str, int | None]]]],
    private: bool = False,
) -> tuple[list[ir.Item], dict[str, str], dict[str, str]]:
    """The items for the entries this session touched (heard in it, or in a connection it
    confirmed), busiest first, the campaign's role codes (code -> source) and the labels of its
    own (code -> label). `private` (DMBOT_PRIVATE_SCENES, #2996 rule 5): an entry mentioned in a
    line inside one of the session's private scenes carries that scene's two player characters
    (`Item.private_with`); a scene citing someone not recorded now is left out."""
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT id, type, name, description, role, status, merged_into FROM memory_entities"
            " WHERE guild_id = %s AND campaign_id = %s AND type <> 'person'"
            " AND status IN ('proposed', 'confirmed', 'merged')",
            (guild_id, campaign_id),
        )
        entities = {str(r["id"]): r for r in await cur.fetchall()}
        cur = await conn.execute(
            "SELECT id, subject_id, predicate, object_id, detail, status, to_session_at,"
            " from_session_at FROM memory_relations WHERE guild_id = %s AND campaign_id = %s"
            " AND status = 'confirmed' AND to_session_at IS NULL",
            (guild_id, campaign_id),
        )
        relations = await cur.fetchall()
        cur = await conn.execute(
            "SELECT DISTINCT entity_id FROM memory_heard WHERE guild_id = %s"
            " AND campaign_id = %s AND session_started_at = %s",
            (guild_id, campaign_id, session_started_at),
        )
        heard = {str(r["entity_id"]) for r in await cur.fetchall()}
        cur = await conn.execute(
            "SELECT entity_id, role, reason FROM memory_roles WHERE guild_id = %s"
            " AND campaign_id = %s AND ended_at IS NULL",
            (guild_id, campaign_id),
        )
        stored = await cur.fetchall()
        cur = await conn.execute(
            "SELECT code, source, label FROM memory_role_codes"
            " WHERE guild_id = %s AND campaign_id = %s",
            (guild_id, campaign_id),
        )
        code_rows = await cur.fetchall()
        codes = {str(r["code"]): str(r["source"]) for r in code_rows}
        labels = {str(r["code"]): str(r["label"]) for r in code_rows if r["source"] == "reading"}
        cur = await conn.execute(
            "SELECT DISTINCT ON (entity_id) entity_id, seq, reason FROM memory_importance"
            " WHERE guild_id = %s AND campaign_id = %s ORDER BY entity_id, seq DESC",
            (guild_id, campaign_id),
        )
        newest = await cur.fetchall()
        scenes: list[tuple[int, int, str, str]] = []
        if private:
            cur = await conn.execute(
                "SELECT from_ms, to_ms, first_id, second_id FROM memory_private_scenes"
                " WHERE guild_id = %s AND campaign_id = %s AND session_started_at = %s"
                " AND status = 'kept' AND speaker_ids <@ %s::bigint[]",
                (guild_id, campaign_id, session_started_at, sorted(set(agreed))),
            )
            scenes = [
                (int(r["from_ms"]), int(r["to_ms"]), str(r["first_id"]), str(r["second_id"]))
                for r in await cur.fetchall()
            ]
    if not codes:
        codes = {c: "seeded" for c in roles.STORED_SEEDED}  # (seeded before its first role)
    ended = roots(entities)
    backed = backed_roles(entities, relations)
    partners: dict[str, set[str]] = {}
    session_links: dict[str, list[str]] = {}
    for r in relations:
        a, b = ended.get(str(r["subject_id"])), ended.get(str(r["object_id"]))
        if a is None or b is None or a == b:
            continue
        partners.setdefault(a, set()).add(b)
        partners.setdefault(b, set()).add(a)
        if r["from_session_at"] == session_started_at:
            words = f"{r['predicate']}" + (f" ({r['detail']})" if r["detail"] else "")
            session_links.setdefault(a, []).append(f"{words} {entities[b]['name']}")
            session_links.setdefault(b, []).append(f"{entities[a]['name']} {words} it")
    held: dict[str, set[str]] = {e: set(r) for e, r in backed.items()}
    by_dm: dict[str, set[str]] = {}
    for row in stored:
        root = ended.get(str(row["entity_id"]))
        if root is None:
            continue
        held.setdefault(root, set()).add(str(row["role"]))
        if row["reason"] == DM_CORRECTED:
            by_dm.setdefault(root, set()).add(str(row["role"]))
    last: dict[str, tuple[int, str]] = {}  # (each merged group's newest assessment)
    for row in newest:
        root = ended.get(str(row["entity_id"]))
        if root is not None and int(row["seq"]) > last.get(root, (0, ""))[0]:
            last[root] = (int(row["seq"]), str(row["reason"]))
    groups: dict[str, list[str]] = {}
    for eid, root in ended.items():
        if root is not None and root != eid:
            groups.setdefault(root, []).append(eid)
    touched = {ended.get(e) for e in heard} | set(session_links)
    live = [e for e in touched if e is not None and entities[e]["status"] != "merged"]
    live.sort(key=lambda e: (-len(partners.get(e, ())), entities[e]["name"], e))
    ranks = await ImportanceStore(db).ranks(guild_id, campaign_id)
    items: list[ir.Item] = []
    for number, eid in enumerate(live, start=1):
        row = entities[eid]
        quotes: list[str] = []
        speakers: set[int] = set()
        private_with: set[str] = set()
        for ref, at in await mentions(eid):
            pointer = parse_ref(ref)
            if at != session_started_at or pointer is None or pointer[0] != session_id:
                continue
            for from_ms, to_ms, a, b in scenes:  # (said inside a private scene: its two)
                if from_ms <= pointer[1] <= to_ms:
                    private_with.update(_name(entities, ended, x) for x in (a, b))
            if len(quotes) >= ir.QUOTES_MAX:
                continue
            around = await transcripts.around(guild_id, session_id, pointer[1], pointer[2], agreed)
            if around is not None:
                user, text = around.line
                quotes.append(text)
                speakers.add(user)
        items.append(
            ir.Item(
                number, eid, str(row["name"]), str(row["type"]), ranks.get(eid, 0),
                tuple(sorted(held.get(eid, ()))), tuple(sorted(by_dm.get(eid, ()))),
                len(partners.get(eid, ())), tuple(session_links.get(eid, ())),
                str(row["description"] or ""), tuple(quotes), tuple(sorted(speakers)),
                player=row["type"] == "character" and row["role"] == "player_character",
                dm_rank=last.get(eid, (0, ""))[1] == DM_CORRECTED,
                group=tuple(sorted(groups.get(eid, ()))),
                private_with=tuple(sorted(private_with)),
            )
        )  # fmt: skip
    return items, codes, labels


def _name(entities: Mapping[str, Any], ended: Mapping[str, str | None], entity_id: str) -> str:
    """A scene's character by the name it ended up as (a merge follows); "a player character"
    when it is gone."""
    root = ended.get(entity_id) or entity_id
    row = entities.get(root)
    return str(row["name"]) if row is not None else "a player character"


async def run(
    client: _Client,
    db: Database,
    items: Sequence[ir.Item],
    codes: Mapping[str, str],
    labels: Mapping[str, str],
    *,
    guild_id: int,
    campaign_id: str,
    session_started_at: int | None,
    consenting: Consenting,
    max_calls: int,
    count_call: Callable[[], Awaitable[None]] | None = None,
    renew: Callable[[], Awaitable[object]] | None = None,
    report: Report | None = None,
    matcher: RoleMatcher | None = None,
) -> Report:
    """Ask about `items` in parts and write what comes back. With `matcher`, a proposed role is
    matched to the campaign's own first (`role_match`)."""
    report = report if report is not None else Report()
    report.entries += len(items)
    ranks, role_store = ImportanceStore(db), RoleStore(db)
    added = {c: labels.get(c, c.replace("_", " ")) for c, src in codes.items() if src == "reading"}
    known = dict(codes)
    queue = [
        list(items[i : i + ir.ITEMS_PER_CALL]) for i in range(0, len(items), ir.ITEMS_PER_CALL)
    ]
    while queue:
        part = queue.pop(0)
        if report.calls >= max_calls:
            report.stopped = report.stopped or "call limit"
            report.unsent += len(part) + sum(len(p) for p in queue)
            break
        try:
            agreed = set(await consenting())
        except Exception as exc:  # who is recorded can't be known: nothing is sent
            log.warning("Importance: couldn't ask who is recorded (%s)", type(exc).__name__)
            report.failed += 1
            report.unsent += len(part)
            continue
        sendable = [i for i in part if set(i.speaker_ids) <= agreed]
        report.dropped_for_consent += len(part) - len(sendable)
        if not sendable:
            continue
        if count_call is not None:
            try:
                await count_call()
            except Exception as exc:  # the budget is a hard limit: an uncounted call is not made
                log.warning("Importance: couldn't count a call (%s)", type(exc).__name__)
                report.failed += 1
                report.unsent += len(sendable)
                continue
        if renew is not None:
            try:
                await renew()
            except Exception as exc:  # a claim that can't be kept: stop, another round goes on
                log.warning("Importance: couldn't renew the claim (%s)", type(exc).__name__)
                report.failed += 1
                report.stopped = "claim lost"
                report.unsent += len(sendable) + sum(len(p) for p in queue)
                break
        report.calls += 1
        try:
            reply = await asyncio.wait_for(
                client.complete(
                    ir.system(added, private=any(i.private_with for i in sendable)),
                    ir.message(sendable),
                    max_tokens=MAX_TOKENS,
                ),
                CALL_TIMEOUT_S,
            )
        except AIOutOfFunds:
            report.failed += 1
            report.stopped = "out of funds"
            report.unsent += len(sendable) + sum(len(p) for p in queue)
            break
        except Exception as exc:  # never fatal: the other parts go on
            log.warning("Importance: a call failed (%s)", type(exc).__name__)
            report.failed += 1
            report.unsent += len(sendable)
            continue
        got = ir.parse(reply.text, sendable, known, cut=reply.cut)
        if got is None:
            if len(sendable) > 1:  # (asked again in halves, at the front of the queue)
                half = len(sendable) // 2
                queue[:0] = [sendable[:half], sendable[half:]]
                report.split += 1
            else:
                report.lost["unreadable"] += 1
            continue
        report.lost.update(got.lost)
        report.private_raised += got.raised
        try:
            still = set(await consenting())  # asked again after the answer
        except Exception as exc:  # can't know who is recorded now: write none of this part
            log.warning("Importance: couldn't ask who is recorded (%s)", type(exc).__name__)
            report.failed += 1
            report.dropped_for_consent += len(got.verdicts)
            continue
        quoted = {i.entity_id: set(i.speaker_ids) for i in sendable}
        verdicts = []
        for v in got.verdicts:
            if quoted[v.entity_id] <= still:
                verdicts.append(v)
            else:
                report.dropped_for_consent += 1
        await _write(
            ranks, role_store, guild_id, campaign_id, session_started_at, verdicts, got,
            sendable, known, added, report, matcher,
        )  # fmt: skip
    return report


async def _write(
    ranks: ImportanceStore,
    role_store: RoleStore,
    guild_id: int,
    campaign_id: str,
    session_started_at: int | None,
    verdicts: Sequence[ir.Verdict],
    got: ir.Answer,
    items: Sequence[ir.Item],
    known: dict[str, str],
    added: dict[str, str],
    report: Report,
    matcher: RoleMatcher | None = None,
) -> None:
    """The ranks, the new codes, the roles and the ended roles of one answer. Never fatal."""
    try:
        placed = await _place(matcher, guild_id, campaign_id, got.new_codes, known, report)
        for new_code in got.new_codes:
            if new_code.code in placed.matched:
                report.codes_matched += 1
                continue
            why = await role_store.add_code(
                guild_id, campaign_id, new_code.code, new_code.label, new_code.description,
                new_code.examples, new_code.not_this,
            )  # fmt: skip
            if why is None:
                report.codes_added += 1
                known[new_code.code] = "reading"
                added[new_code.code] = new_code.label
                vector = placed.vectors.get(new_code.code)
                if matcher is not None and vector is not None:
                    await matcher.remember(
                        guild_id, campaign_id, new_code.code,
                        roles.embed_text(new_code.description, new_code.examples), vector,
                    )  # fmt: skip
            else:
                report.lost["new_role_" + why] += 1
        report.ranked += await ranks.add(
            guild_id,
            campaign_id,
            [
                Assessment(v.entity_id, v.rank, v.reason, session_started_at=session_started_at)
                for v in verdicts
                if v.rank is not None
            ],
        )
        holding = {i.entity_id: set(i.roles) for i in items}
        swap = placed.matched  # (a proposed role that meant one held is stored as that one)
        new = [
            NewRole(v.entity_id, code, "reading", session_started_at)
            for v in verdicts
            for code in dict.fromkeys(swap.get(c, c) for c in v.roles)
            if code not in holding[v.entity_id] and code in known
        ]
        report.roles_added += await role_store.add(guild_id, campaign_id, new, report.lost)
        groups = {i.entity_id: i.group for i in items}
        for v in verdicts:
            kept = {swap.get(c, c) for c in v.roles}
            # (a role ended here is read through the same swap, and one the answer also holds
            # after it, such as "henchman" swapped to `minion` beside ended `minion`, stays)
            for code in dict.fromkeys(swap.get(c, c) for c in v.ended):
                if code in kept:
                    continue
                report.roles_ended += await role_store.end(
                    guild_id, campaign_id, v.entity_id, code, also=groups[v.entity_id],
                    keep_dm=True,
                )  # fmt: skip
    except Exception as exc:  # only the type: never a word of the answer
        log.warning("Importance: storing failed (%s)", type(exc).__name__)
        report.failed += 1


async def _place(
    matcher: RoleMatcher | None,
    guild_id: int,
    campaign_id: str,
    new_codes: Sequence[ir.NewCode],
    known: Mapping[str, str],
    report: Report,
) -> Placed:
    """The proposed roles matched to the campaign's own. Any trouble (the call, the vectors) adds
    every one as before and is counted: a failed match never blocks a role."""
    if matcher is None or not new_codes:
        return Placed()
    texts = [(c.code, roles.embed_text(c.description, c.examples)) for c in new_codes]
    try:
        return await matcher.place(guild_id, campaign_id, texts, known, report.lost)
    except Exception as exc:  # only the type: never a word of the roles
        log.warning("Importance: the role match failed (%s)", type(exc).__name__)
        report.match_failed += 1
        return Placed()


class _Client(Protocol):
    """What `run` needs of the AI client (the extractor's)."""

    async def complete(self, system: str, text: str, *, max_tokens: int = ...) -> Reply: ...


__all__ = ["Report", "gather", "run"]
