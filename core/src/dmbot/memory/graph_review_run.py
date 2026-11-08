"""The bot's side of the graph clean-up's careful check (#3741): take a campaign's batch of the DM's
fixes 15 minutes after the last one, read what the story said about each fixed entry, ask the
careful model whether DMbot missed it, and keep a report of what it missed.

The rules (`memory/graph_review.py` has the why):
- only while the campaign's "share to improve DMbot" switch is on, checked when the batch is taken
  and again when the report is written;
- lines only from people who agree to be recorded and have not opted out of improving DMbot, asked
  when it runs (`transcript/improvement.py`), and again after the call: a quote from someone who
  stopped meanwhile does not count;
- the lease (`claimed_at`) means two bot processes never check one batch; a check that dies is
  taken again after `RETAKE_AFTER_S`, and a batch that fails `TRIES` times is let go (the DM's fixes
  stay in the graph: only the check is skipped);
- logs carry counts and error types only: never a name, a line or a quote.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Collection, Sequence
from typing import Any, Protocol

from dmbot import lineage
from dmbot.memory import graph_review as gr
from dmbot.memory.scene_step import parse_ref
from dmbot.memory.store import MemoryStore
from dmbot.transcript.store import TranscriptStore

log = logging.getLogger(__name__)


class Client(Protocol):
    @property
    def model(self) -> str: ...

    async def complete(self, system: str, text: str, *, max_tokens: int = ...) -> Any: ...


Allowed = Callable[[Collection[int]], Awaitable[set[int]]]


class GraphReviews:
    def __init__(
        self,
        memory: MemoryStore,
        transcripts: TranscriptStore,
        ai: Client,
        *,
        consenting: Callable[[int], Awaitable[Collection[int]]],
        improvement_allowed: Allowed,
        clock: Callable[[], float],
    ) -> None:
        self._memory = memory
        self._db = memory._db
        self._transcripts = transcripts
        self._ai = ai
        self._consenting = consenting
        self._allowed = improvement_allowed
        self._clock = clock

    async def round(self, guild_id: int) -> int:
        """Check every batch of this server that is due. Returns how many reports were kept."""
        kept = 0
        while (taken := await self._claim(guild_id)) is not None:
            campaign_id, fixes, token, tries = taken
            if tries > gr.TRIES:
                await self._finish(guild_id, campaign_id, len(fixes), [], [], token, stopped=True)
                log.warning("A graph check was let go after %d tries", gr.TRIES)
                continue
            try:
                kept += await self._check(guild_id, campaign_id, fixes, token)
            except Exception as exc:  # the type only: a database error can quote a row
                log.warning("A graph check failed: %s", type(exc).__name__)
        return kept

    async def _claim(self, guild_id: int) -> tuple[str, list[dict[str, Any]], int, int] | None:
        now = int(self._clock())
        token = int(self._clock() * 1000)  # (milliseconds: a unique lease, as plot_talk_run's)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE graph_review_pending p SET claimed_at = %s, tries = p.tries + 1"
                " WHERE (p.guild_id, p.campaign_id) = (SELECT q.guild_id, q.campaign_id"
                "  FROM graph_review_pending q WHERE q.guild_id = %s"
                "  AND jsonb_array_length(q.fixes) > 0 AND q.last_fix_at <= %s"
                "  AND (q.claimed_at IS NULL OR q.claimed_at < %s)"
                "  ORDER BY q.last_fix_at LIMIT 1 FOR UPDATE SKIP LOCKED)"
                " RETURNING p.campaign_id, p.fixes, p.tries",
                (token, guild_id, now - gr.WAIT_S, (now - gr.RETAKE_AFTER_S) * 1000),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return str(row["campaign_id"]), list(row["fixes"]), token, int(row["tries"])

    async def _renew(self, guild_id: int, campaign_id: str, token: int) -> int | None:
        """Push the lease on between the parts of a long batch, so no other bot takes it while
        this one is still asking (each part may wait up to `CALL_TIMEOUT_S`). The new token, or
        None when the lease was lost."""
        fresh = int(self._clock() * 1000)
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE graph_review_pending SET claimed_at = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND claimed_at = %s",
                (fresh, guild_id, campaign_id, token),
            )
            return fresh if cur.rowcount else None

    async def _switch_on(self, conn: Any, guild_id: int, campaign_id: str) -> bool:
        cur = await conn.execute(
            "SELECT share_improve FROM campaigns WHERE guild_id = %s AND id = %s",
            (guild_id, campaign_id),
        )
        row = await cur.fetchone()
        return row is not None and bool(row["share_improve"])

    async def _check(
        self, guild_id: int, campaign_id: str, raw: list[dict[str, Any]], token: int
    ) -> int:
        async with self._db.guild(guild_id) as conn:
            if not await self._switch_on(conn, guild_id, campaign_id):
                await conn.execute(  # (off means off: `set_share_improve` deleted it already)
                    "DELETE FROM graph_review_pending WHERE guild_id = %s AND campaign_id = %s",
                    (guild_id, campaign_id),
                )
                return 0
        agreed = await self._people(guild_id)
        fixes = await self._fixes(guild_id, campaign_id, raw, agreed)
        verdicts: list[gr.Verdict] = []
        for start in range(0, len(fixes), gr.PER_CALL):  # (#2596: a long batch goes in parts)
            if start:
                renewed = await self._renew(guild_id, campaign_id, token)
                if renewed is None:
                    return 0  # (another bot has it now)
                token = renewed
            verdicts += await self._ask(fixes[start : start + gr.PER_CALL])
        return await self._finish(guild_id, campaign_id, len(raw), fixes, verdicts, token)

    async def _ask(self, part: Sequence[gr.Fix]) -> list[gr.Verdict]:
        """One careful call for these fixes. An answer cut at the length limit is asked again in
        two halves, down to one fix (#2596); a refusal, or one fix's answer still cut, raises, so
        the batch is taken again later and, after `TRIES`, recorded as stopped early."""
        reply = await asyncio.wait_for(
            self._ai.complete(gr.system(), gr.message(part), max_tokens=gr.TOKENS),
            gr.CALL_TIMEOUT_S,
        )
        if getattr(reply, "cut", False) and len(part) > 1:
            half = len(part) // 2
            return await self._ask(part[:half]) + await self._ask(part[half:])
        if getattr(reply, "refused", False) or getattr(reply, "cut", False):
            raise RuntimeError("the careful check stopped early")
        parsed = gr.parse(str(reply.text), part)
        if parsed is None:
            raise ValueError("the careful check's answer was not the JSON asked for")
        return parsed

    async def _people(self, guild_id: int) -> set[int]:
        """Who may be quoted now: agrees to be recorded, and has not opted out of improving
        DMbot."""
        return set(await self._allowed(set(await self._consenting(guild_id))))

    async def _fixes(
        self, guild_id: int, campaign_id: str, raw: Sequence[dict[str, Any]], agreed: set[int]
    ) -> list[gr.Fix]:
        """The batch as the call reads it, each fix once (a link added then reworded is one fix).
        A fix the DM undid before the check (its connection gone or no longer live, its merge or
        name taken back) is dropped."""
        ids = sorted(gr.entity_ids(raw))
        rel_ids = sorted({str(f["relation"]) for f in raw if f.get("kind") == gr.LINK})
        async with self._memory._read(guild_id, campaign_id, snapshot=True) as scope:
            cur = await scope.conn.execute(
                "SELECT id, name, type, description, status, merged_into, unnamed,"
                " made_by_commit FROM memory_entities WHERE guild_id = %s AND campaign_id = %s"
                " AND id = ANY(%s::text[])",
                (*scope.ids, ids),
            )
            entities = {str(r["id"]): r for r in await cur.fetchall()}
            cur = await scope.conn.execute(
                "SELECT id, subject_id, object_id, predicate, detail, status FROM memory_relations"
                " WHERE guild_id = %s AND campaign_id = %s AND id = ANY(%s::text[])",
                (*scope.ids, rel_ids),
            )
            relations = {str(r["id"]): r for r in await cur.fetchall()}
            # (the lore quotes kept for these entries' connections: the only lore words DMbot
            # keeps, since an upload's text is blanked once it is read; oldest first, so the same
            # six reach the prompt each time)
            cur = await scope.conn.execute(
                "SELECT r.subject_id, r.object_id, s.quote FROM memory_link_sources s"
                " JOIN memory_relations r ON r.guild_id = s.guild_id"
                " AND r.campaign_id = s.campaign_id AND r.id = s.relation_id"
                " WHERE s.guild_id = %s AND s.campaign_id = %s AND s.quote <> ''"
                " AND (r.subject_id = ANY(%s::text[]) OR r.object_id = ANY(%s::text[]))"
                " ORDER BY s.created_at, s.relation_id",
                (*scope.ids, ids, ids),
            )
            lore: dict[str, list[str]] = {}
            for r in await cur.fetchall():
                for end in (str(r["subject_id"]), str(r["object_id"])):
                    lore.setdefault(end, []).append(str(r["quote"]))
            cur = await scope.conn.execute(
                "SELECT subject_id, scene FROM memory_scenes WHERE guild_id = %s"
                " AND campaign_id = %s AND subject_kind = 'entity'"
                " AND subject_id = ANY(%s::text[]) AND speaker_ids <@ %s::bigint[]",
                (*scope.ids, ids, sorted(agreed)),
            )
            scenes = {str(r["subject_id"]): str(r["scene"]) for r in await cur.fetchall()}
        sides: dict[str, gr.Side] = {}
        for eid, e in entities.items():
            sides[eid] = gr.Side(
                eid, str(e["name"]), str(e["type"]), str(e["description"] or ""),
                scenes.get(eid, ""), tuple(await self._lines(guild_id, campaign_id, eid, agreed)),
                tuple(dict.fromkeys(lore.get(eid, [])))[:6], e["made_by_commit"],
            )  # fmt: skip
        out: list[gr.Fix] = []
        seen: set[tuple[str, str]] = set()
        for f in raw:
            key = (str(f.get("kind")), str(f.get("relation") or f.get("entity")))
            if key in seen:
                continue
            seen.add(key)
            fix = self._fix(len(out) + 1, f, entities, relations, sides)
            if fix is not None:
                out.append(fix)
        return out

    @staticmethod
    def _fix(
        number: int,
        f: dict[str, Any],
        entities: dict[str, Any],
        relations: dict[str, Any],
        sides: dict[str, gr.Side],
    ) -> gr.Fix | None:
        kind = f.get("kind")
        if kind == gr.LINK:
            r = relations.get(str(f.get("relation")))
            if r is None or str(r["status"]) not in gr.LIVE:
                return None  # (undone or removed since)
            a, b = sides.get(str(r["subject_id"])), sides.get(str(r["object_id"]))
            if a is None or b is None:
                return None
            words = f"linked {a.label} to {b.label}: {str(r['predicate']).replace('_', ' ')}"
            if r["detail"]:
                words += f" ({r['detail']})"
            return gr.Fix(number, gr.LINK, words, (a, b), str(r["id"]))
        e = entities.get(str(f.get("entity")))
        side = sides.get(str(f.get("entity")))
        if e is None or side is None:
            return None
        if kind == gr.SAME_AS:
            into = sides.get(str(f.get("into")))
            if into is None or str(e["merged_into"] or "") != into.id:
                return None  # (the merge was taken back)
            return gr.Fix(number, gr.SAME_AS, f"said {side.label} is {into.label}", (side, into))
        if kind == gr.NAMED:
            if e["unnamed"]:
                return None  # (the name was taken back)
            return gr.Fix(number, gr.NAMED, f"named someone the story never named: {side.label}",
                          (side,))  # fmt: skip
        return None

    async def _lines(
        self, guild_id: int, campaign_id: str, entity_id: str, agreed: set[int]
    ) -> list[gr.Said]:
        """Up to `LINES_PER_ENTRY` mentions of the entry, each with the line before and after,
        from people who may be quoted now."""
        out: list[gr.Said] = []
        for ref, _at in (await self._memory.mentions(guild_id, campaign_id, entity_id))[
            : gr.LINES_PER_ENTRY
        ]:
            pointer = parse_ref(ref)
            if pointer is None:
                continue
            around = await self._transcripts.around(
                guild_id, pointer[0], pointer[1], pointer[2], agreed
            )
            if around is None:
                continue
            for seen in (around.before, around.line, around.after):
                if seen is not None and seen[0] in agreed:
                    out.append(gr.Said(seen[0], seen[1]))
        return out

    async def _finish(
        self,
        guild_id: int,
        campaign_id: str,
        read: int,
        fixes: Sequence[gr.Fix],
        verdicts: Sequence[gr.Verdict],
        token: int,
        *,
        stopped: bool = False,
    ) -> int:
        """Take the fixes it read off the batch, record how the check ended (for the page's status
        line), and keep a report when DMbot missed something: one transaction, and only while this
        check still holds the lease. Fixes the DM made during the check stay for the next one.
        `drop_all` (the switch going off) waits on the row this updates first, then deletes the
        report with the rest."""
        verdicts = list(verdicts)
        still = await self._people(guild_id) if any(v.in_story for v in verdicts) else set()
        for v in verdicts:  # (someone may have stopped during the call: their quotes go)
            if v.in_story and not v.speaker_ids <= still:
                v.in_story, v.quote, v.speaker_ids, v.sources = False, "", set(), ()
        missed = [v for v in verdicts if v.in_story]
        now = int(self._clock())
        async with self._db.guild(guild_id) as conn:
            cur = await conn.execute(
                "UPDATE graph_review_pending SET claimed_at = NULL, tries = 0,"
                " fixes = jsonb_path_query_array(fixes, %s::jsonpath),"
                " checked_at = %s, checked = %s, missed = %s, stopped = %s"
                " WHERE guild_id = %s AND campaign_id = %s AND claimed_at = %s"
                " RETURNING jsonb_array_length(fixes) AS left_n",
                (
                    f"$[{read} to last]", now, len(verdicts), len(missed), stopped,
                    guild_id, campaign_id, token,
                ),
            )  # fmt: skip
            row = await cur.fetchone()
            if row is None:
                return 0  # (the lease was lost, or the switch went off and took the batch)
            if not row["left_n"]:  # (nothing waits: its entries' scenes may go now)
                await conn.execute(
                    "UPDATE graph_review_pending SET entity_ids = '{}'"
                    " WHERE guild_id = %s AND campaign_id = %s",
                    (guild_id, campaign_id),
                )
            if not missed or not await self._switch_on(conn, guild_id, campaign_id):
                return 0
            await conn.execute(
                "INSERT INTO graph_reviews (guild_id, campaign_id, id, created_at, checked,"
                " missed, report, speaker_ids, review_commit, prompt_version, model)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    guild_id, campaign_id, uuid.uuid4().hex, now, len(verdicts), len(missed),
                    gr.report(campaign_id, fixes, verdicts, now=now,
                              review_commit=lineage.running_commit()),
                    sorted({u for v in missed for u in v.speaker_ids}),
                    lineage.running_commit(), gr.PROMPT_VERSION,
                    str(getattr(self._ai, "model", "") or "unknown")[:80],
                ),
            )  # fmt: skip
        log.info("Graph check kept a report: %d checked, %d missed", len(verdicts), len(missed))
        return 1
