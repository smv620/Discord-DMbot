"""A short, plain summary of one node of the story graph, built from the graph alone (#3236).

Clicking a node should say who or what it is and how it fits the story, in two or three sentences.
This first version is a template: no AI, no storage, nothing to go stale. It is pure, so it is
tested without a database; the website door reads the graph through the gate, hands this module
only what the reader may see, and the module sorts and words it. (An AI wording pass comes later
and would be handed the same sorted list.)

What the text says, in order:
- the node: its name and kind (and, for the DM only, the roles it holds);
- up to `MAX_NEIGHBOURS` entries joined to it, most important first, each with up to `MAX_FACTS`
  short facts about how they are joined, then "and N more";
- for each of those, the single best entry joined to *it* (the second hop), not already named.

Sorting. A neighbour sorts by (its rank, highest first, DM version only), the best category of its
facts, its connection score, the newest session it was in, and its name. The player version leaves
the rank out of the order: the rank knows DM secrets, so an order built from it could hint that an
innocent-looking neighbour matters. A fact sorts by its category (the table below, worked out from
the connection's type every time and never stored, so reordering it needs no migration), then
newest session first, then facts with a detail before bare ones, then its text, so the same node
always reads the same.

Who may see what is decided by the caller, through the gate: a fact or neighbour the players
cannot see arrives marked `players_know=False` and never reaches the player version, and the
player version says nothing of rank or roles. `needs_peek` is the spoiler check.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from dmbot.memory import reputation_words

DM, PLAYER = "dm", "player"
MAX_NEIGHBOURS = 8  # the neighbours named; the rest are counted ("and 12 more")
MAX_FACTS = 3  # the facts shown for one neighbour; the rest are counted
MIN_RANK = 1  # a neighbour must be at least Minor (the DM version); the player version has no rank

# The categories, most important first. A lookup from the connection's type, never stored.
LASTING, LIFE_AND_DEATH, STANDING, ROLES, OTHER_DEEDS, BELIEFS, OTHER = range(1, 8)
CATEGORY_NAMES = {
    LASTING: "lasting ties",
    LIFE_AND_DEATH: "life and death",
    STANDING: "where they stand now",
    ROLES: "roles",
    OTHER_DEEDS: "other deeds",
    BELIEFS: "beliefs",
    OTHER: "other connections",
}

# A deed's kind decides its category; these kinds are matters of life and death.
LIFE_AND_DEATH_DEEDS = frozenset({"attack", "kill", "rescue", "save"})
_ROLE_KINDS = frozenset({"mentor_of", "rival_of", "leads", "serves", "travels_with", "sworn_to"})
_STANDING_KINDS = frozenset({"knows", "ally_of", "enemy_of"})  # (the old two read as feelings)

# Free-text links carry their meaning in the detail. Until the meaning match exists (#3236 slice 4),
# whole words (never parts of a word: "skilled" is not "kill") put the obvious ones in the right
# category; anything else is "other". Negation ("never killed") is not understood: that is the
# meaning match's job.
_WORDS: tuple[tuple[int, frozenset[str]], ...] = (
    (
        LIFE_AND_DEATH,
        frozenset(
            [
                "kill",
                "kills",
                "killed",
                "killer",
                "murder",
                "murdered",
                "murderer",
                "saved",
                "rescued",
                "assassin",
                "assassinated",
            ]
        ),
    ),
    (
        LASTING,
        frozenset(
            [
                "sibling",
                "siblings",
                "brother",
                "brothers",
                "sister",
                "sisters",
                "spouse",
                "husband",
                "wife",
                "married",
                "lover",
                "lovers",
                "parent",
                "parents",
                "mother",
                "father",
                "child",
                "children",
                "cousin",
                "cousins",
                "twin",
                "twins",
            ]
        ),
    ),
    (
        ROLES,
        frozenset(
            [
                "mentor",
                "mentors",
                "apprentice",
                "student",
                "teacher",
                "employer",
                "boss",
                "leader",
                "patron",
            ]
        ),
    ),
    (
        OTHER_DEEDS,
        frozenset(["stole", "stolen", "betrayed", "betrayal", "helped", "robbed", "deceived"]),
    ),
    (
        STANDING,
        frozenset(
            [
                "friend",
                "friends",
                "enemy",
                "enemies",
                "ally",
                "allies",
                "trust",
                "trusts",
                "trusted",
                "hate",
                "hates",
                "hated",
                "rival",
                "rivals",
            ]
        ),
    ),
)

# How a connection reads when the neighbour is the object (`focal` is the subject) and when the
# neighbour is the subject. Said of the neighbour, about the node being summarised.
_PHRASES: dict[str, tuple[str, str]] = {
    "mentor_of": ("their apprentice", "their mentor"),
    "rival_of": ("their rival", "their rival"),
    "leads": ("led by them", "their leader"),
    "serves": ("their servant", "their master"),
    "travels_with": ("travels with them", "travels with them"),
    "sworn_to": ("sworn to them", "sworn to them"),
    "member_of": ("in their group", "their group"),
    "located_in": ("lives there", "where they live"),
    "owns": ("owned by them", "owns it"),
    "ally_of": ("allied with them", "allied with them"),
    "enemy_of": ("an enemy of theirs", "an enemy of theirs"),
}
_FEELING_OUT = {  # the node toward the neighbour
    "sworn enemy": "their sworn enemy", "hostile": "someone they are hostile toward",
    "distrusts": "distrusted by them", "wary": "watched warily by them",
    "neutral": "", "trusts": "trusted by them", "friendly": "liked by them",
    "close": "close to them", "devoted": "someone they are devoted to",
}  # fmt: skip
_FEELING_IN = {  # the neighbour toward the node
    "sworn enemy": "their sworn enemy", "hostile": "hostile toward them",
    "distrusts": "distrusts them", "wary": "wary of them", "neutral": "",
    "trusts": "trusts them", "friendly": "likes them", "close": "close to them",
    "devoted": "devoted to them",
}  # fmt: skip
_ADJECTIVE = {  # the nine words, as they read in "was ___ toward them, now ___"
    "sworn enemy": "a sworn enemy", "hostile": "hostile", "distrusts": "distrustful",
    "wary": "wary", "neutral": "neutral", "trusts": "trusting", "friendly": "friendly",
    "close": "close", "devoted": "devoted",
}  # fmt: skip
_DEED_OUT = {  # the node did it to the neighbour
    "attack": "attacked by them", "kill": "killed by them", "rescue": "rescued by them",
    "save": "saved by them", "steal": "robbed by them", "help": "helped by them",
}  # fmt: skip
_DEED_IN = {  # the neighbour did it to the node
    "attack": "attacked them", "kill": "killed them", "rescue": "rescued them",
    "save": "saved them", "steal": "robbed them", "help": "helped them",
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Fact:
    """One thing known about how a neighbour is joined to the node being summarised."""

    kind: str  # the connection type ("mentor_of", "kin_of", "knows", "did", "associated_with", ...)
    outward: bool = True  # the node is the subject (it mentors / knows / did it); else the object
    detail: str = ""  # the family role, the free-text label, the feeling word on `knows`
    deed: str = ""  # a deed's kind: attack, kill, rescue, steal, help
    belief: bool = False  # what one character believes of the other, not a fact
    session: int | None = None  # the newest session it was in (for "most recent first")
    ended: bool = False  # said as "formerly ...", after the current ones of its category
    # Both default to False: a fact the caller has not marked is never shown to a player.
    players_know: bool = False  # some player character knows it (the caller decides, via the gate)
    viewer_knows: bool = False  # the person who clicked has a character that knows it


@dataclass(frozen=True, slots=True)
class Neighbour:
    """An entry joined to the node (or, for the second hop, to a neighbour)."""

    id: str
    name: str
    kind: str  # the entry's kind ("character", "place", ...)
    rank: int = 0  # 0 to 5; used for eligibility and order in the DM version only
    score: float = 0.0  # the connection score (`importance.py`) over every connection: DM order
    # The same score over the connections the players can see only: the player order. Separate
    # fields, so an order for players can never be built from a hidden connection.
    player_score: float = 0.0
    facts: tuple[Fact, ...] = ()
    players_know: bool = False  # the players know this entry exists (and may be told its name)
    further: tuple[Neighbour, ...] = ()  # entries joined to this one, for the second hop


@dataclass(frozen=True, slots=True)
class Focus:
    """The node being summarised."""

    id: str
    name: str
    kind: str
    roles: tuple[str, ...] = ()  # the DM version only (#3172)


@dataclass(frozen=True, slots=True)
class Summary:
    text: str
    named: tuple[str, ...] = field(default=())  # the neighbours named (first hop, then second)
    more: int = 0  # neighbours left out
    needs_peek: bool = False  # a fact the players hold that this person's characters do not


def category_of(fact: Fact) -> int:
    """Which of the seven categories a fact falls in: a lookup on its type, then the word list for
    free text. Pure, and the one place the order of importance between kinds of fact is written."""
    if fact.belief:
        return BELIEFS
    if fact.kind == "kin_of":
        return LASTING
    if fact.kind == "did":
        return LIFE_AND_DEATH if fact.deed in LIFE_AND_DEATH_DEEDS else OTHER_DEEDS
    if fact.kind in _STANDING_KINDS:
        return STANDING
    if fact.kind in _ROLE_KINDS:
        return ROLES
    tokens = set(re.findall(r"[a-z]+", fact.detail.casefold()))
    for category, words in _WORDS:
        if tokens & words:
            return category
    return OTHER


def _wording(fact: Fact) -> str:
    """The fact as a short phrase, said of the neighbour about the node ("their apprentice")."""
    if fact.kind == "knows":
        word = reputation_words.word_of(fact.detail)
        table = _FEELING_OUT if fact.outward else _FEELING_IN
        return table.get(word or "", "")
    if fact.kind == "did" and fact.deed:
        table = _DEED_OUT if fact.outward else _DEED_IN
        return table.get(fact.deed, fact.detail or "")
    if fact.kind == "kin_of":
        return fact.detail.strip() or "family"
    if fact.kind in _PHRASES:
        return _PHRASES[fact.kind][0 if fact.outward else 1]
    return fact.detail.strip()


def _when(fact: Fact) -> int:
    return fact.session if fact.session is not None else -1


def _key(item: tuple[Fact, str]) -> tuple[int, bool, int, int, str]:
    fact, text = item
    return (category_of(fact), fact.ended, -_when(fact), 0 if fact.detail else 1, text)


def _change(group: Sequence[Fact], focus: str) -> str | None:
    """ "Mira was hostile toward them, now friendly" when a feeling changed sign (one direction)."""
    ordered = sorted(group, key=_when)
    first, last = ordered[0], ordered[-1]
    if reputation_words.sign_of(first.detail) == reputation_words.sign_of(last.detail):
        return None
    before = _ADJECTIVE.get(reputation_words.word_of(first.detail) or "", "neutral")
    after = _ADJECTIVE.get(reputation_words.word_of(last.detail) or "", "neutral")
    if last.outward:
        return f"{focus} was {before} toward them, now {after}"
    return f"was {before} toward {focus}, now {after}"


def facts_to_show(facts: Sequence[Fact], focus: str = "") -> list[tuple[Fact, str]]:
    """The facts between two entries as (fact, words), sorted. The history of a feeling is one line
    for each direction (the newest feeling, or the change when its sign turned), a neutral one that
    changed nothing says nothing, and a fact with no words is left out. Pure."""
    out: list[tuple[Fact, str]] = []
    feelings: dict[bool, list[Fact]] = {}
    for f in facts:
        if f.kind == "knows":
            feelings.setdefault(f.outward, []).append(f)
        elif text := _wording(f):
            out.append((f, f"formerly {text}" if f.ended else text))
    for group in feelings.values():
        newest = max(group, key=_when)
        text = _change(group, focus or "they") or _wording(newest)
        if text:
            out.append((newest, text))
    return sorted(out, key=_key)


def _best_category(n: Neighbour, focus: str) -> int:
    return min((category_of(f) for f, _ in facts_to_show(n.facts, focus)), default=OTHER + 1)


def _newest(n: Neighbour) -> int:
    return max((f.session for f in n.facts if f.session is not None), default=-1)


def _order(
    audience: str, focus: str
) -> Callable[[Neighbour], tuple[int, int, float, int, str, str]]:
    def key(n: Neighbour) -> tuple[int, int, float, int, str, str]:
        rank = -n.rank if audience == DM else 0  # (the player order never sees the rank)
        score = n.score if audience == DM else n.player_score
        return (rank, _best_category(n, focus), -score, -_newest(n), n.name.casefold(), n.id)

    return key


def _visible(n: Neighbour, audience: str) -> Neighbour | None:
    """The neighbour as this audience may see it, or None; its own neighbours are cut the same way.
    The DM sees everything at Minor or above; a player sees only what some player character knows,
    and a neighbour is dropped when the players do not know it exists or know none of the ways it
    is joined."""
    further = tuple(v for v in (_visible(x, audience) for x in n.further) if v is not None)
    if audience == DM:
        return replace(n, further=further) if n.rank >= MIN_RANK else None
    facts = tuple(f for f in n.facts if f.players_know)
    if not n.players_know or not facts:
        return None
    return Neighbour(n.id, n.name, n.kind, 0, 0.0, n.player_score, facts, True, further)


def needs_peek(neighbours: Sequence[Neighbour]) -> bool:
    """Whether this node has a fact the players hold that the person who clicked does not, in the
    neighbours or in the second hop: the summary then sits behind "Contains spoilers … let me peek
    anyway". Looks at every entry the players know, whatever the caps leave out of the text."""
    for n in neighbours:
        if not n.players_know:
            continue
        if any(f.players_know and not f.viewer_knows for f in n.facts) or needs_peek(n.further):
            return True
    return False


