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

import difflib
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace

from dmbot.memory.lookup import CampaignLookup
from dmbot.memory.models import CONFIRMED, NAME_MAX, name_key
from dmbot.memory.sounds import sound_codes

MIN_TIMES = 2
MAX_SUGGESTIONS = 10
MAX_WORDS = 3  # "Oskar Vane", "Ten Towns", "Caer Dineval"
# Near a known name (#394): spelled at least this alike, and sounding alike. One word
# needs more, like the Transcript Cleaner ("Mary" is not the character Mara).
NEAR = 0.8
NEAR_ONE_WORD = 0.9
TIE = 0.05  # two known names this close in likeness: neither is offered

_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")
_WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|['’-](?=[^\W\d_]))*")  # letters, any script
_POSSESSIVE = re.compile(r"['’]s$")


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
    barbarian bard cleric druid fighter monk paladin ranger rogue sorcerer warlock wizard
    artificer dwarf elf elves halfling human gnome dragonborn tiefling orc goliath aasimar
    """
)


@dataclass(frozen=True, slots=True)
class Match:
    """The known name a suggestion sounds like (#394), for "Another name for X"."""

    entity_id: str
    name: str  # the known entry's own name
    likeness: float


@dataclass(frozen=True, slots=True)
class Suggestion:
    name: str  # as heard most often
    times: int
    also: tuple[str, ...] = ()  # near-duplicates folded in ("Vane" with "Oskar Vane")
    match: Match | None = None  # a known name it sounds like


def _sentences(lines: Iterable[str]) -> list[list[str]]:
    out = []
    for line in lines:
        for sentence in _SENTENCE.split(line):
            words = [_POSSESSIVE.sub("", w) for w in _WORD.findall(sentence)]
            if words:
                out.append(words)
    return out


def _is_name_word(word: str, skip: frozenset[str], lower_words: set[str]) -> bool:
    return word[:1].isupper() and name_key(word) not in skip and word.casefold() not in lower_words


def _candidates(
    words: list[str], skip: frozenset[str], lower_words: set[str]
) -> list[tuple[str, bool]]:
    """Each run of name-like words as (text, said mid-sentence). Runs longer than
    MAX_WORDS are dropped, not chopped (no "Caer Dineval Ice"). A run that starts the
    sentence also offers its tail, so "Ask Hrothgar" still finds Hrothgar."""
    out = []
    i = 0
    while i < len(words):
        if not _is_name_word(words[i], skip, lower_words):
            i += 1
            continue
        j = i
        while j < len(words) and _is_name_word(words[j], skip, lower_words):
            j += 1
        if j - i <= MAX_WORDS:
            out.append((" ".join(words[i:j]), i > 0))
            if i == 0 and j - i > 1:
                out.append((" ".join(words[1:j]), True))
        i = j
    return out


def find_new_names(
    lines: Iterable[str],
    skip_keys: Iterable[str] = (),
    *,
    min_times: int = MIN_TIMES,
    limit: int | None = MAX_SUGGESTIONS,
) -> list[Suggestion]:
    """Names in these lines (cleaned, so names fixed live are known) worth asking the DM
    about, most heard first. `limit=None`: all of them (group them, then cap)."""
    sentences = _sentences(lines)
    skip = frozenset({name_key(k) for k in skip_keys} | COMMON | GAME_TERMS)
    lower_words = {w.casefold() for words in sentences for w in words if w[:1].islower()}
    counts: Counter[str] = Counter()
    spellings: dict[str, Counter[str]] = {}
    mid_sentence: set[str] = set()
    for words in sentences:
        for text, mid in _candidates(words, skip, lower_words):
            key = name_key(text)
            if not key or len(text) > NAME_MAX or key in skip:
                continue
            counts[key] += 1
            spellings.setdefault(key, Counter())[text] += 1
            if mid:
                mid_sentence.add(key)
    found = [
        Suggestion(spellings[key].most_common(1)[0][0], times)
        for key, times in counts.items()
        if times >= min_times and key in mid_sentence
    ]
    found.sort(key=lambda s: (-s.times, s.name))
    return found if limit is None else found[:limit]


def _alike(a: str, b: str, need: float = 0.0) -> float:
    """How alike two spellings are, 0 to 1, ignoring case, spaces and punctuation;
    0 at once when they can't reach `need` (a quick check first, for big campaigns)."""
    matcher = difflib.SequenceMatcher(
        None, name_key(a).replace(" ", ""), name_key(b).replace(" ", "")
    )
    if matcher.real_quick_ratio() < need or matcher.quick_ratio() < need:
        return 0.0
    return matcher.ratio()


Candidate = tuple[str, str, str]  # (entity ID, a name of it as written, its own name)


def near_match_in(name: str, candidates: Iterable[Candidate]) -> Match | None:
    """The known name this one sounds like and is spelled much like, if there's one
    clear best. `candidates`: confirmed, non-secret names that share a sound code with
    it (the review is in the DM screen, which players may peek at). Never merged on its
    own: the DM decides."""
    need = NEAR_ONE_WORD if len(name.split()) == 1 else NEAR
    best: dict[str, tuple[float, str]] = {}
    for entity_id, text, own in candidates:
        alike = _alike(name, text, need - TIE)
        if alike > best.get(entity_id, (0.0, ""))[0]:
            best[entity_id] = (alike, own)
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
    if not ranked or ranked[0][1][0] < need:
        return None
    if len(ranked) > 1 and ranked[0][1][0] - ranked[1][1][0] < TIE:
        return None  # two known names fit about as well: offer it as new
    entity_id, (alike, own) = ranked[0]
    return Match(entity_id, own, alike)


def sound_keys(name: str) -> tuple[str, ...]:
    """The sound codes a suggested name is looked up by."""
    return sound_codes(name.replace(" ", ""))


def near_match(lookup: CampaignLookup, name: str) -> Match | None:
    """`near_match_in` against the campaign's names in memory."""
    seen: dict[str, Candidate] = {}
    for code in sound_keys(name):
        for entry in lookup.by_sound.get(code, ()):
            entity = lookup.entities.get(entry.entity_id)
            if entry.secret or not entry.confirmed or entity is None:
                continue
            if entity.status == CONFIRMED:
                seen.setdefault(entry.alias_id, (entry.entity_id, entry.text, entity.name))
    return near_match_in(name, seen.values())


def _same_thing(a: str, b: str) -> bool:
    """Is the shorter one part of the longer ("Vane" in "Oskar Vane"), or do they sound
    and look alike (one-word names: as alike as the Cleaner needs, "Kael" isn't
    "Kaela")?"""
    short, long_ = sorted((name_key(a).split(), name_key(b).split()), key=len)
    if len(short) < len(long_) and (long_[: len(short)] == short or long_[-len(short) :] == short):
        return True
    need = NEAR_ONE_WORD if len(short) == 1 and len(long_) == 1 else NEAR
    return bool(set(sound_keys(a)) & set(sound_keys(b))) and _alike(a, b, need) >= need


def group_alike(found: list[Suggestion]) -> list[Suggestion]:
    """One suggestion per thing: a near-duplicate heard this session is folded into the
    longest one it matches ("Oskar Vane", also "Vane"), with their times added up, but
    only when it matches exactly one: "Lord" between "Lord Neverember" and "Lord Dagult"
    stays its own question."""
    ordered = sorted(found, key=lambda s: (-len(s.name.split()), -s.times, s.name))
    fits = {
        s.name: [g.name for g in ordered if g.name != s.name and _same_thing(s.name, g.name)]
        for s in ordered
    }
    groups: list[Suggestion] = []
    for suggestion in ordered:
        homes = [
            i
            for i, g in enumerate(groups)
            if any(_same_thing(suggestion.name, n) for n in (g.name, *g.also))
        ]
        longer = [
            n for n in fits[suggestion.name] if len(n.split()) >= len(suggestion.name.split())
        ]
        if len(homes) == 1 and len(longer) <= 1:
            g = groups[homes[0]]
            groups[homes[0]] = replace(
                g, also=(*g.also, suggestion.name), times=g.times + suggestion.times
            )
        else:
            groups.append(suggestion)
    groups.sort(key=lambda s: (-s.times, s.name))
    return groups
