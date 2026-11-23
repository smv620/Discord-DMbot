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
from collections.abc import Collection, Iterable, Sequence
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
# Other small words a name may hold ("Wulfgar the Mighty", "Hrothgar in Bryn Shander" may not):
# never joined, only counted in the trace when one stands between two name words, so the sample
# shows whether any is worth adding (the Expert, on slice 2d).
SMALL_WORDS = frozenset({"the", "on", "in", "upon", "by", "at", "under", "von", "van", "de", "du"})

# Why the scan left a candidate out, for the per-name trace (design section 10, rule 6). Fixed
# words; `find_new_names(refusals=...)` collects them, the bot adds ORDINARY and GROUPED.
TOO_FEW = "too_few_hearings"
AT_START = "only_at_a_sentence_start"
TOO_LONG = "too_long"
INSIDE = "only_inside_a_longer_name"
SMALL_WORD = "stopped_at_a_small_word"  # ("Wulfgar the Mighty", the whole run: counted only)
ORDINARY = "ordinary_word"  # also said in lower case, and the name reader didn't find it
# ...and some of the reader's batches were not read: a call's fault, maybe not the word's, so
# why_missed must not blame the word. (When the reader did not read at all, the bot keeps the
# strict scan's list, which never holds an ordinary word.)
ORDINARY_PART_READ = "ordinary_word_some_lines_not_read"
SPLIT = "split_by_the_reader"  # "Wulfgar of Icewind Dale": the reader named a part, not the whole
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
    known name) isn't offered as a new name (#399). A word is matched with and without its
    "'s", so a known "Kelvin's Cairn" is found too."""
    if not many:
        return set()
    forms = [(name_key(_bare(w)), name_key(w)) for w in words]
    covered: set[int] = set()
    for start, keys in enumerate(forms):
        for rest in {r for key in set(keys) for r in many.get(key, ())}:
            end = start + 1 + len(rest)
            got = forms[start + 1 : end]
            if len(got) == len(rest) and all(
                want in pair for want, pair in zip(rest, got, strict=True)
            ):
                covered.update(range(start, end))
    return covered


def _candidates(
    words: list[str],
    skip: frozenset[str],
    lower_words: set[str],
    many: KnownNames,
    *,
    loose: bool = False,
    said_lower: set[str] | None = None,
    too_long: list[str] | None = None,
    small: list[str] | None = None,
) -> list[tuple[str, bool, str]]:
    """Each run of name-like words as (text, said mid-sentence, the key of the longer name that
    holds it here, or ""). Runs longer than MAX_WORDS name words are dropped, not chopped (no
    "Caer Dineval Ice"), and listed in `too_long` when given. A run that starts the
    sentence also offers its tail, so "Ask Hrothgar" still finds Hrothgar. A known
    name said whole ends a run: what's left on either side is still looked at.

    `loose` (design section 3): the run goes on through "of" or "of the" to more name words,
    and each part of it is kept as well, marked; a join that can't be a name (too long, a
    pronoun, a number) is read as its parts instead. A run led by words also said in lower case
    (`said_lower`: "Brave Hrothgar") keeps the name after them too, marked, as #5086 does for
    the strict scan. `small`, when given, gets each run that stopped at a small word before more
    name words, with them (`SMALL_WORDS`, for the trace)."""
    covered = _known_name_spans(words, many)
    said_lower = said_lower or set()

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
        # person at the table ("Mia's" is Mia), a pronoun or a word of address: "so I Hrothgar"
        # must still find Hrothgar.
        word = words[index]
        bare = _bare(word)
        return (
            word[:1].isupper()
            and index not in covered
            and name_key(bare) not in skip
            and not not_names.is_not_name(bare)
            and not_names.shape_of(bare) is None
        )

    def can_be_a_name(text: str, at: int) -> bool:
        return (
            not not_names.is_not_name(text, sentence_start=at == 0)
            and not_names.shape_of(text) is None
        )

    out: list[tuple[str, bool, str]] = []

    def plain(i: int, j: int) -> None:
        """One run with no joining word in it."""
        run = _text(words[i:j])
        spoken = " ".join(words[i:j])
        # A pronoun is never offered as a name (#2358), unless this sentence plainly names
        # someone by it ("My name is We"). A run led by one ("We Should") goes with it.
        if not_names.is_not_name(spoken, sentence_start=i == 0) and not (
            not_names.keep(spoken, " ".join(words), sentence_start=i == 0)
        ):
            return
        if not_names.shape_of(spoken) is not None:
            return  # a form of address ("Friend"), a number, a measure or a time: no name (#3394)
        if j - i > MAX_WORDS:
            if too_long is not None:
                too_long.append(run)
            return
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
            and can_be_a_name(" ".join(words[i - 1 : j]), i - 1)
        )
        led_key = name_key(_text(words[i - 1 : j])) if led else ""
        out.append((run, i > 0, led_key))
        if led:
            out.append((_text(words[i - 1 : j]), True, ""))
        if i == 0 and j - i > 1:
            out.append((_text(words[1:j]), True, ""))
        # The loose scan's own "Brave Hrothgar": the same rule, for words it no longer rules out
        # (once per sighting: the sentence-start tail above already holds "Ask Hrothgar"'s)
        k = i
        while loose and k < j and _bare(words[k]).casefold() in said_lower:
            k += 1
        if i < k < j and not (i == 0 and k == 1) and can_be_a_name(_text(words[k:j]), k):
            out.append((_text(words[k:j]), True, name_key(run)))

    def joined(i: int, j: int, parts: Sequence[tuple[int, int]]) -> None:
        whole = _text(words[i:j])
        out.append((whole, i > 0, ""))
        for a, b in parts:
            part = _text(words[a:b])
            if can_be_a_name(part, a):
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
        if (
            small is not None
            and j + 1 < len(words)
            and words[j].casefold() in SMALL_WORDS
            and name_like(j + 1)
        ):
            end = j + 1
            while end < len(words) and name_like(end):
                end += 1
            small.append(_text(words[i:end]))
        if len(parts) == 1:
            plain(i, j)
            i = j
            continue
        # "Spine of the World": the whole is one candidate, and each part is kept too, marked,
        # so "Icewind Dale" heard alone elsewhere keeps every sighting and a part only ever said
        # inside it ("World") is never offered alone. The whole and each part must each be able
        # to be a name (no pronoun, number or form of address).
        run = _text(words[i:j])
        names = sum(b - a for a, b in parts)  # (joining words don't count toward MAX_WORDS)
        if names <= MAX_WORDS and can_be_a_name(run, i):
            if i == 0 and parts[0][1] - parts[0][0] > 1:
                # "Ask Heart of Chardalyn": read from the second word, as "Ask Hrothgar" is
                # (the run from the first word is kept unmarked, never its parts twice)
                out.append((run, False, ""))
                joined(1, j, [(1, parts[0][1]), *parts[1:]])
            else:
                joined(i, j, parts)
        else:
            # (a join that can't be one name: each part is read as if "of" had ended it, so the
            # loose scan never loses what the strict one finds)
            if names > MAX_WORDS and too_long is not None:
                too_long.append(run)
            for a, b in parts:
                plain(a, b)
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
    small_runs: list[str] | None = [] if refusals is not None and loose else None
    many = _many_word_names(skip)
    counts: Counter[str] = Counter()
    spellings: dict[str, Counter[str]] = {}
    mid_sentence: set[str] = set()
    led_times: Counter[str] = Counter()  # sightings of a name inside a longer led name (#5055)
    led_by: dict[str, set[str]] = {}
    for words in sentences:
        # (loose: no word is ruled out for being said in lower case, design rule 2)
        for text, mid, led_key in _candidates(
            words,
            skip,
            set() if loose else lower_words,
            many,
            loose=loose,
            said_lower=lower_words if loose else None,
            too_long=long_runs,
            small=small_runs,
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
        for run in small_runs or ():
            left_out.setdefault(name_key(run), (run, SMALL_WORD))
        refusals += sorted(left_out.values(), key=lambda r: (r[1], r[0].casefold()))
    found.sort(key=lambda s: (-s.times, s.name))
    return found if unlimited else found[:MAX_SUGGESTIONS]


def joined_parts(name: str) -> list[str]:
    """The names a name joined by "of" holds: "Wulfgar of Icewind Dale" is Wulfgar and Icewind
    Dale; any other name is itself alone."""
    words = name.split()
    parts: list[list[str]] = [[]]
    i = 0
    while i < len(words):
        if words[i].casefold() == JOINING and parts[-1] and i + 1 < len(words):
            i += 2 if words[i + 1].casefold() == _THE and i + 2 < len(words) else 1
            parts.append([])
            continue
        parts[-1].append(words[i])
        i += 1
    return [" ".join(p) for p in parts if p]


def split_joined(
    found: Iterable[Suggestion], named: Collection[str], skip: Collection[str] = ()
) -> tuple[list[Suggestion], list[Suggestion]]:
    """A name joined by "of" that the name reader did not return, when it did return a part of it,
    is two names ("Wulfgar of Icewind Dale": a person and a place, the Expert on slice 2d): its
    parts are offered in its place, each heard as often as it, unless already listed or in `skip`.
    `named`: the keys the reader returned. Returns the list, in order, and the names split."""
    out: list[Suggestion] = []
    split: list[Suggestion] = []
    found = list(found)
    have = {name_key(f.name) for f in found}
    for f in found:
        parts = joined_parts(f.name)
        if (
            len(parts) > 1
            and name_key(f.name) not in named
            and any(name_key(p) in named for p in parts)
        ):
            split.append(f)
            for part in parts:
                if (key := name_key(part)) not in have and key not in skip:
                    out.append(Suggestion(part, f.times))
                    have.add(key)
            continue
        out.append(f)
    return out, split


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
