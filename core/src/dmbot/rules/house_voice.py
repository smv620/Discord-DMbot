"""A house rule said at the table (#953): the DM says "house rule: potions are a bonus
action", and DMbot offers to write it down.

No AI. Only a clear phrase at the start of a DM's line counts: "house rule: …", "new house
rule …", "for this table, …" or "our rule is …". A player's line never starts one (the caller
only looks at the campaign's DMs), and a question like "what do the house rules say?" does
not begin with a phrase. Nothing is saved by this module or without the DM pressing Save:
this only finds the words, and keeps the limits (one proposal a minute, the same words once
a session). The buttons and the posting are `dmbot.dm_screen.house_voice`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dmbot.rules.house import RULE_MAX

GAP_S = 60.0  # at most one proposal in this many seconds; the rest are dropped
MIN_WORDS = 2

_DELIM = r"\s*[:;,\-–—]\s*"  # after a phrase that could start any sentence
_OPEN = r"[\s:;,\-–—]*"  # after a phrase that can only mean one thing
_PHRASES = (
    re.compile(rf"^house rule{_DELIM}(?P<rule>.+)$", re.I | re.S),
    re.compile(rf"^new house rule\b{_OPEN}(?P<rule>.+)$", re.I | re.S),
    re.compile(rf"^for this table{_DELIM}(?P<rule>.+)$", re.I | re.S),
    re.compile(rf"^our (?:house )?rule is\b{_OPEN}(?P<rule>.+)$", re.I | re.S),
)
_EDGES = " \t\r\n\"'“”‘’,;:-–—"


@dataclass(frozen=True, slots=True)
class Said:
    """A house rule found in a line."""

    rule: str  # the words after the phrase
    cut: bool  # there were more than the store allows: the rest is left out

    @property
    def key(self) -> str:
        """What "the same words" means: letters and digits, case ignored."""
        return " ".join(re.sub(r"[^\w]+", " ", self.rule.casefold()).split())


def find(line: str) -> Said | None:
    """The house rule a DM's line starts, or None. The line is the cleaned transcript line,
    as the DM said it."""
    text = " ".join(line.split())
    for pattern in _PHRASES:
        match = pattern.match(text)
        if match is None:
            continue
        rule = match["rule"].strip(_EDGES)
        if len(rule.split()) < MIN_WORDS:
            return None
        rule = rule[:1].upper() + rule[1:]
        if len(rule) > RULE_MAX:
            cut = rule[:RULE_MAX].rsplit(" ", 1)[0] if " " in rule[:RULE_MAX] else rule[:RULE_MAX]
            return Said(cut.rstrip(_EDGES), True)
        return Said(rule, False)
    return None


@dataclass(frozen=True, slots=True)
class Clash:
    """An existing house rule the new one may be about the same thing as."""

    number: int
    version: int
    words: str


@dataclass(frozen=True, slots=True)
class Proposal:
    said: Said
    clashes: tuple[Clash, ...] = ()
    scenario: str = ""
    session_id: str | None = None
    unchecked: bool = False  # the campaign's house rules couldn't be read, so no clash check


@dataclass(slots=True)
class HouseVoice:
    """A session's proposals: which words were offered, and when the last one was."""

    seen: set[str] = field(default_factory=set)
    last_at: float | None = None
    proposals: dict[str, Proposal] = field(default_factory=dict)

    def pick(self, said: Said | None, now: float) -> Said | None:
        """The words to offer, if any: not within a minute of the last proposal (it is
        dropped, never kept for later), and not the same words as before."""
        if said is None:
            return None
        if self.last_at is not None and now - self.last_at < GAP_S:
            return None
        if said.key in self.seen:
            return None
        return said

    def remember(self, said: Said, now: float, proposal_id: str, proposal: Proposal) -> None:
        self.seen.add(said.key)
        self.last_at = now
        self.proposals[proposal_id] = proposal

    def forget(self, proposal_id: str, last_at: float | None, now: float) -> None:
        """The proposal could not be shown: the words and the minute are given back (the
        minute only if no later proposal has taken it since)."""
        proposal = self.proposals.pop(proposal_id, None)
        if proposal is not None:
            self.seen.discard(proposal.said.key)
            if self.last_at == now:
                self.last_at = last_at
