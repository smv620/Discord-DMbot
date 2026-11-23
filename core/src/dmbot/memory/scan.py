"""The after-session scan (docs/PLAN.md, "Campaign memory", where names come from).

After a session, DMbot reads what it heard and suggests names it doesn't know yet, for
the DM to confirm: "📝 Hrothgar: new name, heard 4 times. Someone or something in your
game?" It only *suggests*; nothing counts until the DM says yes.

Pure, no database. Speech-to-text writes names it doesn't know with capitals, so a
candidate is a capitalized word or run of words that:
- appears capitalized in the middle of a sentence at least once (so "Then" at the start
  of a sentence isn't a name),
- never appears in lower case in the session ("Roll" next to "roll" is a word); the one
  exception is a word that leads a longer name mid-sentence: "the Dwarven Valley" is a name
  even when "the dwarven city" is said too, as long as "Valley" itself is never lower case
  ("the Dwarven city" offers nothing), and such a word is never offered alone (#5055),
- isn't a common word, a game term, or anything DMbot already has an answer for (known
  names, names the DM said aren't names, "keep as heard" words, people at the table),
  and isn't part of a known name said whole in that sentence ("Oskar" in "Oskar Vane",
  #399); the same word alone, away from any known name, is still offered,
- was heard at least `MIN_TIMES` times.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from dmbot.memory.models import NAME_MAX, name_key
from dmbot.memory.name_longer import widened
from dmbot.memory.sounds import sound_codes
from dmbot.names import not_names

MIN_TIMES = 2
MAX_SUGGESTIONS = 10
MAX_WORDS = 3  # "Oskar Vane", "Ten Towns", "Caer Dineval"
# Near a known name (#394): spelled at least this alike, and sounding alike. One word
# needs more, like the Transcript Cleaner ("Mary" is not the character Mara).
NEAR = 0.8
NEAR_ONE_WORD = 0.9
TIE = 0.05  # two known names this close in likeness: neither is offered

_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\n+")
# Letters, any script. A comma, semicolon or slash is kept as a word of its own ("," below), so a
# list of names ("Caer-Dineval, Caer-Konig, Lonelywood") is never read as one run of name words
# (#3383); it is not name-like, so it ends a run.
_WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|['’-](?=[^\W\d_]))*|[,;/]")
_POSSESSIVE = re.compile(r"['’]s$")
_BREAKS = frozenset({",", ";", "/"})
# The loosened scan (docs/design/session_names.md, section 3; on only beside the name reader): a
# run may go on through "of" or "of the" to more name words ("Spine of the World", "Heart of
# Chardalyn"). Those words don't count toward MAX_WORDS.
JOINING = "of"
_THE = "the"

# Why the scan left a candidate out, for the per-name trace (design section 10, rule 6). Fixed
# words; `find_new_names(refusals=...)` collects them, the bot adds ORDINARY and GROUPED.
TOO_FEW = "too_few_hearings"
AT_START = "only_at_a_sentence_start"
TOO_LONG = "too_long"
INSIDE = "only_inside_a_longer_name"
ORDINARY = "ordinary_word"  # also said in lower case, and the name reader didn't find it
GROUPED = "grouped_into_another"


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


@dataclass(frozen=True, slots=True)
class Suggestion:
    name: str  # as heard most often
    times: int
    also: tuple[str, ...] = ()  # near-duplicates folded in ("Vane" with "Oskar Vane")
    # The loosened scan only: every word of it is also said in lower case ("Hope" beside "hope"),
    # so it may be an ordinary word. The scan alone never suggests it: it goes on only when the
    # name reader also found it (design section 3).
    ordinary: bool = False


def _bare(word: str) -> str:
    """A word without its "'s": "Kelvin's" is looked up as "Kelvin"."""
    return _POSSESSIVE.sub("", word)


def _text(run: Sequence[str]) -> str:
    """A run of words as a name: a possessive inside it is kept ("Kelvin's Cairn"), one at its end
    is not ("Hrothgar's axe" is Hrothgar)."""
    return " ".join([*run[:-1], _bare(run[-1])]) if run else ""


def _sentences(lines: Iterable[str], *, possessives: bool = False) -> list[list[str]]:
    """Each sentence's words. Their "'s" is dropped, unless `possessives` (the loosened scan keeps
    it, so a name with one inside can be read whole; `_text` drops the one at a name's end)."""
    out = []
    for line in lines:
        for sentence in _SENTENCE.split(line):
            words = [w if possessives else _bare(w) for w in _WORD.findall(sentence)]
            while words and words[0] in _BREAKS:
                words.pop(0)  # a sentence led by a comma would shift the mid-sentence test
            if words:
                out.append(words)
    return out