def _clause(n: Neighbour, focus: str) -> str:
    words = [text for _, text in facts_to_show(n.facts, focus)]
    kept = words[:MAX_FACTS]
    left = len(words) - len(kept)
    if not kept:
        return n.name
    return f"{n.name} ({'; '.join(kept)}{f'; +{left} more' if left else ''})"


def _join(parts: Sequence[str]) -> str:
    if len(parts) <= 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def summarize(focus: Focus, neighbours: Sequence[Neighbour], audience: str) -> Summary:
    """The summary text for `focus` as `audience` (`DM` or `PLAYER`) may read it. `neighbours` are
    the entries joined to the node, each with its facts and, optionally, its own neighbours. The
    spoiler check (`Summary.needs_peek`) is worked out here for a player, from the same entries the
    text is built from, so a caller cannot forget it. Pure."""
    pool = [
        v for v in (_visible(n, audience) for n in neighbours if n.id != focus.id) if v is not None
    ]
    pool.sort(key=_order(audience, focus.name))
    shown, rest = pool[:MAX_NEIGHBOURS], pool[MAX_NEIGHBOURS:]
    peek = audience == PLAYER and needs_peek(list(neighbours))
    head = f"{focus.name} ({focus.kind})."
    if audience == DM and focus.roles:
        head += f" Roles: {', '.join(focus.roles)}."
    if not shown:
        return Summary(head, (), len(rest), peek)
    named = [n.name for n in shown]
    taken = {x.casefold() for x in named} | {focus.name.casefold()}
    parts: list[str] = []
    for n in shown:
        clause = _clause(n, focus.name)
        also = [
            x
            for x in sorted(n.further, key=_order(audience, focus.name))
            if x.id != focus.id and x.name.casefold() not in taken
        ]
        if also:
            clause += f", through them {also[0].name}"
            named.append(also[0].name)
            taken.add(also[0].name.casefold())
        parts.append(clause)
    if rest:
        parts.append(f"{len(rest)} more")
    return Summary(f"{head} Joined to {_join(parts)}.", tuple(named), len(rest), peek)
