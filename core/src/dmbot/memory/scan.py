"""The after-session scan (docs/PLAN.md, "Campaign memory", where names come from).

After a session, DMbot reads what it heard and suggests names it doesn't know yet, for
the DM to confirm: "📝 Hrothgar: new name, heard 4 times. Someone or something in your
game?" It only *suggests*; nothing counts until the DM says yes.

Pure, no database. Speech-to-text writes names it doesn't know with capitals, so a
candidate is a capitalized word or run of words that:
- appears capitalized in the middle of a sentence at least once (so "Then" at the start
  of a sentence isn't a name),
- never appears in lower case in the session ("Roll" next to "roll" is a word),
- isn't a common word, a game term, or anything DMbot already has an answer for (known
  names, names the DM said aren't names, "keep as heard" words, people at the table),
- was heard at least `MIN_TIMES` times.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from dmbot.memory.models import NAME_MAX, name_key

MIN_TIMES = 2
MAX_SUGGESTIONS = 10
MAX_WORDS = 3  # "Oskar Vane", "Ten Towns", "Caer Dineval"

_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")
_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*")


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


# Capitalized at the table but not story names. Lower case; compared by name_key.
COMMON = _words(
    """
    i i'm i'll i've i'd im ok okay yes yeah no nope hey hi hello oh ah um uh hmm well so
    and but or the a an this that these those there here then now what who why how when
    where which mr mrs ms miss sir madam lord lady king queen prince princess captain
    monday tuesday wednesday thursday friday saturday sunday january february march april
    may june july august september october november december god gods christmas
    dm gm npc npcs pc pcs dnd d&d discord google youtube english
    """
)
# Rules words a table says with capitals; the rules advisor (Phase 3) knows them.
GAME_TERMS = _words(
    """
    strength dexterity constitution intelligence wisdom charisma acrobatics arcana
    athletics deception history insight intimidation investigation medicine nature
    perception performance persuasion religion sleight stealth survival initiative
    advantage disadvantage inspiration concentration blinded charmed deafened exhaustion
    frightened grappled incapacitated invisible paralyzed petrified poisoned prone
    restrained stunned unconscious ac hp dc xp
    """
)


@dataclass(frozen=True, slots=True)
class Suggestion:
    name: str  # as heard most often
    times: int


def _sentences(lines: Iterable[str]) -> list[list[str]]:
    out = []
    for line in lines:
        for sentence in _SENTENCE.split(line):
            words = _WORD.findall(sentence)
            if words:
                out.append(words)
    return out


def _runs(words: list[str], skip: set[str] | frozenset[str]) -> list[tuple[int, int]]:
    """Each run of capitalized words as (start, end). A common word ("The") ends a run
    and isn't part of it, and runs are at most MAX_WORDS long."""
    runs = []
    i = 0
    while i < len(words):
        if words[i][0].isupper() and name_key(words[i]) not in skip:
            j = i
            while (
                j < len(words)
                and words[j][0].isupper()
                and name_key(words[j]) not in skip
                and j - i < MAX_WORDS
            ):
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def find_new_names(
    lines: Iterable[str], skip_keys: Iterable[str] = (), *, min_times: int = MIN_TIMES
) -> list[Suggestion]:
    """Names in these lines (as heard) worth asking the DM about, most heard first."""
    sentences = _sentences(lines)
    skip = {name_key(k) for k in skip_keys} | COMMON | GAME_TERMS
    lower_words = {w.casefold() for words in sentences for w in words if w[0].islower()}
    counts: Counter[str] = Counter()
    spellings: dict[str, Counter[str]] = {}
    mid_sentence: set[str] = set()
    for words in sentences:
        for start, end in _runs(words, skip):
            run = words[start:end]
            text = " ".join(run)
            key = name_key(text)
            if (
                not key
                or len(text) > NAME_MAX
                or key in skip
                or any(name_key(w) in skip or w.casefold() in lower_words for w in run)
            ):
                continue
            counts[key] += 1
            spellings.setdefault(key, Counter())[text] += 1
            if start > 0:
                mid_sentence.add(key)
    found = [
        Suggestion(spellings[key].most_common(1)[0][0], times)
        for key, times in counts.items()
        if times >= min_times and key in mid_sentence
    ]
    found.sort(key=lambda s: (-s.times, s.name))
    return found[:MAX_SUGGESTIONS]