def _is_name_word(word: str, skip: frozenset[str], lower_words: set[str]) -> bool:
    bare = _bare(word)
    return word[:1].isupper() and name_key(bare) not in skip and bare.casefold() not in lower_words


# The many-word known names, by their first word: {"oskar": [("vane",), ...]}.
KnownNames = dict[str, list[tuple[str, ...]]]


def _many_word_names(skip: Iterable[str]) -> KnownNames:
    """Built once per scan, so each sentence only looks at the names that start with one
    of its own words (a big campaign has thousands)."""
    index: KnownNames = {}
    for known in skip:
        parts = known.split()
        if len(parts) > 1:
            index.setdefault(parts[0], []).append(tuple(parts[1:]))
    return index


def _known_name_spans(words: list[str], many: KnownNames) -> set[int]:
    """The positions of words that belong to a many-word known name said whole here
    ("Oskar Vane"), so that a word of it ("Oskar", whose other half "Vane" is also a
    known name) isn't offered as a new name (#399)."""
    if not many:
        return set()
    keys = [name_key(_bare(w)) for w in words]
    covered: set[int] = set()
    for start, key in enumerate(keys):
        for rest in many.get(key, ()):
            end = start + 1 + len(rest)
            if tuple(keys[start + 1 : end]) == rest:
                covered.update(range(start, end))
    return covered


