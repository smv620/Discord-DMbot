"""The stored scene of a question (#3596): keep, find, sweep and wipe. The AI's text is written by
`scene_reading`; this is where it lives until the question is answered.

- **Kept until answered.** `sweep` deletes a scene whose entry is no longer an open question (named,
  joined, removed, rejected, merged) or whose claim is no longer waiting. There is no foreign key
  to the subject, so the sweep is the rule, run after a session and before a scene is read.
- **Consent.** A scene made from a session carries the people whose lines it came from.
  `purge_stopped` deletes one when any of them is not recorded now, before the DM is shown any;
  `forget` (Stop recording me) deletes theirs in every campaign of the server. A scene from the
  DM's own document has none (nothing to wipe) and goes with its question.
- **DM-only.** Only the bot and the DM-only door (`dmbot_web_scenes`) read the table.
Counts only leave this module; the scene's words are never logged.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dmbot.db import Conn
from dmbot.memory import isolated
from dmbot.memory.models import MemoryRuleError
from dmbot.memory.store import isolated_rows_in

if TYPE_CHECKING:
    from dmbot.memory.store import MemoryStore

ENTITY, CLAIM = "entity", "claim"


@dataclass(frozen=True, slots=True)
class Scene:
    """One scene to keep: whose question it is about, the AI's words, and who said the lines."""

    subject_kind: str
    subject_id: str
    text: str
    lines_n: int
    speaker_ids: tuple[int, ...] = ()
    session_started_at: int | None = None


