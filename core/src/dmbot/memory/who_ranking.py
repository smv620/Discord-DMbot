"""Who a name could be: the candidates for a name that waits on "Who is this?", pre-ranked by code
(#3602, slice 1). The AI that works out who a name is (slice 2) is given at most the best five of
these, so it chooses between a few real entries instead of the whole campaign, and the DM's card
and the website (slices 3 and 4) show the same list in the same order.

Pure: no database, no AI, no Discord. It works on plain values the caller reads for one campaign
(its entries, who was in the scene, what was said in the same session), so a candidate can never
come from another campaign, and nothing here reads a transcript: the words it compares are names.
A hidden or secret entry is a candidate for the DM's ranking only; it is marked `hidden`, so no
screen but the DM's may show its name. The reasons are fixed plain words, never a quote.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from dmbot.memory.models import name_key

LIMIT = 5  # the most candidates the AI and the cards are given

# The weights (points). A shared word is the strongest sign; being in the scene, or tied to who
# the name belongs to, comes next; a mention in the same session is the weakest.
SHARED_WORD = 3.0  # every word of the name is among the candidate's; less is scaled down
PREFIX_WORD = 0.5  # a word that only starts the candidate's word counts this share of a whole one
SAME_SCENE = 1.5
BELONGS_TO = 2.0
ONE_CONNECTION = 1.0  # for each connection to someone in the scene, up to `MAX_CONNECTIONS`
MAX_CONNECTIONS = 3
SAME_SESSION = 0.5
KIND_FITS = 0.5
COMMON_WORD_SHARE = 0.34  # a word in this share of the candidates' names (or more) is scaled down

# What a reason says, in plain words, for the card.
WHY_WORD = "shares a word with the name"
WHY_SCENE = "was in the scene"
WHY_BELONGS = "is tied to who it belongs to"
WHY_CONNECTED = "is tied to someone in the scene"
WHY_SESSION = "was mentioned in the same session"
WHY_KIND = "is the kind of thing it sounds like"

# Words that say nothing about who it is: articles, owner words, the commonest filler.
_FILLER = frozenset(
    {"a", "an", "the", "of", "and", "his", "her", "their", "its", "my", "our", "your", "s"}
)
_POSSESSIVE = re.compile(r"['\u2019\u02bc]s\b|(?<=s)['\u2019\u02bc](?=\s|$)")
_PREFIX = 4  # a word this long that starts a longer one is a weak match ("Brew", "Brewner")
_MIN_WORD = 2  # a fragment shorter than this is no word (a script that splits into marks)
# The kinds a name of one family can be. The legacy names (`npc`, `player_character`, `deity`,
# `creature`) and `person` (a player or the DM) are characters; `people` is a group.
_FAMILY = {
    "character": "character", "npc": "character", "player_character": "character",
    "deity": "character", "creature": "character", "person": "character",
    "faction": "group", "people": "group",
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Candidate:
    """An entry the name could be. `names`: its main name and its other names (the visible ones
    and, for a hidden candidate ONLY, the secret ones too: a visible entry's secret names must
    never be put here, since "shares a word" would confirm them). `ties`: the ids of the entries
    it has a confirmed, VISIBLE connection with (a secret tie would show as a reason). `hidden`:
    the entry is the DM's eyes only (secret, hidden from the players, a disguise)."""

    id: str
    kind: str
    names: tuple[str, ...]
    ties: frozenset[str] = frozenset()
    hidden: bool = False  # secret, hidden from the players, a disguise: the DM's eyes only


@dataclass(frozen=True, slots=True)
class Ask:
    """The name to place and what is known around it. `kind`: what it sounds like ("" or
    "concept" when unsure). `scene`: the ids of the entries in its scene (who was there).
    `session`: the ids mentioned in the same session. `owner`: the ids it belongs to
    ("Brewner's daughter": Brewner)."""

    name: str
    kind: str = ""
    scene: frozenset[str] = frozenset()
    session: frozenset[str] = frozenset()
    owner: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Ranked:
    candidate: Candidate
    score: float
    reasons: tuple[str, ...]


