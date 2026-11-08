"""Matching a story role the reading proposes to one the campaign already has, by meaning (#3172,
the embedding slice; Supervisor's ruling of 2026-11-08 on #3172).

When the importance reading proposes a NEW role (`importance_read.NewCode`), its description and
examples are compared with the campaign's existing roles: the owner's seeded ones and the ones the
campaign's readings added. When the closest is close enough (`THRESHOLD`) and clearly ahead of the
second (`MARGIN`), that role is used instead and nothing new is added, so near-duplicates
("henchman" beside `minion`) don't fill the campaign's `roles.ADDED_MAX`. Otherwise the new role is
added as before: a weak or close match is never guessed, and a failed call never blocks (counted).

What goes to Voyage AI (the ruling's terms): the seeded roles' fixed texts, and a role's
description and examples (`roles.embed_text`), never `not_this`, never a transcript line, never a
DM note. The seeded vectors are the same for every campaign, so they are kept in memory, keyed by
the model and the hash of the text (a changed text is made again). An added role's vector is kept
in its own campaign's `memory_vectors` (kind `role`, owner the code), so one campaign's role never
matches another's; vectors are never in a backup, so one that is missing (a restored campaign, an
earlier failure) is made again the next time a match is asked, and one whose code is gone is
dropped.

Off until measured: `THRESHOLD` and `MARGIN` are starting values; dev1's run on the golden set
(`role_golden`, `devtools/role_match_check.py`) gives the counts Supervisor rules them from, and
`DMBOT_ROLE_MATCH` stays off until then. Every Voyage call is told to `count_call` first. Counts
only leave this module.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from dmbot.memory import roles
from dmbot.memory import vectors as vs
from dmbot.memory.voyage import cosine

log = logging.getLogger(__name__)

THRESHOLD = 0.80  # the lowest cosine that can be used: a starting value until dev1's counts
MARGIN = 0.05  # the lead over the second best: a starting value until dev1's counts


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str], *, query: bool = False) -> list[list[float]]: ...


class AddedRoles(Protocol):
    """Where the campaign's added roles come from (`RoleStore.added_texts`)."""

    async def added_texts(self, guild_id: int, campaign_id: str) -> dict[str, str]: ...


@dataclass(frozen=True, slots=True)
class Best:
    code: str
    score: float
    second_score: float  # -1.0 when there is only one role to compare with


def nearest(vector: Sequence[float], vectors: Mapping[str, Sequence[float]]) -> Best | None:
    """The closest role and the runner-up's score, or None when there is nothing to compare, a
    vector is the wrong length or a number is not finite. Equal scores are ordered by code, so the
    result never depends on the order of the input."""
    try:
        scored = [(cosine(vector, v), code) for code, v in vectors.items()]
    except ValueError:  # (a vector of another length)
        return None
    if not scored or not all(math.isfinite(s) for s, _ in scored):
        return None
    scored.sort(key=lambda sc: (-sc[0], sc[1]))
    second = scored[1][0] if len(scored) > 1 else -1.0
    return Best(scored[0][1], scored[0][0], second)


def decide(
    best: Best | None, *, threshold: float = THRESHOLD, margin: float = MARGIN
) -> tuple[str | None, str]:
    """The existing role to use, or None, with why not ("" when used): no match, below the
    threshold, or too close to the second."""
    if best is None:
        return None, "no_match"
    if not best.score >= threshold:  # (written so a NaN is below the threshold)
        return None, "below_threshold"
    if not best.score - best.second_score >= margin:
        return None, "close_second"
    return best.code, ""


class SeededVectors:
    """The seeded roles' vectors, made once for each model and text and kept for the process (the
    same 29 fixed texts for every campaign: no campaign data). A failed call keeps nothing, so the
    next match asks again."""

    def __init__(self) -> None:
        self._kept: dict[tuple[str, str], list[float]] = {}  # (model, text hash) -> vector

    async def get(
        self, embedder: Embedder, model: str, count_call: Callable[[], Awaitable[None]] | None
    ) -> dict[str, list[float]]:
        wanted = [r for r in roles.SEEDED_ROLES if r.code != roles.COMMONER]
        texts = {r.code: r.embed_text for r in wanted}
        missing = [c for c, t in texts.items() if (model, vs.text_hash(t)) not in self._kept]
        if missing:
            if count_call is not None:
                await count_call()
            made = await embedder.embed([texts[c] for c in missing])
            if len(made) != len(missing):
                raise ValueError("embedding count")
            for code, vector in zip(missing, made, strict=True):
                self._kept[(model, vs.text_hash(texts[code]))] = list(vector)
        return {c: self._kept[(model, vs.text_hash(t))] for c, t in texts.items()}


