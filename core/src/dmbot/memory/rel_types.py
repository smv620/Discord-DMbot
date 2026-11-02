"""Matching a relationship's one-sentence description to a type by meaning (#3244, stage 1 of
#3242). Pure: the embedder is given, so nothing here calls Voyage or touches a database.

The reader writes one sentence per tie in context ("The elder addresses the young adventurer as
'my child': a form of address, not a family tie"). That sentence and each type's long text
(`rel_type_texts`: what the type *is*, with its examples, never its `not_this`) are turned into
numbers, and the closest type wins when it is both close enough (`BAR`) and clearly ahead of the
second (`MARGIN`). Otherwise the reader's own pick stands, as it does today: a weak match is never
dropped and never guessed. A form of address is only counted, not stored. `ally_of` and `enemy_of`
are not targets (an alliance or enmity is `knows` with a stance word, #3175), and neither are
`located_in` or `travels_with`.

The owner's 80% is a **precision**: of the matches that get used, at least 80% must be right
(`PRECISION_TARGET`). The cosine that achieves it is not known in advance, so `THRESHOLD` and
`MARGIN` are starting values that `choose` sets from the golden set (`rel_golden`) in dev1's
measured run with the real embeddings: the loosest pair that still meets the target, which covers
the most ties. Nothing is turned on before then.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from dmbot.memory import reputation_words
from dmbot.memory.rel_type_texts import BY_KEY, TERM_OF_ADDRESS, RelType
from dmbot.memory.voyage import cosine

PRECISION_TARGET = 0.80  # of the matches used, this share must be right (owner, 2026-11-02)
THRESHOLD = 0.60  # the lowest cosine that can be used: a starting value, set by `choose`
MARGIN = 0.03  # the lead over the second best: a starting value, set by `choose`

USED = "embedding"
AI_PICK = "ai"

# The stance words the type text lists that are not one of the nine feeling words, and the word
# each is stored as (owner's rule 2 on #3244): the reader returns the word, and the nine are all
# that is ever stored. Anything else is looked at as one of the nine or left unset.
STANCE_TO_NINE: Mapping[str, str] = {
    "at war": reputation_words.HOSTILE,
    "enemies": reputation_words.HOSTILE,
    "allies": reputation_words.FRIENDLY,
    "ally": reputation_words.FRIENDLY,
    "partners in a plan": reputation_words.TRUSTS,
    "companions in a cause": reputation_words.TRUSTS,
    "friends": reputation_words.FRIENDLY,
    "lovers": reputation_words.CLOSE,
    "lover": reputation_words.CLOSE,
    "sweethearts": reputation_words.CLOSE,
    "old flame": reputation_words.CLOSE,
    "friend": reputation_words.FRIENDLY,
    "sworn enemies": reputation_words.SWORN_ENEMY,
}


def embed_text(t: RelType) -> str:
    """What is embedded for a type: what it is, then its examples. Never `not_this`: its words
    ("a form of address", "working for someone") would pull the vector toward the very type it
    must be told apart from."""
    return t.is_text + " " + " ".join(t.examples)


def text_hash(t: RelType) -> str:
    """Names the embedded text, so a changed type text is embedded again and never mixed."""
    return hashlib.sha256(embed_text(t).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Match:
    key: str
    score: float
    second_key: str
    second_score: float


def nearest(vector: Sequence[float], vectors: Mapping[str, Sequence[float]]) -> Match | None:
    """The closest type and the runner-up, or None when there are fewer than two types, a vector
    is the wrong length or has a number that is not finite (the reader's own pick then stands).
    Equal scores are ordered by key, so the result never depends on the order of the input."""
    try:
        scored = [(cosine(vector, v), k) for k, v in vectors.items() if k in BY_KEY]
    except ValueError:  # (a vector of another length)
        return None
    if len(scored) < 2 or not all(math.isfinite(s) for s, _ in scored):
        return None
    scored.sort(key=lambda sk: (-sk[0], sk[1]))
    (s1, k1), (s2, k2) = scored[0], scored[1]
    return Match(k1, s1, k2, s2)


@dataclass(frozen=True, slots=True)
class Decision:
    """The kind chosen for one tie: `source` says whether the match or the reader's own pick
    decided, `reason` why the match was not used, `stored` whether the tie is kept."""

    kind: str
    source: str
    reason: str
    stored: bool


def decide(
    ai_kind: str, match: Match | None, *, threshold: float = THRESHOLD, margin: float = MARGIN
) -> Decision:
    """The type for a tie. The match decides only when its cosine is at least `threshold` and it is
    ahead of the second by at least `margin`; otherwise the reader's own `ai_kind` stands (counted
    by `reason`)."""
    keep = ai_kind != TERM_OF_ADDRESS  # (a form of address is never stored, whoever found it)
    if match is None:
        return Decision(ai_kind, AI_PICK, "no_match", keep)
    if not match.score >= threshold:  # (written so a NaN is below the threshold)
        return Decision(ai_kind, AI_PICK, "below_threshold", keep)
    if not match.score - match.second_score >= margin:
        return Decision(ai_kind, AI_PICK, "close_second", keep)
    return Decision(match.key, USED, "", match.key != TERM_OF_ADDRESS)


def nine_word(word: object) -> str | None:
    """The one of the nine feeling words a `knows` stance is stored as: the word itself when it is
    one of the nine, the mapped word for a stance the type text lists, otherwise None (nothing is
    stored, and a standing the DM set always wins). Hyphens and underscores read as spaces."""
    if not isinstance(word, str):
        return None
    clean = " ".join(re.sub(r"[-_]", " ", word.casefold()).split())
    return reputation_words.word_of(clean) or STANCE_TO_NINE.get(clean)


@dataclass(frozen=True, slots=True)
class Scored:
    """One golden case after matching: the best type with its score and the runner-up's score, and
    the type it should have been."""

    key: str
    score: float
    second_score: float
    expected: str


@dataclass(frozen=True, slots=True)
class Choice:
    threshold: float
    margin: float
    used: int  # how many matches the pair lets through (the coverage)
    precision: float  # the share of those that are right


def choose(
    cases: Sequence[Scored],
    *,
    target: float = PRECISION_TARGET,
    margins: Sequence[float] = (0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12),
) -> Choice | None:
    """The threshold and margin to use: among every pair whose used matches are right at least
    `target` of the time, the one that uses the most (so it covers the most ties), then the more
    precise, then the stricter. Thresholds tried are the scores seen. None when no pair meets the
    target (the matches are then not used at all and the reader's own types stand)."""
    best: Choice | None = None
    best_rank: tuple[int, float, float, float] | None = None
    for threshold in sorted({c.score for c in cases if math.isfinite(c.score)}):
        for margin in margins:
            used = [c for c in cases if c.score >= threshold and c.score - c.second_score >= margin]
            if not used:
                continue
            right = sum(1 for c in used if c.key == c.expected)
            precision = right / len(used)
            if precision < target:
                continue
            rank = (len(used), precision, threshold, margin)
            if best_rank is None or rank > best_rank:
                best, best_rank = Choice(threshold, margin, len(used), precision), rank
    return best