def words_of(name: str) -> frozenset[str]:
    """The words of a name that say who it is: lower case, without accents or apostrophes, with
    articles, owner words and one-character fragments left out."""
    owned = _POSSESSIVE.sub("", name)  # ("Brewner's" says Brewner, not "brewners")
    return frozenset(
        w for w in re.findall(r"\w+", name_key(owned)) if w not in _FILLER and len(w) >= _MIN_WORD
    )


def _match(a: str, b: str) -> float:
    """How well two words match: 1 the same word, `PREFIX_WORD` when the name's word (the shorter
    one, at least `_PREFIX` letters) only starts the other, else 0."""
    if a == b:
        return 1.0
    if len(a) >= _PREFIX and len(b) > len(a) and b.startswith(a):
        return PREFIX_WORD
    return 0.0


def _word_score(ask_words: Collection[str], names: Iterable[str], common: Collection[str]) -> float:
    """The share of the name's words the candidate's names hold (each counted once; a common word
    counts a third), as a number from 0 to 1."""
    theirs = {w for n in names for w in words_of(n)}
    if not ask_words:
        return 0.0
    total = got = 0.0
    for a in ask_words:
        weight = 1 / 3 if a in common else 1.0
        total += weight
        got += weight * max((_match(a, b) for b in theirs), default=0.0)
    return got / total if total else 0.0


def _family(kind: str) -> str:
    return _FAMILY.get(kind, kind)


def _fits(ask_kind: str, kind: str) -> bool | None:
    """True: the kinds fit. None: the ask is unsure of its kind. False: they cannot be the same."""
    if ask_kind in ("", "concept"):
        return None
    return _family(ask_kind) == _family(kind)


def rank(
    ask: Ask,
    candidates: Iterable[Candidate],
    limit: int = LIMIT,
    *,
    include_hidden: bool = False,
) -> list[Ranked]:
    """The best `limit` candidates for the name, best first: highest score, then (for a steady
    order) the shorter main name, then the id. A candidate with no sign at all is left out, so the
    list may be short or empty. A hidden candidate is left out unless `include_hidden`: only the
    DM's own ranking asks for it, and what it returns must not leave the DM's screens. A repeated
    id counts once (the first). The entry the name belongs to is never a candidate for it."""
    ask_words = words_of(ask.name)
    pool: dict[str, Candidate] = {}
    for c in candidates:
        if c.id not in pool and c.id not in ask.owner and (include_hidden or not c.hidden):
            pool[c.id] = c
    # a word in many of the candidates' names says little ("old", "captain", "lord")
    spread = Counter(w for c in pool.values() for w in {w for n in c.names for w in words_of(n)})
    common = (
        {w for w in ask_words if spread[w] >= max(2, COMMON_WORD_SHARE * len(pool))}
        if len(pool) >= 3
        else set()
    )
    out: list[Ranked] = []
    for c in pool.values():
        fits = _fits(ask.kind, c.kind)
        if fits is False:
            continue
        score = 0.0
        reasons: list[str] = []
        word = _word_score(ask_words, c.names, common)
        if word > 0:
            score += SHARED_WORD * word
            reasons.append(WHY_WORD)
        if c.id in ask.scene:
            score += SAME_SCENE
            reasons.append(WHY_SCENE)
        if ask.owner & c.ties:
            score += BELONGS_TO
            reasons.append(WHY_BELONGS)
        near = len(ask.scene & c.ties)  # (the scene's own people, not the whole session)
        if near:
            score += ONE_CONNECTION * min(near, MAX_CONNECTIONS)
            reasons.append(WHY_CONNECTED)
        if c.id in ask.session:
            score += SAME_SESSION
            reasons.append(WHY_SESSION)
        if not reasons:
            continue  # nothing links it to the name: never offered on its kind alone
        if fits:
            score += KIND_FITS
            reasons.append(WHY_KIND)
        out.append(Ranked(c, score, tuple(reasons)))
    out.sort(
        key=lambda r: (
            -r.score,
            len(r.candidate.names[0]) if r.candidate.names else 0,
            r.candidate.id,
        )
    )
    return out[:limit]