@dataclass(slots=True)
class Placed:
    """What `RoleMatcher.place` came to: new code -> the existing code to use instead, and each new
    code's vector (kept once the code is added, so it is never made twice)."""

    matched: dict[str, str] = field(default_factory=dict)
    vectors: dict[str, list[float]] = field(default_factory=dict)


class RoleMatcher:
    """One campaign's matching, built for one reading. `place` may raise (the caller adds every
    new role as before and counts the failure); `remember` never does."""

    def __init__(
        self,
        embedder: Embedder,
        store: vs.VectorStore,
        added: AddedRoles,
        model: str,
        seeded: SeededVectors,
        *,
        count_call: Callable[[], Awaitable[None]] | None = None,
        threshold: float = THRESHOLD,
        margin: float = MARGIN,
    ) -> None:
        self._embedder, self._store, self._added = embedder, store, added
        self._model, self._seeded, self._count = model, seeded, count_call
        self.threshold, self.margin = threshold, margin

    async def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self._count is not None:
            await self._count()
        made = await self._embedder.embed(texts)
        if len(made) != len(texts):
            raise ValueError("embedding count")
        return [list(v) for v in made]

    async def _added_vectors(self, guild_id: int, campaign_id: str) -> dict[str, list[float]]:
        """The campaign's added roles' vectors: the missing or changed ones are made and kept, and
        those of codes that are gone are dropped. Read back from the campaign's own rows."""
        texts = await self._added.added_texts(guild_id, campaign_id)
        kept = await self._store.kept(guild_id, campaign_id, self._model, kind=vs.ROLE)
        have = {owner: h for (_k, owner), h in kept.items()}
        gone = sorted(set(have) - set(texts))
        if gone:
            await self._store.drop(guild_id, campaign_id, vs.ROLE, gone)
        stale = sorted(c for c, t in texts.items() if have.get(c) != vs.text_hash(t))
        made: dict[str, list[float]] = {}
        if stale:
            for code, vector in zip(
                stale, await self._embed([texts[c] for c in stale]), strict=True
            ):
                made[code] = vector
            await self._store.save(
                guild_id, campaign_id, self._model,
                [vs.Item(vs.ROLE, c, vs.text_hash(texts[c]), v) for c, v in made.items()],
            )  # fmt: skip
        fresh = set(texts) - set(made)
        if fresh:
            for row in await self._store.vectors(guild_id, campaign_id, self._model, vs.ROLE):
                if row.owner_id in fresh:
                    made[row.owner_id] = list(row.vector)
        return made

    async def place(
        self,
        guild_id: int,
        campaign_id: str,
        new: Sequence[tuple[str, str]],
        known: Mapping[str, str],
        counts: Counter[str],
    ) -> Placed:
        """Match each proposed role (code, `roles.embed_text`) to the campaign's existing roles
        (`known`: code -> source). One call for all the proposed texts of one answer."""
        out = Placed()
        if not new:
            return out
        seeded = await self._seeded.get(self._embedder, self._model, self._count)
        added = await self._added_vectors(guild_id, campaign_id)
        pool = {c: v for c, v in {**seeded, **added}.items() if c in known and c != roles.COMMONER}
        for (code, _text), vector in zip(new, await self._embed([t for _c, t in new]), strict=True):
            out.vectors[code] = vector
            target, why = decide(
                nearest(vector, pool), threshold=self.threshold, margin=self.margin
            )
            if target is None:
                counts["role_match_" + why] += 1
            else:
                out.matched[code] = target
        return out

    async def remember(
        self, guild_id: int, campaign_id: str, code: str, text: str, vector: Sequence[float]
    ) -> bool:
        """Keep a newly added role's vector in its campaign. Never raises: a vector not kept is
        made again at the next match."""
        try:
            kept = await self._store.save(
                guild_id,
                campaign_id,
                self._model,
                [vs.Item(vs.ROLE, code, vs.text_hash(text), vector)],
            )
        except Exception as exc:  # only the type: the role itself is already stored
            log.warning("Role match: couldn't keep a vector (%s)", type(exc).__name__)
            return False
        return kept > 0


__all__ = [
    "MARGIN",
    "THRESHOLD",
    "Best",
    "Placed",
    "RoleMatcher",
    "SeededVectors",
    "decide",
    "nearest",
]
