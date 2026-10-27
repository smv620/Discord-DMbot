"""PlotBot's starting points by meaning (#2664; docs/STORY_MEMORY.md 9a, "Embeddings"): given a
question, the campaign's entries closest to it in meaning, to add to the names the question spelled
out. A question about "the cook who poisoned the duke" finds Bram's card even though it never says
"Bram".

Steps: the question becomes numbers (`voyage-3.5`, as a query); the campaign's kept numbers for
entries are compared in Python inside this one campaign (`VectorStore.nearest`); the best
`POOL` are re-scored by `rerank-2.5` against the entries' own text; the top few entry ids come back,
best first. It is optional context: any failure, a switch turned off, or a campaign with no numbers
yet returns nothing and the answer goes ahead with the names alone. The question's words go to
Voyage AI only while `allowed()` says the switch is on and the key is set (the privacy page names
Voyage; a table question is never sent, the caller does not ask for one). Logging is counts only.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol

from dmbot.ai import AIOutOfFunds
from dmbot.db import Database
from dmbot.memory import vectors as vs
from dmbot.memory.embedding import Allowed, owner_of
from dmbot.memory.voyage import ProviderError, Ranked

log = logging.getLogger(__name__)

POOL = 20  # candidates compared by meaning before the reranker picks the best
KEEP = 3  # entries given back
MIN_SCORE = 0.2  # a reranker score below this is not a match; nothing is better than a bad lead
QUESTION_MAX = 500  # the question as sent: a long one is not a question about one thing


class Voyager(Protocol):
    async def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]: ...

    async def rerank(self, query: str, documents: Sequence[str], *, top_k: int) -> list[Ranked]: ...


async def entry_texts(
    db: Database, guild_id: int, campaign_id: str, ids: Sequence[str], *, for_players: bool = False
) -> dict[str, str]:
    """The text each of these confirmed entries was embedded from (name and description), by id.
    `for_players`: leave out an entry the players may not know yet (hidden, or a clue only some
    know), so a lead that ends in a player-facing answer never starts from one."""
    if not ids:
        return {}
    async with db.guild(guild_id) as conn:
        cur = await conn.execute(
            "SELECT id, name, description FROM memory_entities"
            " WHERE guild_id = %s AND campaign_id = %s AND id = ANY(%s) AND status = 'confirmed'"
            " AND merged_into IS NULL AND char_length(btrim(description)) > 0"
            " AND NOT (%s AND (hidden_from_players OR audience_limited))",
            (guild_id, campaign_id, list(ids), for_players),
        )
        return {str(r["id"]): f"{r['name']}: {r['description']}" for r in await cur.fetchall()}


class MeaningSeeds:
    def __init__(
        self,
        db: Database,
        store: vs.VectorStore,
        voyage: Voyager,
        model: str,
        *,
        allowed: Allowed,
        count_call: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._db = db
        self._store = store
        self._voyage = voyage
        self._model = model
        self._allowed = allowed
        self._count = count_call

    async def find(
        self,
        guild_id: int,
        campaign_id: str,
        question: str,
        *,
        keep: int = KEEP,
        for_players: bool = False,
    ) -> list[str]:
        """Entry ids closest in meaning to `question`, best first; `[]` when there is nothing to
        say (switched off, no numbers yet, nothing close enough, or any failure)."""
        text = " ".join(question.split())[:QUESTION_MAX]
        if not text or not await self._allowed():
            return []
        if not await self._store.has_any(guild_id, campaign_id, self._model, vs.ASSERTION):
            return []  # nothing to compare with yet: the question goes nowhere
        try:
            if self._count is not None:
                await self._count()
            (query,) = await self._voyage.embed([text], query=True)
            close = await self._store.nearest(
                guild_id, campaign_id, self._model, query, kinds=[vs.ASSERTION], limit=POOL
            )
            ids = list(dict.fromkeys(owner_of(m.owner_id) for m in close))
            texts = await entry_texts(self._db, guild_id, campaign_id, ids, for_players=for_players)
            ids = [i for i in ids if i in texts]  # an entry gone or merged since is not a lead
            if not ids:
                return []
            if self._count is not None:
                await self._count()
            ranked = await self._voyage.rerank(text, [texts[i] for i in ids], top_k=keep)
        except AIOutOfFunds:
            log.warning("Meaning search: no credit left; answering from the names alone")
            return []
        except ProviderError as exc:
            log.warning("Meaning search: a call failed (%s)", exc.describe())
            return []
        found = [ids[r.index] for r in ranked if r.score >= MIN_SCORE and r.index < len(ids)]
        log.info("Meaning search: %d candidates, %d starting points", len(ids), len(found))
        return found
