"""The Transcript Cleaner, first part: fix misheard names as each line is written down
(docs/PLAN.md, "Transcript Cleaner"; #127). Pure: no Discord, no database, no AI.

Every fix here is a silent one, so it only fixes what it's sure of, and leaves the
words as heard otherwise (a wrong fix is worse than a missed one):

- **Same letters, other spelling:** "Kazeth" → "Ka'zeth", "bryn Shander" → "Bryn
  Shander" (`scene.find_mentions` finds them). A word in lower case is only fixed if
  its letters differ, so "ring the bell" never becomes "Bell".
- **A spelling the DM fixed:** "Sara" → "Cerric", once the DM said that's who it means.
- **Sounds like one name:** a word speech-to-text didn't know ("Beleros"), or a name it
  split ("Ka Zeth"), that sounds like exactly one confirmed name (`lookup.by_sound`) and
  is spelled much like it. A word counts as unknown when speech-to-text gave it a
  capital, it isn't a common word or a game term, and nobody said it in lower case this
  session ("Thorn" next to "a thorn" is a word). A word starting a sentence gets a
  capital anyway, so it counts only once it was also written with one mid-sentence.
  Words in lower case are never changed this way ("Bell or us" waits for the DM's
  answer, later).

Only confirmed, non-secret names make a fix; a name DMbot only suggested never does.
Nothing is changed inside a secret name, a known name, a "keep as heard" word or the
name of someone at the table, and a word that also sounds like a secret name is left
alone, so a fix can never write a secret identity into a transcript. A fix writes the
name the way it was said ("the Frostwolfs" → "the Frostwolves", not "Frostwolf tribe"):
the spelling of what was said, never the meaning.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from dmbot.memory.lookup import CampaignLookup, NameEntry
from dmbot.memory.models import CONFIRMED, name_key
from dmbot.memory.scan import COMMON, GAME_TERMS
from dmbot.memory.scene import LONGEST_NAME_WORDS, PLAYER_CHARACTER, WORD, find_mentions
from dmbot.memory.sounds import sound_codes

MAX_JOINED = 3  # a name split into at most this many words ("Ka Zeth", "Bry N Shander")
MIN_LETTERS = 4  # shorter words sound like too many names
MIN_LIKENESS = 0.7  # how alike the spelling must be (0 to 1) for a fix by sound
WORDS_KEPT = 5_000  # per speaker: words said in lower case this session
_POSSESSIVE = ("'s", "’s")
_SENTENCE_END = re.compile(r"[.!?…]")

SPELLING, DM_FIX, SOUND = "spelling", "dm", "sound"


@dataclass(frozen=True, slots=True)
class Fix:
    start: int  # characters in the line as heard
    end: int
    heard: str
    written: str
    entity_id: str
    how: str  # SPELLING, DM_FIX or SOUND


@dataclass(frozen=True, slots=True)
class Cleaned:
    text: str
    fixes: tuple[Fix, ...]


@dataclass(slots=True)
class Vocabulary:
    """What this session's lines say about words, per speaker: words said in lower case
    (so a capitalized "Thorn" is a real word) and words written with a capital in the
    middle of a sentence (so the same word starting a sentence is a name too). In memory
    with the running session only; a speaker who stops being recorded is forgotten."""

    limit: int = WORDS_KEPT
    lower: dict[int, set[str]] = field(default_factory=dict)
    named: dict[int, set[str]] = field(default_factory=dict)

    def note(self, speaker: int, text: str) -> None:
        lower, named = _lower_words(text), _named_words(text)
        for kept, new in ((self.lower, lower), (self.named, named)):
            mine = kept.setdefault(speaker, set())
            for word in new:
                if len(mine) >= self.limit:
                    break
                mine.add(word)

    def is_word(self, word: str) -> bool:
        return _said(self.lower, word.casefold())

    def is_name(self, word: str) -> bool:
        return _said(self.named, word.casefold())

    def forget_speaker(self, speaker: int) -> None:
        self.lower.pop(speaker, None)
        self.named.pop(speaker, None)


def _said(kept: dict[int, set[str]], key: str) -> bool:
    return any(key in words for words in kept.values())


def _lower_words(text: str) -> set[str]:
    return {w.group() for w in WORD.finditer(text) if w.group().islower()}


def _starts(text: str, words: list[re.Match[str]]) -> set[int]:
    """The words that start a sentence (speech-to-text gives those a capital anyway)."""
    starts = set()
    at = 0
    for i, word in enumerate(words):
        if i == 0 or _SENTENCE_END.search(text[at : word.start()]):
            starts.add(i)
        at = word.end()
    return starts


def _named_words(text: str) -> set[str]:
    """Words with a capital in the middle of a sentence, in lower case."""
    words = list(WORD.finditer(text))
    starts = _starts(text, words)
    return {
        _stem(w.group()).casefold()
        for i, w in enumerate(words)
        if i not in starts and w.group()[:1].isupper()
    }


def likeness(a: str, b: str) -> float:
    """How alike two spellings are, 0 to 1, ignoring case, spaces and punctuation."""
    return difflib.SequenceMatcher(
        None, name_key(a).replace(" ", ""), name_key(b).replace(" ", "")
    ).ratio()


def own_name(lookup: CampaignLookup, entity_id: str) -> str | None:
    """An entry's own name as written, if it's a confirmed name that isn't secret."""
    entity = lookup.entities.get(entity_id)
    if entity is None or entity.status != CONFIRMED:
        return None
    for entry in lookup.by_key.get(name_key(entity.name), ()):
        if entry.entity_id == entity_id and entry.confirmed and not entry.secret:
            return entry.text
    return None


