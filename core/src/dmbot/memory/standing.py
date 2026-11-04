"""Standing: how a viewer feels about a subject, worked out when it is needed (#1655;
docs/STORY_MEMORY.md, section 5). Pure: it reads one in-memory copy of the entries, a few kinds of
connection and the impressions and attitudes the DM set, and does no query per pair.

Two ladders. Who is judged: a character, then their party, the groups they belong to and the
people they are of. Who judges: an NPC, then the groups they belong to and the place they are in,
then the wider place. At most two rungs up either ladder, never a loop. Every impression or
attitude found between a rung of each counts with its evidence weight, halved for each rung it is
up the two ladders, so a deed-made impression with plenty of evidence outweighs prejudice as it
grows. The DM always wins: an impression the DM set on the exact pair, or the viewer's own
attitude the DM set toward a people the subject is of, decides the step outright, and the rungs
above show only as reasons. Nothing found is "No opinion yet", not Wary, so nothing invents a
feeling. It only reminds the DM; it never decides how anyone acts.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

HOSTILE, UNFRIENDLY, WARY, FRIENDLY, DEVOTED = (
    "Hostile",
    "Unfriendly",
    "Wary",
    "Friendly",
    "Devoted",
)
STEPS = (HOSTILE, UNFRIENDLY, WARY, FRIENDLY, DEVOTED)
# What the DM's step is stored as, and the cut-offs it is read back with.
SCORE_OF = {HOSTILE: -80, UNFRIENDLY: -40, WARY: 0, FRIENDLY: 40, DEVOTED: 80}
CUTS = ((-60, HOSTILE), (-20, UNFRIENDLY), (20, WARY), (60, FRIENDLY))
NO_OPINION = "No opinion yet"
DM_WEIGHT = 100  # a standing the DM set: the DM always wins at that rung
MAX_RUNGS = 2
HALF = 0.5

# A deed the DM recorded (#1732) moves how those it mattered to feel about the doer: how it landed,
# in the DM's words, and the score each stands for. A deed is evidence of weight `DEED_WEIGHT`
# (the DM's own word on a pair is `DM_WEIGHT` and always wins), halved once it is older than
# `DEED_FADES_AFTER` sessions: old news fades, but the DM can always set it.
DEED_EFFECTS = (
    ("Helped them", 20),
    ("Pleased them", 10),
    ("Annoyed them", -10),
    ("Hurt them", -20),
)
DEED_SCORE = dict(DEED_EFFECTS)
DEED_VERB = {
    "Helped them": "helped",
    "Pleased them": "pleased",
    "Annoyed them": "annoyed",
    "Hurt them": "hurt",
}
DEED_WEIGHT = 20
# A feeling the DM kept with a connection ("knows (hostile)", #3175): the story's word, pressed by
# the DM but read by an AI, so it counts for more than one deed and for far less than a standing
# the DM set (`DM_WEIGHT`), which always wins.
FEELING_WEIGHT = 30
DEED_FADES_AFTER = 10
STORY_SOURCE = "story"

DM_SOURCE = "dm"
MEMBER_OF, LOCATED_IN, OF_PEOPLE = "member_of", "located_in", "of_people"

REMINDER = "This only reminds you. You decide how they act."


def step_of(score: float) -> str:
    for limit, step in CUTS:
        if score < limit:
            return step
    return DEVOTED


@dataclass(frozen=True, slots=True)
class Node:
    """An entry as the rule needs it."""

    id: str
    name: str
    kind: str  # character, faction, place, people…
    party: bool = False
    player_character: bool = False


@dataclass(frozen=True, slots=True)
class Row:
    """One impression (holder = viewer, target = subject) or attitude (holder → a people)."""

    holder: str
    target: str
    score: int
    weight: int
    source: str
    attitude: bool = False
    deed: str = ""  # a deed's words (#1732): this row is evidence from it, not a feeling the DM set
    effect: str = ""  # ...and how it landed ("Helped them")
    session: int | None = None  # ...and when it was, for "session 4"


@dataclass(frozen=True, slots=True)
class Link:
    """A connection the ladders climb: member_of, located_in or of_people."""

    subject: str
    predicate: str
    object: str


@dataclass(frozen=True, slots=True)
class Standing:
    viewer: str
    subject: str
    score: float | None  # None: nothing found
    step: str | None
    reasons: tuple[str, ...]
    decided_by_dm: bool = False

    @property
    def shown(self) -> str:
        return NO_OPINION if self.step is None else self.step


class Index:
    """The copy standing is worked out from: built once, asked many times."""

    def __init__(
        self,
        nodes: Iterable[Node],
        links: Iterable[Link],
        rows: Iterable[Row],
        session_numbers: Mapping[int, int] | None = None,
    ) -> None:
        self.session_numbers: Mapping[int, int] = session_numbers or {}
        self.nodes: dict[str, Node] = {n.id: n for n in nodes}
        self.party: str | None = next((n.id for n in self.nodes.values() if n.party), None)
        self.up: dict[str, list[str]] = defaultdict(list)  # a judge climbs member_of + located_in
        self.of: dict[str, list[str]] = defaultdict(list)  # a subject climbs member_of + of_people
        for link in links:
            if link.subject not in self.nodes or link.object not in self.nodes:
                continue
            if link.predicate in (MEMBER_OF, LOCATED_IN):
                self.up[link.subject].append(link.object)
            if link.predicate in (MEMBER_OF, OF_PEOPLE):
                self.of[link.subject].append(link.object)
        self.impressions: dict[tuple[str, str], Row] = {}
        self.attitudes: dict[tuple[str, str], Row] = {}
        self.deeds: dict[tuple[str, str], list[Row]] = defaultdict(list)
        for row in rows:
            if row.deed:
                self.deeds[(row.holder, row.target)].append(row)
            else:
                (self.attitudes if row.attitude else self.impressions)[(row.holder, row.target)] = (
                    row
                )

    # ---- the ladders ----

    @staticmethod
    def _climb(start: str, step: Mapping[str, Sequence[str]], extra: str | None) -> list[list[str]]:
        """Rung 0 is the start; each rung up holds what is first reached there. No loops."""
        rungs: list[list[str]] = [[start]]
        seen = {start}
        for _ in range(MAX_RUNGS):
            nxt: list[str] = []
            for node in rungs[-1]:
                near = list(step.get(node, ()))
                if extra is not None and node == start and rungs[-1] == [start]:
                    near.insert(0, extra)
                for other in near:
                    if other not in seen:
                        seen.add(other)
                        nxt.append(other)
            if not nxt:
                break
            rungs.append(sorted(nxt))
        return rungs

    def judging(self, viewer: str) -> list[list[str]]:
        return self._climb(viewer, self.up, None)

    def judged(self, subject: str) -> list[list[str]]:
        node = self.nodes.get(subject)
        party = self.party if node is not None and node.player_character else None
        return self._climb(subject, self.of, party)

    # ---- the rule ----

    def standing(self, viewer: str, subject: str) -> Standing:
        if viewer not in self.nodes or subject not in self.nodes or viewer == subject:
            return Standing(viewer, subject, None, None, ())
        found: list[tuple[int, int, Row]] = []  # (rungs up the judging ladder, judged ladder, row)
        for i, judges in enumerate(self.judging(viewer)):
            for j, judged in enumerate(self.judged(subject)):
                for v in judges:
                    for s in judged:
                        if v == s:
                            continue
                        for row in (self.impressions.get((v, s)), self.attitudes.get((v, s))):
                            if row is not None:  # a group can hold both for the same pair
                                found.append((i, j, row))
                        for row in self.deeds.get((v, s), ()):
                            found.append((i, j, row))  # never decisive: the DM's word still wins
        if not found:
            return Standing(viewer, subject, None, None, ())
        found.sort(key=lambda f: (f[0] + f[1], f[0], f[2].holder, f[2].target))
        reasons = tuple(self._reason(row, viewer, subject) for _, _, row in found)
        # The DM always wins: the viewer's own, DM-set word on the exact pair, or toward a people
        # the subject is of, decides the step; the rungs above are only reasons.
        decisive = [
            f
            for f in found
            if f[0] == 0 and f[2].source == DM_SOURCE and (f[2].attitude or f[1] == 0)
        ]
        if decisive:
            nearest = min(f[1] for f in decisive)
            rows = [f[2] for f in decisive if f[1] == nearest]
            score = sum(r.score * r.weight for r in rows) / sum(r.weight for r in rows)
            return Standing(viewer, subject, score, step_of(score), reasons, decided_by_dm=True)
        total = sum(row.weight * HALF ** (i + j) for i, j, row in found)
        score = sum(row.score * row.weight * HALF ** (i + j) for i, j, row in found) / total
        return Standing(viewer, subject, score, step_of(score), reasons)

    def _reason(self, row: Row, viewer: str, subject: str) -> str:
        if row.deed:
            number = self.session_numbers.get(row.session) if row.session is not None else None
            when = f"session {number}, " if number is not None else ""
            return f"⚔️ {row.deed} ({when}{DEED_VERB.get(row.effect, row.effect)})"
        who = "you set it" if row.source == DM_SOURCE else "from the story"
        if row.holder == viewer and row.target == subject:
            return f"{who.capitalize()}."  # the step above already says what
        holder = self.nodes[row.holder].name
        target = self.nodes[row.target].name
        return f"{holder} is {step_of(row.score)} toward {target} ({who})."


__all__ = [
    "CUTS",
    "DEED_EFFECTS",
    "DEED_FADES_AFTER",
    "DEED_SCORE",
    "DEED_VERB",
    "DEED_WEIGHT",
    "DEVOTED",
    "DM_SOURCE",
    "DM_WEIGHT",
    "FEELING_WEIGHT",
    "FRIENDLY",
    "HOSTILE",
    "NO_OPINION",
    "REMINDER",
    "SCORE_OF",
    "STEPS",
    "UNFRIENDLY",
    "WARY",
    "Index",
    "Link",
    "Node",
    "Row",
    "Standing",
    "step_of",
]