def _candidates(
    words: list[str],
    skip: frozenset[str],
    lower_words: set[str],
    many: KnownNames,
    *,
    loose: bool = False,
    too_long: list[str] | None = None,
) -> list[tuple[str, bool, str]]:
    """Each run of name-like words as (text, said mid-sentence, the key of the longer name that
    holds it here, or ""). Runs longer than MAX_WORDS name words are dropped, not chopped (no
    "Caer Dineval Ice"), and listed in `too_long` when given. A run that starts the
    sentence also offers its tail, so "Ask Hrothgar" still finds Hrothgar. A known
    name said whole ends a run: what's left on either side is still looked at. `loose`: the
    run goes on through "of" or "of the" to more name words, and each part of it is kept as
    well, marked (design section 3)."""
    covered = _known_name_spans(words, many)

    def name_like(index: int) -> bool:
        return index not in covered and _is_name_word(words[index], skip, lower_words)

    def joined_to(index: int) -> int:
        # "of" or "of the" at `index` followed by a name word: where that word is, else `index`
        if not loose or index >= len(words) or words[index].casefold() != JOINING:
            return index
        after = index + 1
        if after < len(words) and words[after].casefold() == _THE:
            after += 1
        return after if after < len(words) and name_like(after) else index

    def leads(index: int) -> bool:
        # A capitalized word also said in lower case ("dwarven") may lead a name that follows it
        # mid-sentence (#5055: Supervisor's ruling). Only one such word, never at the sentence's
        # start (where every word has a capital), and never a common word, a known one, a
        # pronoun or a word of address: "so I Hrothgar" must still find Hrothgar.
        word = words[index]
        return (
            word[:1].isupper()
            and index not in covered
            and name_key(word) not in skip
            and not not_names.is_not_name(word)
            and not_names.shape_of(word) is None
        )

    out: list[tuple[str, bool, str]] = []

    def joined(i: int, j: int, parts: Sequence[tuple[int, int]]) -> None:
        whole = _text(words[i:j])
        out.append((whole, i > 0, ""))
        for a, b in parts:
            part = _text(words[a:b])
            if not not_names.is_not_name(part, sentence_start=a == 0):
                out.append((part, a > 0, name_key(whole)))

    i = 0
    while i < len(words):
        if not name_like(i):
            i += 1
            continue
        j = i
        parts: list[tuple[int, int]] = []  # each stretch of name words, between joining words
        while True:
            start = j
            while j < len(words) and name_like(j):
                j += 1
            parts.append((start, j))
            if (on := joined_to(j)) == j:
                break
            j = on
        names = sum(b - a for a, b in parts)  # (joining words don't count toward MAX_WORDS)
        run = _text(words[i:j])
        if len(parts) > 1:
            # "Spine of the World": the whole is one candidate, and each part is kept too, marked,
            # so "Icewind Dale" heard alone elsewhere keeps every sighting and a part only ever
            # said inside it ("World") is never offered alone. Each part is also looked at as a run
            # of its own (a pronoun, a form of address), as the whole is below.
            if names <= MAX_WORDS and not_names.shape_of(run) is None:
                joined(i, j, parts)
                if i == 0 and parts[0][1] - parts[0][0] > 1:  # "Ask Heart of Chardalyn"
                    joined(1, j, [(1, parts[0][1]), *parts[1:]])
            elif names > MAX_WORDS and too_long is not None:
                too_long.append(run)
            i = j
            continue
        # A pronoun is never offered as a name (#2358), unless this sentence plainly names
        # someone by it ("My name is We"). A run led by one ("We Should") goes with it.
        if not_names.is_not_name(" ".join(words[i:j]), sentence_start=i == 0) and not (
            not_names.keep(" ".join(words[i:j]), " ".join(words), sentence_start=i == 0)
        ):
            i = j
            continue
        if not_names.shape_of(" ".join(words[i:j])) is not None:
            i = j  # a form of address ("Friend"), a number, a measure or a time: no name (#3394)
            continue
        if j - i > MAX_WORDS and too_long is not None:
            too_long.append(run)
        if j - i <= MAX_WORDS:
            # "the Dwarven Valley" (#5055): the led name is offered too, and its plain tail is
            # kept as well, marked, so a name also heard alone ("Hrothgar" beside "Brave
            # Hrothgar") keeps every sighting; find_new_names drops the tail only when it was
            # never heard unled. Not after a comma (a clause start, capitalized like a
            # sentence's), and never past MAX_WORDS.
            led = (
                i >= 2
                and words[i - 2] not in _BREAKS
                and j - i < MAX_WORDS
                and leads(i - 1)
                and not not_names.is_not_name(" ".join(words[i - 1 : j]))
                and not_names.shape_of(" ".join(words[i - 1 : j])) is None
            )
            led_key = name_key(_text(words[i - 1 : j])) if led else ""
            out.append((run, i > 0, led_key))
            if led:
                out.append((_text(words[i - 1 : j]), True, ""))
            if i == 0 and j - i > 1:
                out.append((_text(words[1:j]), True, ""))
        i = j
    return out