def characters(lookup: CampaignLookup) -> dict[int, str]:
    """Each player's character (Discord user ID → name), from the campaign's confirmed
    player characters. A player with more than one gets the newest."""
    newest: dict[int, tuple[int, str]] = {}
    for entity in lookup.entities.values():
        if entity.type != PLAYER_CHARACTER or entity.played_by is None:
            continue
        name = own_name(lookup, entity.id)
        if name is None:
            continue
        best = newest.get(entity.played_by)
        if best is None or entity.created_at > best[0]:
            newest[entity.played_by] = (entity.created_at, name)
    return {player: name for player, (_, name) in newest.items()}


def clean(
    lookup: CampaignLookup,
    heard: str,
    *,
    vocabulary: Vocabulary | None = None,
    people: Iterable[str] = (),
) -> Cleaned:
    """The line with the names it's sure were misheard fixed, and the fixes made.
    `vocabulary`: what this session's lines said about words (see `Vocabulary`), this
    line not included; `people`: display names of people at the table, never changed."""
    words = list(WORD.finditer(heard))
    keys = [name_key(w.group()) for w in words]
    person_keys = {name_key(p) for p in people}
    n = len(words)
    # Words that are already something DMbot knows: never changed by sound.
    known = [False] * n
    for start in range(n):
        for end in range(start + 1, min(n, start + LONGEST_NAME_WORDS) + 1):
            key = " ".join(keys[start:end])
            if (
                key in lookup.by_key
                or key in lookup.fixes
                or key in lookup.keep_keys
                or key in person_keys
            ):
                known[start:end] = [True] * (end - start)

    fixes = [*_known_names(lookup, heard), *_by_sound(lookup, heard, words, known, vocabulary)]
    fixes.sort(key=lambda f: f.start)
    out, at = [], 0
    for fix in fixes:
        out += [heard[at : fix.start], fix.written]
        at = fix.end
    out.append(heard[at:])
    return Cleaned("".join(out), tuple(fixes))


def _known_names(lookup: CampaignLookup, heard: str) -> list[Fix]:
    """Fixes for names DMbot already matches word for word: another spelling of the
    same letters, or a spelling the DM fixed. The longest name wins where they overlap;
    a place that could be two different entries is left alone."""
    by_span: dict[tuple[int, int], list[tuple[str, str]]] = {}
    for found in find_mentions(lookup, heard):
        by_span.setdefault(found.span, []).append((found.entity_id, found.method))
    taken: list[tuple[int, int]] = []
    fixes = []
    for span in sorted(by_span, key=lambda s: (s[0] - s[1], s[0])):  # longest first
        if any(span[0] < end and start < span[1] for start, end in taken):
            continue
        taken.append(span)
        hits = by_span[span]
        if len({entity_id for entity_id, _ in hits}) != 1:
            continue  # two entries answer to it: not sure which
        entity_id = hits[0][0]
        said = heard[span[0] : span[1]]
        if any(method == "exact" for _, method in hits):
            written = _respelled(lookup, said)
            how = SPELLING
        else:
            written = own_name(lookup, entity_id)
            how = DM_FIX
        if written is not None and written != said:
            fixes.append(Fix(span[0], span[1], said, written, entity_id, how))
    return fixes


