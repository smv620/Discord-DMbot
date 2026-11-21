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

IMPORT_DAYS = 14  # (ruling V: an import of a session played this long before is old history)
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
_TO_THE_PARTY = (
    r"(?=\s*(?:$|[.!,;:]|and\b|then\b|to\s+(?:the\s+(?:party|group|others|company|team)|everyone"
    r"|everybody|us|you|y'all|all|(?:his|her|their|my|our)\s+(?:friends|companions|party)"
    r"|the\s+rest)\b))"
)
_LEAVING = re.compile(
    # (a goodbye said to the party, never to the innkeeper: "says goodbye to everyone")
    r"\b(?:says?|said|saying|bids?|bade|waves?|waved)\s+(?:(?:a|their|his|her|my|your|our)\s+)?"
    r"(?:good-?byes?|farewells?)"
    + _TO_THE_PARTY
    + r"|\b(?:leaves?|leaving|left|quits?|quitting|quit)\s+(?:the\s+)?(?:party|group|company|team"
    r"|band|crew|adventure)\b"
    r"|\bparts?\s+ways\b|\bparted\s+ways\b|\bparting\s+ways\b"
    # (retiring from the adventure, never to a room for the night)
    r"|\b(?:retires?|retired|retiring)\s+from\s+(?:adventuring|the\s+(?:party|group|road|adventure)"
    r"|adventure)\b"
    r"|\b(?:stays?|stayed|staying|remains?|remained)\s+behind\b"
    r"|\b(?:goes|go|going|went|heads?|headed|heading|returns?|returned|returning|rides?|rode"
    r"|sails?|sailed|walks?|walked)\s+(?:back\s+)?home\b"
    r"|\b(?:won't|will\s+not|isn't|is\s+not)\s+(?:be\s+)?(?:coming|travell?ing)\s+with\b",
    re.IGNORECASE,
)
# A negation, a plan, a wish or a condition before the leaving word: "won't leave the party",
# "wants to go home", "will leave the party tomorrow", "if Okthandom won't be coming with you"
_NOT_SAID = re.compile(
    r"\b(?:not|never|no\s+longer\s+wants?|don't|doesn't|didn't|won't|can't|cannot|wouldn't"
    r"|shouldn't|if|unless|whether|might|may|maybe|perhaps|could|should|would|will|shall|\w+'ll"
    r"|going\s+to|gonna|wants?\s+to|wanted\s+to|wish(?:es)?\s+to|plans?\s+to|planning\s+to"
    r"|thinks?\s+about|thinking\s+about|considers?|considering|threatens?\s+to|about\s+to"
    r"|tempted\s+to|almost|nearly|pretends?\s+to)\b[^.!?;]*$",
    re.IGNORECASE,
)
# A trip, never a goodbye: what follows the leaving words in the same clause ("stays behind to
# guard the door", "heads home for the night", "leaves the party for a day", "... tomorrow")
_ONLY_A_TRIP = re.compile(
    r"\b(?:for\s+(?:the|a|an|one|two|three|a\s+few|some)\s+(?:night|day|days|hour|hours|while"
    r"|moment|bit|week|evening|morning)|for\s+now|tonight|today|tomorrow|soon|later|next"
    r"|until|till|briefly|for\s+a\s+(?:spell|rest)|to\s+(?:guard|watch|keep\s+watch|scout|rest"
    r"|sleep|shop|buy|fetch|get|look|check|wait|hold|cover|deal)|to\s+the\s+(?:market|shop|inn"
    r"|tavern|stables?|temple|room))\b",
    re.IGNORECASE,
)
# The leaving character's own player speaking of their character: "I say goodbye to everyone"
_FIRST_PERSON = re.compile(r"\b(?:i|i'm|i've|i'll|my|me)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Goodbye:
    """One player character heard leaving: as the reader named them, the cited lines' numbers
    that hold the quote, and who spoke each (ids, filled at the run's end; None for no one known).
    No words of the transcript. `own`: the lines that don't name the character but say the
    goodbye in the first person ("I say goodbye to everyone"): only that character's own player may
    have said them (`whose`)."""

    who: str
    lines: tuple[str, ...]
    speakers: tuple[int | None, ...] = ()
    own: tuple[str, ...] = ()


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


def import_refusal(imported_at: int | None, newest: bool, started_at: int) -> str:
    """Why an imported session asks about no goodbye, or "" when it may (Supervisor's ruling V on
    #4537): a live session always may (`imported_at` None). An imported one may only when it is
    the campaign's newest session and it started at most `IMPORT_DAYS` before it was imported, so
    a backlog imported oldest first, or an old session imported late, asks about no goodbye that
    may long be undone. Times in seconds. Pure."""
    if imported_at is None:
        return ""
    if not newest or imported_at - started_at > IMPORT_DAYS * 86_400:
        return "leave_old_import"
    return ""


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
    """Why this quote is refused against this line, or "" when it holds (pure; the rules above).
    The leaving words are found in the line itself where the quote is, so what follows the quote
    counts too (a goodbye "to the innkeeper", a stay behind "to guard the door")."""
    q = _plain(quote)
    words = len(q.split())
    if not QUOTE_WORDS[0] <= words <= QUOTE_WORDS[1]:
        return "leave_quote_length"
    plain = _plain(line)
    at = plain.find(q)
    if at < 0:
        return "leave_quote_not_in_line"
    m = _LEAVING.search(plain, at)
    if m is None or m.start() >= at + len(q):
        return "leave_not_said"
    # (what the line says up to the leaving words, and the rest of their clause: a question, a
    # denial, a plan or a trip is no goodbye)
    before = plain[: m.start()]
    clause = re.split(r"[.!;:]", plain[m.start() :], maxsplit=1)[0]
    if (
        "?" in clause
        or _NOT_SAID.search(before)
        or _ONLY_A_TRIP.search(clause[m.end() - m.start() :])
    ):
        return "leave_not_said"
    return ""


def names_who(who: str, line: str) -> bool:
    """Whether the line names the character, by any of the reader's name's words of three letters
    or more ("Okthandom" in "Okthandom Ironfist says goodbye")."""
    plain = _plain(line)
    return any(re.search(rf"\b{re.escape(w)}\b", plain) for w in _plain(who).split() if len(w) >= 3)


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
        own: list[str] = []
        for n in numbers:
            why = check_quote(quote, lines[n])
            if why:
                continue
            # (the goodbye is the named character's: the line names them, or says it as "I", which
            # only their own player may; "Mira says goodbye" cited for Okthandom is no one's)
            if names_who(who, lines[n]):
                holds.append(n)
            elif _FIRST_PERSON.search(_plain(quote)):
                holds.append(n)
                own.append(n)
            else:
                why = "leave_who_not_in_line"
        if not holds:
            out.drop(why or "leave_quote_not_in_line")
            continue
        key = name_key(who)
        if key in seen:
            continue
        seen.add(key)
        out.goodbyes.append(Goodbye(" ".join(who.split()), tuple(holds), own=tuple(own)))
    return out


def once_each(found: Iterable[Goodbye]) -> list[Goodbye]:
    """One goodbye per character across a session's batches: the first's name, with every
    batch's lines, their speakers and which must be the player's own, so the question goes with
    any of their forgets."""
    out: dict[str, Goodbye] = {}
    for g in found:
        key = name_key(g.who)
        first = out.get(key)
        if first is None:
            out[key] = g
            continue
        pairs = dict(zip(first.lines, first.speakers, strict=False))
        pairs.update((n, s) for n, s in zip(g.lines, g.speakers, strict=False) if n not in pairs)
        own = tuple(dict.fromkeys((*first.own, *g.own)))
        out[key] = Goodbye(first.who, tuple(pairs), tuple(pairs.values()), own)
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
        # (a line in the first person is the player's own: the DM saying "I" is an NPC or the DM)
        if (
            not g.speakers
            or len(g.speakers) != len(g.lines)
            or any(s is None or s not in allowed for s in g.speakers)
            or any(s != player for n, s in zip(g.lines, g.speakers, strict=True) if n in g.own)
        ):
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
    "import_refusal",
    "names_who",
    "once_each",
    "parse_leaves",
    "whose",
]
