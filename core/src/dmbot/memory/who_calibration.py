"""Scoring the "who is he?" rule against the calibration set (#2790, slice 2). Pure: the caller
supplies the predictions (from the AI, or from a stand-in in tests), so this runs with no network.

The set is `tests/fixtures/who_is_it_calibration.json`: invented passages only (never the
owner's campaign), each with the names DMbot knows, the phrase to resolve and the right answer.
The report is what the owner asked for: how often an accepted answer is right at 80% and in the
signal-to-noise band, how often the DM would be asked, and how often a person is kept unnamed.
Counts only, so dev1 can post a real run on GitHub without names or text.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from dmbot.memory.who_is_it import ACCEPT_AT, NOT_LISTED, Candidate, Verdict, decide

ASK = "ask"  # the answer of an ambiguous passage: the text does not settle it


@dataclass(frozen=True, slots=True)
class Passage:
    id: str
    text: str  # the passage, up to and including the sentence with the phrase
    phrase: str  # the pronoun or description to resolve
    known: tuple[str, ...]  # names DMbot already knows for the campaign
    answer: str  # the right name, NOT_LISTED, or ASK when the text does not settle it
    kind: str  # pronoun, description, unnamed or ambiguous


@dataclass(slots=True)
class Tally:
    total: int = 0
    right: int = 0

    def add(self, right: bool) -> None:
        self.total += 1
        self.right += int(right)


@dataclass(slots=True)
class Report:
    total: int = 0
    accepted_high: Tally = field(default_factory=Tally)  # accepted at 80% or more
    accepted_band: Tally = field(default_factory=Tally)  # accepted by signal-to-noise (40-80%)
    asked: Tally = field(default_factory=Tally)  # "right" = the top pick was the answer
    unnamed: Tally = field(default_factory=Tally)  # "right" = the answer really was not listed
    # Passages the text leaves open: "right" = the DM would be asked, wrong = a name was accepted
    # (an overconfident accept) or the person was made unnamed. An accept here also counts as wrong
    # in its accepted line above.
    ambiguous: Tally = field(default_factory=Tally)

    def counts(self) -> dict[str, tuple[int, int]]:
        """(total, right) for each outcome: the only thing a GitHub post needs."""
        return {
            "accepted_high": (self.accepted_high.total, self.accepted_high.right),
            "accepted_band": (self.accepted_band.total, self.accepted_band.right),
            "asked": (self.asked.total, self.asked.right),
            "unnamed": (self.unnamed.total, self.unnamed.right),
            "ambiguous": (self.ambiguous.total, self.ambiguous.right),
        }


def load(path: Path) -> list[Passage]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [Passage(**{**row, "known": tuple(row["known"])}) for row in rows]


def score(passages: list[Passage], predict: Callable[[Passage], list[Candidate]]) -> Report:
    report = Report()
    for passage in passages:
        report.total += 1
        candidates = predict(passage)
        decision = decide(candidates)
        left_open = passage.answer == ASK
        if left_open:
            report.ambiguous.add(decision.verdict is Verdict.ASK)
        match decision.verdict:
            case Verdict.ACCEPT:
                band = report.accepted_high if decision.top >= ACCEPT_AT else report.accepted_band
                band.add(decision.name == passage.answer)
            case Verdict.ASK:
                if not left_open:
                    report.asked.add(decision.name == passage.answer)
            case Verdict.UNNAMED:
                if not left_open:
                    report.unnamed.add(passage.answer == NOT_LISTED)
    return report