def _respelled(lookup: CampaignLookup, said: str) -> str | None:
    """How a known name is written, if `said` has the same letters spelled another way.
    A word in lower case with the same letters is a real word ("bell"), so only its
    punctuation or accents are fixed, never its capitals."""
    texts = {
        e.text
        for e in lookup.by_key.get(name_key(said), ())
        if e.confirmed and not e.secret  # confirmed means its entry is confirmed too
    }
    if len(texts) != 1 or said in texts:
        return None  # not a confirmed name, or written two ways, or already right
    (text,) = texts
    if said.casefold() == text.casefold() and not said[:1].isupper():
        return None
    return text


def _by_sound(
    lookup: CampaignLookup,
    heard: str,
    words: list[re.Match[str]],
    known: list[bool],
    vocabulary: Vocabulary | None,
) -> list[Fix]:
    """Unknown capitalized words (1 to MAX_JOINED in a row) that sound like exactly one
    confirmed name and are spelled much like it. A word starting a sentence has its
    capital anyway, so it counts only once it was also written with one mid-sentence
    (in this line or earlier this session): "Thorn bushes everywhere" is never
    "Thorin"."""
    lower = {w.casefold() for w in _lower_words(heard)}
    named = _named_words(heard)
    starts = _starts(heard, words)

    def unknown(i: int, last: bool) -> bool:
        word = words[i].group()
        if known[i] or not word[:1].isupper():
            return False
        if not last and word.endswith(_POSSESSIVE):
            return False
        stem = _stem(word)
        key = name_key(stem)
        if key in COMMON or key in GAME_TERMS or stem.casefold() in lower:
            return False
        if vocabulary is not None and vocabulary.is_word(stem):
            return False
        if i in starts:
            return stem.casefold() in named or (vocabulary is not None and vocabulary.is_name(stem))
        return True

    fixes = []
    i = 0
    while i < len(words):
        for size in range(min(MAX_JOINED, len(words) - i), 0, -1):
            run = range(i, i + size)
            if not all(unknown(j, j == run[-1]) for j in run):
                continue
            if any(heard[words[j].end() : words[j + 1].start()] != " " for j in run[:-1]):
                continue  # only words with a plain space between are one split name
            start, end = words[i].start(), words[run[-1]].end()
            said = _stem(heard[start:end])
            fix = _sounds_like(lookup, said, start)
            if fix is not None:
                fixes.append(fix)
                i += size
                break
        else:
            i += 1
    return fixes


def _stem(word: str) -> str:
    """Without an "'s" at the end: "Beleros's" is matched as "Beleros"."""
    for ending in _POSSESSIVE:
        if word.endswith(ending):
            return word[: -len(ending)]
    return word


def _sounds_like(lookup: CampaignLookup, said: str, start: int) -> Fix | None:
    joined = said.replace(" ", "")
    if len(name_key(joined)) < MIN_LETTERS:
        return None
    matches: dict[str, NameEntry] = {}
    for code in sound_codes(joined):
        for entry in lookup.by_sound.get(code, ()):
            matches.setdefault(entry.alias_id, entry)
    if any(e.secret for e in matches.values()):
        return None  # it could be a secret name: never guess around one
    if len({e.entity_id for e in matches.values()}) != 1:
        return None  # nothing, or more than one entry, sounds like it
    usable = [e for e in matches.values() if e.confirmed]
    if not usable:
        return None  # only a name DMbot suggested: never a silent fix
    best = max(usable, key=lambda e: (likeness(said, e.text), e.text))
    if likeness(said, best.text) < MIN_LIKENESS:
        return None
    return Fix(start, start + len(said), said, best.text, best.entity_id, SOUND)
