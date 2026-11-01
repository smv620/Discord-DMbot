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

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

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
# a short word list puts the obvious ones in the right category; anything else is "other".
_KEYWORDS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (LIFE_AND_DEATH, ("kill", "murder", "saved", "rescued", "assassin")),
    (
        LASTING,
        (
            "sibling",
            "brother",
            "sister",
            "spouse",
            "husband",
            "wife",
            "married",
            "lover",
            "parent",
            "mother",
            "father",
            "child",
            "cousin",
            "twin",
        ),
    ),
    (ROLES, ("mentor", "apprentice", "student", "teacher", "employer", "boss", "leader", "patron")),
    (OTHER_DEEDS, ("stole", "betrayed", "helped", "robbed", "deceived")),
    (STANDING, ("friend", "enemy", "ally", "trust", "hate", "rival")),
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
    ended: bool = False
    players_know: bool = True  # some player character knows it (the caller decides, via the gate)
    viewer_knows: bool = True  # the person who clicked has a character that knows it


@dataclass(frozen=True, slots=True)
class Neighbour:
    """An entry joined to the node (or, for the second hop, to a neighbour)."""

    id: str
    name: str
    kind: str  # the entry's kind ("character", "place", ...)
    rank: int = 0  # 0 to 5; used for eligibility and order in the DM version only
    score: float = 0.0  # the connection score (`importance.py`), for the audience's own view
    facts: tuple[Fact, ...] = ()
    players_know: bool = True  # the players know this entry exists (and may be told its name)
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
    text = fact.detail.casefold()
    for category, words in _KEYWORDS:
        if any(w in text for w in words):
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


def _key(fact: Fact) -> tuple[int, int, int, str]:
    return (
        category_of(fact),
        -(fact.session if fact.session is not None else -1),
        0 if fact.detail else 1,
        _wording(fact),
    )


def facts_to_show(facts: Sequence[Fact]) -> list[Fact]:
    """The facts between two entries as they are worded: a fact a stronger one already says is
    dropped (a plain "knows" next to a family tie), the history of a feeling is one line, and the
    rest are sorted. Pure."""
    live = [f for f in facts if _wording(f)]
    strong = any(category_of(f) in (LASTING, ROLES) for f in live)
    keep = [f for f in live if not (f.kind == "knows" and not f.detail and strong)]
    feelings = sorted((f for f in keep if f.kind == "knows"), key=lambda f: f.session or 0)
    if len(feelings) > 1:  # keep the newest only; the change is told in `_standing_line`
        keep = [f for f in keep if f.kind != "knows"] + [feelings[-1]]
    return sorted(keep, key=_key)


def _sign(fact: Fact) -> int:
    return reputation_words.sign_of(fact.detail)


def _standing_line(facts: Sequence[Fact]) -> str | None:
    """ "was hostile, now friendly" when the feeling between two entries changed over time."""
    feelings = sorted(
        (f for f in facts if f.kind == "knows" and _sign(f)), key=lambda f: f.session or 0
    )
    if len(feelings) < 2 or _sign(feelings[0]) == _sign(feelings[-1]):
        return None
    before = reputation_words.word_of(feelings[0].detail) or ""
    after = reputation_words.word_of(feelings[-1].detail) or ""
    return f"was {before}, now {after}"


def _best_category(n: Neighbour) -> int:
    shown = facts_to_show(n.facts)
    return min((category_of(f) for f in shown), default=OTHER + 1)


def _newest(n: Neighbour) -> int:
    return max((f.session for f in n.facts if f.session is not None), default=-1)


def _order(audience: str) -> Callable[[Neighbour], tuple[int, int, float, int, str]]:
    def key(n: Neighbour) -> tuple[int, int, float, int, str]:
        rank = -n.rank if audience == DM else 0  # (the player order never sees the rank)
        return (rank, _best_category(n), -n.score, -_newest(n), n.name.casefold())

    return key


def _visible(n: Neighbour, audience: str) -> Neighbour | None:
    """The neighbour as this audience may see it, or None. The DM sees everything; a player sees
    only what some player character knows, and a neighbour is dropped when the players do not know
    it exists or know none of the ways it is joined."""
    if audience == DM:
        return n if n.rank >= MIN_RANK else None
    facts = tuple(f for f in n.facts if f.players_know)
    if not n.players_know or not facts:
        return None
    seen_further = tuple(v for v in (_visible(x, audience) for x in n.further) if v is not None)
    return Neighbour(n.id, n.name, n.kind, 0, n.score, facts, True, seen_further)


def needs_peek(neighbours: Sequence[Neighbour]) -> bool:
    """Whether this node has a fact the players hold that the person who clicked does not: the
    summary then sits behind "Contains spoilers … let me peek anyway". Looks at every neighbour the
    players know, whatever the caps leave out of the text."""
    return any(
        f.players_know and not f.viewer_knows for n in neighbours if n.players_know for f in n.facts
    )


def _clause(n: Neighbour, audience: str) -> tuple[str, int]:
    shown = facts_to_show(n.facts)
    words = [w for w in (_wording(f) for f in shown) if w]
    extra = _standing_line(n.facts)
    if extra:
        words = [w for w in words if w] + [extra]
    kept = words[:MAX_FACTS]
    left = len(words) - len(kept)
    text = (
        n.name if not kept else f"{n.name} ({'; '.join(kept)}{f'; +{left} more' if left else ''})"
    )
    return text, left


def summarize(
    focus: Focus, neighbours: Sequence[Neighbour], audience: str, *, peek: bool = False
) -> Summary:
    """The summary text for `focus` as `audience` (`DM` or `PLAYER`) may read it. `neighbours` are
    the entries joined to the node, each with its facts and, optionally, its own neighbours. Pure.
    """
    pool = [v for v in (_visible(n, audience) for n in neighbours) if v is not None]
    pool.sort(key=_order(audience))
    shown, rest = pool[:MAX_NEIGHBOURS], pool[MAX_NEIGHBOURS:]
    head = f"{focus.name} is a {focus.kind}."
    if audience == DM and focus.roles:
        head += f" Roles: {', '.join(focus.roles)}."
    if not shown:
        return Summary(head, (), len(rest), peek)
    named = [n.name for n in shown]
    parts: list[str] = []
    for n in shown:
        clause, _ = _clause(n, audience)
        also = [
            x for x in sorted(n.further, key=_order(audience))
            if x.id != focus.id and x.name not in named and x.name != n.name
        ]  # fmt: skip
        if also:
            clause += f", through them {also[0].name}"
            named.append(also[0].name)
        parts.append(clause)
    body = f"Joined to {_join(parts)}"
    if rest:
        body += f", and {len(rest)} more"
    return Summary(f"{head} {body}.", tuple(named), len(rest), peek)


def _join(parts: Sequence[str]) -> str:
    if len(parts) <= 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + ", and " + parts[-1]