def find_new_names(
    lines: Iterable[str],
    skip_keys: Iterable[str] = (),
    *,
    min_times: int = MIN_TIMES,
    unlimited: bool = False,
    loose: bool = False,
    refusals: list[tuple[str, str]] | None = None,
) -> list[Suggestion]:
    """Names in these lines (cleaned, so names fixed live are known) worth asking the DM
    about, most heard first: at most MAX_SUGGESTIONS (read when called), or all of them
    with `unlimited` (to group them, then cap).

    `loose` (docs/design/session_names.md, section 3; only beside the name reader, which then
    judges): a word also said in lower case may be a name, a name may hold "of" or "of the" and a
    possessive, and a name all of whose words are also said in lower case is marked `ordinary`
    for the reader to confirm. `refusals`, when given, gets (name, reason) for each candidate
    left out, for the dev-tools trace: words a person at the table or the DM has answered for
    are never candidates, so never listed."""
    lines = list(lines)
    sentences = _sentences(lines, possessives=loose)
    skip = frozenset({name_key(k) for k in skip_keys} | COMMON | GAME_TERMS)
    lower_words = {_bare(w).casefold() for words in sentences for w in words if w[:1].islower()}
    long_runs: list[str] | None = [] if refusals is not None else None
    many = _many_word_names(skip)
    counts: Counter[str] = Counter()
    spellings: dict[str, Counter[str]] = {}
    mid_sentence: set[str] = set()
    led_times: Counter[str] = Counter()  # sightings of a name inside a longer led name (#5055)
    led_by: dict[str, set[str]] = {}
    for words in sentences:
        # (loose: no word is ruled out for being said in lower case, design rule 2)
        for text, mid, led_key in _candidates(
            words, skip, set() if loose else lower_words, many, loose=loose, too_long=long_runs
        ):
            key = name_key(text)
            if not key or len(text) > NAME_MAX or key in skip:
                continue
            counts[key] += 1
            if led_key:
                led_times[key] += 1
                led_by.setdefault(key, set()).add(led_key)
            spellings.setdefault(key, Counter())[text] += 1
            if mid:
                mid_sentence.add(key)
    offered = {k for k, times in counts.items() if times >= min_times and k in mid_sentence}
    found = []
    left_out: dict[str, tuple[str, str]] = {}  # key -> (name, reason), for `refusals`
    for key, times in counts.items():
        if key in offered and led_times[key] == times and led_by[key] & offered:
            # only ever heard inside "the Dwarven Valley": "Valley" is not offered alone
            left_out[key] = (spellings[key].most_common(1)[0][0], INSIDE)
            continue
        if key not in offered:
            reason = TOO_FEW if times < min_times else AT_START
            left_out[key] = (spellings[key].most_common(1)[0][0], reason)
        if key in offered:
            name = spellings[key].most_common(1)[0][0]
            # A place's last word is often lower case in the transcript ("Harrow's keep"): when the
            # same word is followed by the same place word often enough, the longer name is what is
            # offered (#2570). Still only a suggestion; "Harrow" said alone elsewhere is not lost.
            longer = widened(lines, name, min_times)
            if longer and len(longer) <= NAME_MAX:
                if name_key(longer) in skip:
                    continue  # the DM already answered for the longer name: not its first word
                name = longer
            found.append(Suggestion(name, times, ordinary=loose and _ordinary(name, lower_words)))
    if refusals is not None:
        for run in long_runs or ():
            left_out.setdefault(name_key(run), (run, TOO_LONG))
        refusals += sorted(left_out.values(), key=lambda r: (r[1], r[0].casefold()))
    found.sort(key=lambda s: (-s.times, s.name))
    return found if unlimited else found[:MAX_SUGGESTIONS]


def _ordinary(name: str, lower_words: set[str]) -> bool:
    """Every name word of it is also said in lower case this session ("Hope", "Ten Towns")."""
    words = [_bare(w).casefold() for w in name.split() if w.casefold() not in (JOINING, _THE)]
    return bool(words) and all(w in lower_words for w in words)


def _alike(a: str, b: str, need: float = 0.0) -> float:
    """How alike two spellings are, 0 to 1, ignoring case, spaces and punctuation;
    0 at once when they can't reach `need` (a quick check first, for big campaigns)."""
    matcher = difflib.SequenceMatcher(
        None, name_key(a).replace(" ", ""), name_key(b).replace(" ", "")
    )
    if matcher.real_quick_ratio() < need or matcher.quick_ratio() < need:
        return 0.0
    return matcher.ratio()


alike = _alike  # (public: how alike two spellings are, 0 to 1)

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
    entity_id, (_, own) = ranked[0]
    return Match(entity_id, own)


def sound_keys(name: str) -> tuple[str, ...]:
    """The sound codes a suggested name is looked up by."""
    return sound_codes(name.replace(" ", ""))


def _same_thing(a: str, b: str) -> bool:
    """Is the shorter one part of the longer ("Vane" in "Oskar Vane"), or do they sound
    and look alike (one-word names: as alike as the Cleaner needs, "Kael" isn't
    "Kaela")?"""
    short, long_ = sorted((name_key(a).split(), name_key(b).split()), key=len)
    # (never part of a name joined by "of": "Chardalyn" is not the Heart of Chardalyn, nor
    # "Hrothgar" Hrothgar of Bryn Shander, design section 3)
    if (
        len(short) < len(long_)
        and JOINING not in long_
        and (long_[: len(short)] == short or long_[-len(short) :] == short)
    ):
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
                g,
                also=(*g.also, suggestion.name),
                times=g.times + suggestion.times,
                ordinary=g.ordinary and suggestion.ordinary,  # (one plain name word is enough)
            )
        else:
            groups.append(suggestion)
    groups.sort(key=lambda s: (-s.times, s.name))
    return groups