class Scenes:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def keep(
        self,
        guild_id: int,
        campaign_id: str,
        scenes: Sequence[Scene],
        *,
        opted_in: Collection[int],
        prompt_version: str,
        model: str,
    ) -> int:
        """Keep these scenes (a later one for the same question replaces the earlier). A scene
        made from someone who is not recorded now is not kept. Returns how many were."""
        recorded = set(opted_in)
        kept = 0
        async with self._store._read(guild_id, campaign_id) as scope:
            for s in scenes:
                if s.subject_kind not in (ENTITY, CLAIM) or not s.text.strip():
                    continue
                if not set(s.speaker_ids) <= recorded:
                    continue
                await scope.conn.execute(
                    "INSERT INTO memory_scenes (guild_id, campaign_id, subject_kind, subject_id,"
                    " scene, lines_n, speaker_ids, session_started_at, prompt_version, model,"
                    " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (guild_id, campaign_id, subject_kind, subject_id) DO UPDATE SET"
                    " scene = EXCLUDED.scene, lines_n = EXCLUDED.lines_n,"
                    " speaker_ids = EXCLUDED.speaker_ids,"
                    " session_started_at = EXCLUDED.session_started_at,"
                    " prompt_version = EXCLUDED.prompt_version, model = EXCLUDED.model,"
                    " created_at = EXCLUDED.created_at",
                    (
                        *scope.ids, s.subject_kind, s.subject_id, s.text, s.lines_n,
                        sorted(set(s.speaker_ids)), s.session_started_at, prompt_version[:40],
                        model[:80], int(self._store._clock()),
                    ),
                )  # fmt: skip
                kept += 1
        return kept

    async def sweep(self, guild_id: int, campaign_id: str) -> int:
        """Delete the scenes whose question is answered. An entry's question is open while it is
        a suggestion (`proposed`) or a person the story never named (confirmed and `unnamed`), and
        it has not been merged away; a claim's while it waits. An entry no chain links to the party
        keeps its scene too, for the graph clean-up page, until it is linked (#3740). Returns how
        many went."""
        async with self._store._read(guild_id, campaign_id) as scope:
            # (the graph is read only when an entry's scene is up for deletion, since the DM
            # opens scene cards often and a lore campaign's graph is large; and inside this one
            # transaction, so a link rejected meanwhile cannot cost a newly isolated entry its
            # scene)
            cur = await scope.conn.execute(
                "SELECT 1 FROM memory_scenes s WHERE s.guild_id = %s AND s.campaign_id = %s"
                " AND s.subject_kind = 'entity' AND NOT EXISTS (SELECT 1 FROM memory_entities e"
                "  WHERE e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id"
                "  AND e.id = s.subject_id AND e.merged_into IS NULL"
                "  AND (e.status = 'proposed' OR (e.status = 'confirmed' AND e.unnamed)))"
                " LIMIT 1",
                scope.ids,
            )
            keep: list[str] = []
            if await cur.fetchone() is not None:
                keep = sorted(isolated.isolated_ids(*await isolated_rows_in(scope)))
            cur = await scope.conn.execute(
                "DELETE FROM memory_scenes s WHERE s.guild_id = %s AND s.campaign_id = %s AND ("
                " (s.subject_kind = 'entity' AND NOT s.subject_id = ANY(%s::text[])"
                "  AND NOT EXISTS (SELECT 1 FROM memory_entities e"
                "   WHERE e.guild_id = s.guild_id AND e.campaign_id = s.campaign_id"
                "   AND e.id = s.subject_id AND e.merged_into IS NULL"
                "   AND (e.status = 'proposed' OR (e.status = 'confirmed' AND e.unnamed))))"
                " OR (s.subject_kind = 'claim' AND NOT EXISTS (SELECT 1 FROM memory_claims c"
                "   WHERE c.guild_id = s.guild_id AND c.campaign_id = s.campaign_id"
                "   AND c.id = s.subject_id AND c.status = 'proposed')))",
                (*scope.ids, keep),
            )
            return cur.rowcount

    async def purge_stopped(
        self, guild_id: int, campaign_id: str, opted_in: Collection[int]
    ) -> int:
        """Delete the scenes with a speaker who is not recorded in this server now: the consent
        re-check on the read path, as for claims and deeds."""
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "DELETE FROM memory_scenes WHERE guild_id = %s AND campaign_id = %s"
                " AND NOT (speaker_ids <@ %s::bigint[])",
                (*scope.ids, sorted(set(opted_in))),
            )
            return cur.rowcount

    async def of(
        self,
        guild_id: int,
        campaign_id: str,
        subject_kind: str,
        subject_ids: Sequence[str],
        *,
        opted_in: Collection[int],
    ) -> Mapping[str, str]:
        """The scenes of these questions, after the answered ones and the ones with a stopped
        speaker are gone. DM-only: the caller is a DM's card."""
        if subject_kind not in (ENTITY, CLAIM):
            raise MemoryRuleError("That is not something with a scene.")
        if not subject_ids:
            return {}
        await self.sweep(guild_id, campaign_id)
        await self.purge_stopped(guild_id, campaign_id, opted_in)
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT subject_id, scene FROM memory_scenes WHERE guild_id = %s"
                " AND campaign_id = %s AND subject_kind = %s AND subject_id = ANY(%s)",
                (*scope.ids, subject_kind, list(subject_ids)),
            )
            return {str(r["subject_id"]): str(r["scene"]) for r in await cur.fetchall()}

    async def missing(
        self, guild_id: int, campaign_id: str, subject_kind: str, subject_ids: Sequence[str]
    ) -> list[str]:
        """Of these questions, the ones with no scene yet (what the next session's step makes)."""
        if not subject_ids:
            return []
        async with self._store._read(guild_id, campaign_id) as scope:
            cur = await scope.conn.execute(
                "SELECT subject_id FROM memory_scenes WHERE guild_id = %s AND campaign_id = %s"
                " AND subject_kind = %s AND subject_id = ANY(%s)",
                (*scope.ids, subject_kind, list(subject_ids)),
            )
            have = {str(r["subject_id"]) for r in await cur.fetchall()}
        return [i for i in subject_ids if i not in have]


async def forget(conn: Conn, guild_id: int, user_id: int) -> int:
    """Stop recording me: delete, in every campaign of the server, the scenes made from this
    person's lines. Returns how many went."""
    cur = await conn.execute(
        "DELETE FROM memory_scenes WHERE guild_id = %s AND %s = ANY(speaker_ids)",
        (guild_id, user_id),
    )
    return cur.rowcount
