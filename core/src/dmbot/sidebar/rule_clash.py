"""Two house rules that disagree, met while answering (#2284, step 3). Pure: no Discord, no AI.

A pair the DM already settled (`dmbot.rules.wins`) is followed with no question: the losing rule
is left out of what the answer is built from, so the answer comes from the winning rule only. For
any other pair the answering call reports a clash itself, in a fixed `CONFLICT` line; that line is
checked here, in code, before anyone is asked: it must name exactly two different rules the call
was shown, by the labels the prompt gave them. Anything else is dropped, and logged by numbers
only. DMbot never picks a rule: a clash is put to the DM, or (a table question) to the DM only.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

import discord

from dmbot.rules.followed import FollowedRule
from dmbot.rules.house import HouseRule
from dmbot.rules.wins import RuleRef, Win

log = logging.getLogger(__name__)

# What the prompt calls a rule: "House rule 3" (the campaign's, or merged in from a set) or
# "Shared rule 2" (a set the campaign follows). The reply names them the same way.
_NAMED = re.compile(r"\b(house|shared)\s+rule\s+(\d{1,9})\b", re.IGNORECASE)
WORDS_MAX = 200


def ref_of(rule: HouseRule) -> RuleRef:
    """The key `dmbot.rules.wins` stores a rule under: the set's name for a followed rule, else
    "" (the campaign's own, a rule merged in from a set included, as the save-time check does)."""
    return RuleRef(getattr(rule, "set_name", "") or "", rule.number)


def rank_of(rule: HouseRule) -> tuple[bool, bool]:
    """Where a rule stands: the campaign's own first, then rules merged in from a set, then a
    followed set's (precedence is unchanged, and the prompt says so)."""
    return isinstance(rule, FollowedRule), bool(getattr(rule, "from_set", None))


def label_of(rule: HouseRule) -> str:
    return f"{'Shared' if isinstance(rule, FollowedRule) else 'House'} rule {rule.number}"


def settled(rules: Sequence[HouseRule], wins: Sequence[Win]) -> list[HouseRule]:
    """The rules without the loser of any pair the DM settled, when both are in `rules`."""
    present = {ref_of(r) for r in rules}
    pairs = [w for w in wins if w.loser in present and w.winner in present]
    # A rule that itself lost to another shown rule does not take a third one down with it: in a
    # chain (A over B, B over C) only B goes, since the DM never ruled on A against C, and in a
    # cycle nobody goes (the answer may then ask again, and the DM's new choice replaces the old).
    losing = {w.loser for w in pairs}
    gone = {w.loser for w in pairs if w.winner not in losing}
    return [r for r in rules if ref_of(r) not in gone]


@dataclass(frozen=True, slots=True)
class Side:
    """One rule of a clash: how to name it, its words, and the key its win is stored under."""

    label: str
    words: str
    ref: RuleRef


def _side(rule: HouseRule) -> Side:
    words = rule.rule + (f" (replaces: {rule.supersedes})" if rule.supersedes else "")
    words = " ".join(words.split())
    if len(words) > WORDS_MAX:
        words = words[: WORDS_MAX - 1].rstrip() + "…"
    # A followed set's words come from another campaign's DM: no markdown, link or ping gets
    # through into the DM's message.
    words = discord.utils.escape_markdown(discord.utils.escape_mentions(words))
    return Side(label_of(rule), words, ref_of(rule))


def named(field: str, shown: Sequence[HouseRule]) -> tuple[Side, Side] | None:
    """The clash a reply's CONFLICT line names, or None. It counts only when it names two
    different rules, each of which is one of `shown` and found by its label alone (a label two of
    the rules share is not a name). A pair already settled is not a clash either."""
    found: list[HouseRule] = []
    for kind, number in _NAMED.findall(field or ""):
        followed = kind.lower() == "shared"
        matches = [
            r for r in shown if r.number == int(number) and isinstance(r, FollowedRule) == followed
        ]
        if len(matches) != 1:
            log.info("A sidebar clash named rule %s, which was not one rule it was given", number)
            return None
        if matches[0] not in found:
            found.append(matches[0])
    if len(found) != 2:
        if (field or "").strip().lower().strip(" .") not in ("", "none", "n/a", "-"):
            log.info("A sidebar clash line did not name two different rules (%d found)", len(found))
        return None
    if rank_of(found[0]) != rank_of(found[1]):
        return None  # the campaign's own rule outranks a merged or followed one: not a question
    return _side(found[0]), _side(found[1])


CLASH_RULES = """

14. If two of the house rules you were given say different things about this question, do not \
answer from either. Put the labels of those two rules (like "House rule 3, Shared rule 2") on \
one more line, CONFLICT:, and leave ANSWER as the single word "clash". Otherwise write \
CONFLICT: none."""

ASK = (
    "⚖️ **Which one applies?** Your house rules disagree about this. "
    "Your choice is remembered for these two rules."
)
ONLY_DM = "Only this campaign's DM can choose."
NOTE = (
    "A player's question needed {a} and {b}, which disagree, so I told the table it's one for "
    "you. Ask it yourself to choose which one applies."
)


def said_clash(answer: str) -> bool:
    """The model's own marker for a clash, as the prompt asks for it."""
    return answer.strip().strip(".").lower() == "clash"


def question_text(a: Side, b: Side) -> str:
    """The DM's question: both rules quoted with their numbers. It never says which is right."""
    return f"{ASK}\n**{a.label}:** {a.words}\n**{b.label}:** {b.words}"
