"""A player character saying goodbye to the party, heard in a session (#4537, party knowledge slice
6b; `docs/design/party_knowledge.md` §4 "Readers only suggest" and §11; Supervisor's rulings R, T
and U on slice 6, 2026-11-21). Pure: no database, no AI call, nothing written.

The session reader is asked for them in the same call as its claims (`LEAVE_ADDENDUM`, so the
switch costs no extra call, as `language_reading` does). What holds becomes one after-session
question for the DM, "Has Okthandom left the party?", on the join question's rails
(`join_questions.ask_heard`). Nothing closes a stretch here or anywhere a reader reaches: only the
DM's answer, or the card's own button, ends one (§11). Behind DMBOT_PARTY_LEAVES (off) until the
sample's counts show no false goodbye questions (ruling U).

Rules held in code, not trust:
- **A quote, checked.** The reader copies a few words from a cited line, and they must be in it
  (`leave_quote_not_in_line`). Only the lines that hold the quote are kept as evidence.
- **Said as a goodbye, never a trip, a plan or a question.** The quote must say leaving the party
  ("says goodbye", "leaves the party", "stays behind", "heads home for good"); a question ("are
  you leaving?"), a negation ("won't leave") or a plan or wish ("wants to go home", "might
  leave") is refused (`leave_not_said`).
- **Only the DM, or the leaving character's own player** (ruling T): another player saying
  "he's gone" is not evidence. Checked once the speakers and the party are known (`whose`).
- **One character, named, and in the party now.** Never a pronoun, the party or a crowd; a name
  two characters share is no one's; one who isn't a current member is not asked about.
- **One per character per session** (`once_each`, and the store's own check).
- **The transcript is data.** Words that read like an instruction are refused.

Counts only leave this module; the words of a transcript are never logged or kept: the question
holds the character's id, the session and who spoke (ids), nothing else.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from dmbot.memory.claims import LINES_MAX, reads_like_instruction
from dmbot.memory.models import NAME_MAX, name_key
from dmbot.memory.readings import PRONOUNS
from dmbot.memory.reveal_reading import GROUP_WORDS

MAX_PER_ANSWER = 5  # one batch of ten lines hears this many goodbyes at most
QUOTE_WORDS = (2, 25)  # a quote is a few words, never a whole speech
PROMPT_SUFFIX = "+leave-1"  # added to the claims' prompt version when the addendum is sent

LEAVE_ADDENDUM = """

