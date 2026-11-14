"""Offering the words a table keeps using for a connection as a kind of its own (#3033).

When the same words sit on three or more of a campaign's catch-all connections ("sworn on the
ashes of"), DMbot may offer the DM to make them a kind of connection for this campaign. This module
holds the counting and the answers' bookkeeping; the store makes the kind and moves the
connections on the DM's Yes (`MemoryStore.make_kind_of_words`).

Counts only: the words are the ones already stored on the campaign's connections, folded
(`fold`), and nothing else is ever kept for an offer. `memory_kind_offers` holds only the DM's
Not now and Never, and a row whose words have left the campaign is deleted by the same sweep that
counts, so nothing outlives its words. The sweep reads connections and kinds only; it never reads
a transcript.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass

from dmbot.memory._changes import ENTITIES, RELATIONS, Scope
from dmbot.memory.models import CONFIRMED
from dmbot.memory.ontology import FALLBACK_PREDICATE

MIN_COUNT = 3  # the same words this many times make an offer
GROWTH = 3  # after "Not now": asked again only once the count has grown by this much
LATER, NEVER = "later", "never"


def fold(detail: str) -> str:
    """The words as stored on a connection, with case and spacing folded: what two connections
    must share to count as the same words."""
    return " ".join(detail.casefold().split())


@dataclass(frozen=True, slots=True)
class Offer:
    words_key: str  # the folded words (the key of an answer)
    words: str  # the words as most often written, for the question
    count: int
    relation_ids: tuple[str, ...]
    subject_types: tuple[str, ...]  # the kinds every one of them starts from
    object_types: tuple[str, ...]  # ... and ends at


def wanted(count: int, state: str | None, seen: int) -> bool:
    """Whether words said `count` times should be offered, given the DM's earlier answer."""
    if count < MIN_COUNT or state == NEVER:
        return False
    return state != LATER or count >= seen + GROWTH


async def sweep(scope: Scope, covered: Collection[str] = ()) -> list[Offer]:
    """The words worth offering now, most-said first, and the cleanup: an answer whose words are no
    longer on any live catch-all connection is deleted. `covered`: folded words an existing kind
    already means (they are never offered, whatever the count). Offers are not made here for words
    whose connections share no start kind or no end kind."""
    rows = await scope.select(
        RELATIONS,
        " AND predicate = %s AND status = %s AND to_session_at IS NULL AND detail <> ''",
        [FALLBACK_PREDICATE, CONFIRMED],
    )
    kinds = {
        str(e["id"]): str(e["type"])
        for e in await scope.select(ENTITIES, " AND status = %s", [CONFIRMED])
    }
    by_words: dict[str, list[dict[str, object]]] = {}
    for r in rows:
        if str(r["subject_id"]) in kinds and str(r["object_id"]) in kinds:
            by_words.setdefault(fold(str(r["detail"])), []).append(r)
    answers = await scope.conn.execute(
        "SELECT words_key, state, seen FROM memory_kind_offers"
        " WHERE guild_id = %s AND campaign_id = %s",
        scope.ids,
    )
    held = {
        str(a["words_key"]): (str(a["state"]), int(a["seen"])) for a in await answers.fetchall()
    }
    gone = [k for k in held if k not in by_words]
    if gone:  # (nothing is kept for words the campaign no longer has)
        await scope.conn.execute(
            "DELETE FROM memory_kind_offers WHERE guild_id = %s AND campaign_id = %s"
            " AND words_key = ANY(%s)",
            (*scope.ids, gone),
        )
    found: list[Offer] = []
    for key, group in by_words.items():
        state, seen = held.get(key, (None, 0))
        if key in covered or not wanted(len(group), state, seen):
            continue
        subjects = set.intersection(*({kinds[str(r["subject_id"])]} for r in group))
        objects = set.intersection(*({kinds[str(r["object_id"])]} for r in group))
        if not subjects or not objects:
            continue
        written = Counter(str(r["detail"]) for r in group).most_common(1)[0][0]
        found.append(
            Offer(
                key, written, len(group), tuple(sorted(str(r["id"]) for r in group)),
                tuple(sorted(subjects)), tuple(sorted(objects)),
            )
        )  # fmt: skip
    return sorted(found, key=lambda o: (-o.count, o.words_key))


async def answer(scope: Scope, words_key: str, state: str, seen: int, now: int) -> None:
    """Remember the DM's "Not now" (`later`, with the count seen) or "Never" for these words."""
    await scope.conn.execute(
        "INSERT INTO memory_kind_offers (guild_id, campaign_id, words_key, state, seen, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (guild_id, campaign_id, words_key)"
        " DO UPDATE SET state = EXCLUDED.state, seen = EXCLUDED.seen",
        (*scope.ids, words_key[:300], state, seen, now),
    )


async def clear(scope: Scope, words_key: str) -> bool:
    """The DM lifts a Never (or a Not now): the words may be offered again. Whether a row went."""
    cur = await scope.conn.execute(
        "DELETE FROM memory_kind_offers WHERE guild_id = %s AND campaign_id = %s"
        " AND words_key = %s",
        (*scope.ids, words_key),
    )
    return cur.rowcount > 0
