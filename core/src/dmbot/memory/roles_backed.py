"""The story roles a connection shows, worked out on read and never stored (#3172, item b,
owner-approved 2026-11-01). Pure.

A backed role holds while its connection is confirmed and current, and ends when the connection
ends: the quest is done, the "king" leads nothing. The DM can open the connection and its lines,
which is how the graph knows WHY someone is a quest giver. The rules, from the agreed table on
#3172:

| role | backed when |
|---|---|
| quest giver / target | `in_thread` whose part names it ("quest giver", "patron"; "target") |
| mentor | `mentor_of` a player character (or a companion) |
| rival | `rival_of` a player character (or a companion), either way round |
| antagonist | `knows` hostile or sworn enemy FROM the entry TO a player character, or `enemy_of` |
| minion | `serves` an antagonist (worked out after the antagonists) |
| ruler | `leads` a group or a place |
| companion | `travels_with` (the store lets it point only at a player character or the party) |
| ally | `ally_of` a player character, or `knows` trusts, friendly, close or devoted to one |
| captive | `captive_of` anyone |

Direction matters where the table says so: a player character hating someone does not make that
someone an antagonist. A player character holds none of these (it is a player character). Every
end is read as the entry it was merged into, so a joined pair's connections back the one that
stayed. Roles are for the DM only, like the importance rank, and never add to it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping

from dmbot.memory import reputation_words as rw
from dmbot.memory.importance_rank import roots

QUEST_GIVER, QUEST_TARGET, MENTOR, RIVAL, ANTAGONIST, MINION, RULER, COMPANION, ALLY, CAPTIVE = (
    "quest_giver", "quest_target", "mentor", "rival", "antagonist", "minion", "ruler",
    "companion", "ally", "captive",
)  # fmt: skip
BACKABLE = frozenset(
    {QUEST_GIVER, QUEST_TARGET, MENTOR, RIVAL, ANTAGONIST, MINION, RULER, COMPANION, ALLY, CAPTIVE}
)
# The words of an `in_thread` part that name a quest role (the DM types the part; `involve`).
# Whole words, case aside; a part that names neither stays a stored role, if the reading found one.
GIVER_WORDS = ("quest giver", "patron")
TARGET_WORDS = ("quest target", "target")
HOSTILE_WORDS = frozenset({rw.HOSTILE, rw.SWORN_ENEMY})
FRIENDLY_WORDS = frozenset({rw.TRUSTS, rw.FRIENDLY, rw.CLOSE, rw.DEVOTED})

Backed = dict[str, dict[str, list[str]]]  # entry -> role -> the connection ids that show it


def _names(detail: str, words: tuple[str, ...]) -> bool:
    plain = f" {' '.join(detail.casefold().replace('-', ' ').split())} "
    return any(f" {w} " in plain for w in words)


def backed_roles(
    entities: Mapping[str, Mapping[str, object]], relations: Iterable[Mapping[str, object]]
) -> Backed:
    """The roles each live entry holds by its connections. `entities`: id -> a row with `type`,
    `role`, `status` and `merged_into` (live and merged entries); `relations`: rows with `id`,
    `subject_id`, `predicate`, `object_id`, `detail`, `status` and `to_session_at`. Only confirmed,
    current connections count. One pass over the whole campaign: a view of many entries calls
    this once, never once per entry."""
    ended = roots(entities)
    out: Backed = defaultdict(lambda: defaultdict(list))

    def player(eid: str) -> bool:
        row = entities.get(eid)
        return row is not None and row["type"] == "character" and row["role"] == "player_character"

    def give(eid: str, role: str, rid: str) -> None:
        if not player(eid):
            out[eid][role].append(rid)

    live = []
    for r in relations:
        if r["status"] != "confirmed" or r["to_session_at"] is not None:
            continue
        a, b = ended.get(str(r["subject_id"])), ended.get(str(r["object_id"]))
        if a is None or b is None or a == b:
            continue
        live.append((str(r["id"]), a, str(r["predicate"]), b, str(r["detail"] or "")))
    # pass 1: companions (mentor and rival accept one at the far end)
    for rid, a, how, _b, _ in live:
        if how == "travels_with":
            give(a, COMPANION, rid)
    companions = {e for e, held in out.items() if COMPANION in held}

    def party_side(eid: str) -> bool:
        return player(eid) or eid in companions

    for rid, a, how, b, detail in live:
        word = rw.word_of(detail)  # (as the other readers read it: "Sworn-Enemy" is one)
        if how == "in_thread":
            if _names(detail, GIVER_WORDS):
                give(a, QUEST_GIVER, rid)
            elif _names(detail, TARGET_WORDS):
                give(a, QUEST_TARGET, rid)
        elif how == "mentor_of" and party_side(b):
            give(a, MENTOR, rid)
        elif how == "rival_of":
            if party_side(b):
                give(a, RIVAL, rid)
            if party_side(a):
                give(b, RIVAL, rid)
        elif how == "knows" and player(b) and word in HOSTILE_WORDS:
            give(a, ANTAGONIST, rid)
        elif how == "knows" and player(b) and word in FRIENDLY_WORDS:
            give(a, ALLY, rid)
        elif how == "enemy_of":  # (old rows, before #3175: either way round)
            if player(b):
                give(a, ANTAGONIST, rid)
            if player(a):
                give(b, ANTAGONIST, rid)
        elif how == "ally_of":
            if player(b):
                give(a, ALLY, rid)
            if player(a):
                give(b, ALLY, rid)
        elif how == "leads":
            kind = entities[b]["type"]
            if kind in ("faction", "place"):
                give(a, RULER, rid)
        elif how == "captive_of":
            give(a, CAPTIVE, rid)
    # pass 2: who serves an antagonist
    antagonists = {e for e, held in out.items() if ANTAGONIST in held}
    for rid, a, how, b, _ in live:
        if how == "serves" and b in antagonists:
            give(a, MINION, rid)
    return {e: dict(held) for e, held in out.items()}


__all__ = ["BACKABLE", "Backed", "backed_roles"]
