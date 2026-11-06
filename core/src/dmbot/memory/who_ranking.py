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
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from dmbot.memory.models import name_key

LIMIT = 5  # the most candidates the AI and the cards are given

# The weights (points). A shared word is the strongest sign; being in the scene, or tied to who
# the name belongs to, comes next; a mention in the same session is the weakest.
SHARED_WORD = 3.0
SAME_SCENE = 1.5
BELONGS_TO = 2.0
ONE_CONNECTION = 1.0  # for each connection to someone in the scene, up to `MAX_CONNECTIONS`
MAX_CONNECTIONS = 3
SAME_SESSION = 0.5
KIND_FITS = 0.5

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
_POSSESSIVE = re.compile(r"['’]s\b")
_PREFIX = 4  # a word this long that starts another counts as the same word ("Brew", "Brewner")
# A name of one kind is never a candidate for another that cannot be it ("a place" for a person).
_COMPATIBLE: dict[str, frozenset[str]] = {
    "character": frozenset({"character"}),
    "place": frozenset({"place"}),
    "faction": frozenset({"faction"}),
    "item": frozenset({"item"}),
}


@dataclass(frozen=True, slots=True)
class Candidate:
    """An entry the name could be. `names`: its main name and its other names (the visible ones
    and, for a hidden candidate, the secret ones too). `ties`: the ids of the entries it has a
    confirmed connection with."""

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
    articles and owner words left out."""
    owned = _POSSESSIVE.sub("", name)  # ("Brewner's" says Brewner, not "brewners")
    return frozenset(w for w in re.findall(r"\w+", name_key(owned)) if w not in _FILLER)


def _same_word(a: str, b: str) -> bool:
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return len(short) >= _PREFIX and long.startswith(short)


def _shares_a_word(ask_words: Collection[str], names: Iterable[str]) -> bool:
    theirs = {w for n in names for w in words_of(n)}
    return any(_same_word(a, b) for a in ask_words for b in theirs)


def _fits(ask_kind: str, kind: str) -> bool | None:
    """True: the kinds fit. None: the ask is unsure of its kind. False: they cannot be the same."""
    if ask_kind in ("", "concept"):
        return None
    return kind in _COMPATIBLE.get(ask_kind, frozenset({ask_kind}))


def rank(ask: Ask, candidates: Iterable[Candidate], limit: int = LIMIT) -> list[Ranked]:
    """The best `limit` candidates for the name, best first: highest score, then (for a steady
    order) the shorter main name, then the id. A candidate with no sign at all is left out, so the
    list may be short or empty."""
    ask_words = words_of(ask.name)
    out: list[Ranked] = []
    for c in candidates:
        fits = _fits(ask.kind, c.kind)
        if fits is False:
            continue
        score = 0.0
        reasons: list[str] = []
        if ask_words and _shares_a_word(ask_words, c.names):
            score += SHARED_WORD
            reasons.append(WHY_WORD)
        if c.id in ask.scene:
            score += SAME_SCENE
            reasons.append(WHY_SCENE)
        if ask.owner & c.ties:
            score += BELONGS_TO
            reasons.append(WHY_BELONGS)
        near = len((ask.scene | ask.session) & c.ties)
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