Also list the player characters who LEAVE THE PARTY in this stretch: the DM says a character says
goodbye, leaves the group, retires, stays behind for good or goes home for good, or a character's
own player says their character says goodbye and leaves. Leave out a short trip (scouting ahead,
going to the market, a night away), a plan or a wish ("wants to go home"), a question ("are you
leaving?"), a threat, a death, and anyone who is not a player character.
Give them as "leaves", beside "claims":
{"claims": [...], "leaves": [{"who": "Okthandom", "quote": "Okthandom says goodbye", "lines": [7]}]}
"who" is the character's name, never "he", "she", "I", "the party", "DM" or "Player". "quote" is
a few words copied exactly from one of the cited lines that say the leaving. Give "leaves": [] if
there are none."""

_LIST_START = re.compile(r'\s*\{\s*"claims"\s*:')
# Saying leaving the party, in the quote's own words
_LEAVING = re.compile(
    r"\b(?:says?|said|saying|bids?|bade|waves?|waved)\s+(?:(?:a|their|his|her|my|your|our)\s+)?"
    r"(?:good-?byes?|farewells?)\b"
    r"|\bgood-?byes?\b|\bfarewell\b"
    r"|\b(?:leaves?|leaving|left|quits?|quitting|quit)\s+(?:the\s+)?(?:party|group|company|team"
    r"|band|crew|adventure)\b"
    r"|\bparts?\s+ways\b|\bparted\s+ways\b|\bparting\s+ways\b"
    r"|\b(?:retires?|retired|retiring)\b"
    r"|\b(?:stays?|stayed|staying|remains?|remained)\s+behind\b"
    r"|\b(?:goes|go|going|went|heads?|headed|heading|returns?|returned|returning|rides?|rode"
    r"|sails?|sailed|walks?|walked)\s+(?:back\s+)?home\b"
    r"|\b(?:won't|will\s+not|isn't|is\s+not)\s+(?:be\s+)?(?:coming|travell?ing)\s+with\b",
    re.IGNORECASE,
)
# A negation, a plan or a wish before the leaving word: "won't leave the party", "wants to go home"
_NOT_SAID = re.compile(
    r"\b(?:not|never|no\s+longer\s+wants?|don't|don’t|doesn't|doesn’t|didn't|didn’t|won't|won’t"
    r"|can't|can’t|cannot|wouldn't|wouldn’t|shouldn't|shouldn’t|if|unless|might|may|maybe"
    r"|perhaps|could|should|would|wants?\s+to|wanted\s+to|wish(?:es)?\s+to|plans?\s+to"
    r"|thinks?\s+about|thinking\s+about|considers?|considering|threatens?\s+to|about\s+to"
    r"|tempted\s+to|almost|nearly)\b[^.!?;]*$",
    re.IGNORECASE,
)
# The phrase "won't be coming with" is itself a goodbye: its negation is the leaving, not a denial
_NEGATIVE_GOODBYE = re.compile(r"(?:won't|will\s+not|isn't|is\s+not)\s+(?:be\s+)?\S+\s+with$", re.I)


@dataclass(frozen=True, slots=True)
class Goodbye:
    """One player character heard leaving: as the reader named them, the cited lines' numbers
    that hold the quote, and who spoke each (ids, filled at the run's end; None for no one known).
    No words of the transcript."""

    who: str
    lines: tuple[str, ...]
    speakers: tuple[int | None, ...] = ()


@dataclass(slots=True)
class Parsed:
    goodbyes: list[Goodbye] = field(default_factory=list)
    refused: int = 0
    dropped: dict[str, int] = field(default_factory=dict)

    def drop(self, why: str) -> None:
        self.dropped[why] = self.dropped.get(why, 0) + 1
        self.refused += 1


def _plain(text: str) -> str:
    return " ".join(text.replace("’", "'").casefold().split())


def check_who(who: str) -> str:
    """Why this name is refused, or "": one character, never a pronoun, a crowd or a role."""
    name = " ".join(who.split())
    if (
        not name
        or len(name) > NAME_MAX
        or name.casefold() in PRONOUNS
        or name.casefold() in GROUP_WORDS
        or name.casefold() in ("i", "me", "myself", "dm", "player", "the dm", "everyone")
    ):
        return "leave_who"
    return ""


def check_quote(quote: str, line: str) -> str:
    """Why this quote is refused against this line, or "" when it holds (pure; the rules above)."""
    q = _plain(quote)
    words = len(q.split())
    if not QUOTE_WORDS[0] <= words <= QUOTE_WORDS[1]:
        return "leave_quote_length"
    plain = _plain(line)
    at = plain.find(q)
    if at < 0:
        return "leave_quote_not_in_line"
    m = _LEAVING.search(q)
    if m is None:
        return "leave_not_said"
    # (what the line says up to the leaving word, and the clause it ends: a question or a denial
    # is no goodbye)
    before = plain[: at + m.start()]
    clause = re.split(r"[.!;:]", plain[at + m.start() :], maxsplit=1)[0]
    if "?" in clause:
        return "leave_not_said"
    if _NOT_SAID.search(before) and not _NEGATIVE_GOODBYE.search(plain[: at + m.end()].rstrip()):
        return "leave_not_said"
    return ""


def parse_leaves(answer: str, lines: Mapping[str, str]) -> Parsed:
    """The goodbyes in a claims answer that carries the addendum, each checked against the cited
    lines (`lines`: the batch's numbers to their words). Malformed parts are refused and counted;
    one character's goodbye twice in an answer is one."""
    out = Parsed()
    text = answer.strip()
    start = _LIST_START.search(text)
    try:
        data: Any = json.loads(text[start.start() :] if start else text)
    except (json.JSONDecodeError, ValueError):
        return out
    items = data.get("leaves") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return out
    seen: set[str] = set()
    for item in items[: MAX_PER_ANSWER * 2]:
        if len(out.goodbyes) >= MAX_PER_ANSWER:
            out.drop("leave_too_many")
            continue
        if not isinstance(item, dict):
            out.drop("leave_malformed")
            continue
        who, quote = str(item.get("who") or ""), str(item.get("quote") or "")
        cited = item.get("lines")
        numbers = (
            tuple(dict.fromkeys(str(n) for n in cited[:LINES_MAX] if str(n) in lines))
            if isinstance(cited, list)
            else ()
        )
        if not numbers:
            out.drop("leave_lines")
            continue
        if reads_like_instruction(who) or reads_like_instruction(quote):
            out.drop("leave_instruction")
            continue
        why = check_who(who)
        if why:
            out.drop(why)
            continue
        holds: list[str] = []
        for n in numbers:
            why = check_quote(quote, lines[n])
            if not why:
                holds.append(n)
        if not holds:
            out.drop(why or "leave_quote_not_in_line")
            continue
        key = name_key(who)
        if key in seen:
            continue
        seen.add(key)
        out.goodbyes.append(Goodbye(" ".join(who.split()), tuple(holds)))
    return out


def once_each(found: Iterable[Goodbye]) -> list[Goodbye]:
    """One goodbye per character across a session's batches: the first's name, with every
    batch's lines and their speakers, so the question goes with any of their forgets."""
    out: dict[str, Goodbye] = {}
    for g in found:
        key = name_key(g.who)
        first = out.get(key)
        if first is None:
            out[key] = g
            continue
        pairs = dict(zip(first.lines, first.speakers, strict=False))
        pairs.update((n, s) for n, s in zip(g.lines, g.speakers, strict=False) if n not in pairs)
        out[key] = Goodbye(first.who, tuple(pairs), tuple(pairs.values()))
    return list(out.values())


def whose(
    goodbyes: Iterable[Goodbye],
    names: Mapping[str, Collection[str]],
    members: Collection[str],
    played_by: Mapping[str, int | None],
    dm_ids: Collection[int],
) -> tuple[list[tuple[str, tuple[int, ...]]], Counter[str]]:
    """Each goodbye's character and who spoke it, for the questions: `names` are name keys to the
    confirmed characters that carry them, `members` the party now, `played_by` each member's
    player. A name two characters share, a character not in the party now, or a goodbye with a
    line not spoken by a DM or that character's own player (ruling T) is dropped and counted. One
    question per character. Counts only."""
    asks: dict[str, tuple[int, ...]] = {}
    dropped: Counter[str] = Counter()
    for g in goodbyes:
        ids = [e for e in names.get(name_key(g.who), ()) if e in members]
        if len(set(names.get(name_key(g.who), ()))) > 1:
            dropped["leave_name_shared"] += 1
            continue
        if not ids:
            dropped["leave_not_a_member"] += 1
            continue
        entity_id = ids[0]
        player = played_by.get(entity_id)
        allowed = {*dm_ids, *([player] if player is not None else [])}
        if not g.speakers or any(s is None or s not in allowed for s in g.speakers):
            dropped["leave_not_dm_or_player"] += 1
            continue
        heard = {s for s in g.speakers if s is not None}
        asks[entity_id] = tuple(sorted({*asks.get(entity_id, ()), *heard}))
    return list(asks.items()), dropped


__all__ = [
    "LEAVE_ADDENDUM",
    "PROMPT_SUFFIX",
    "Goodbye",
    "Parsed",
    "check_quote",
    "check_who",
    "once_each",
    "parse_leaves",
    "whose",
]
