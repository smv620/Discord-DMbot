"""The Transcript Cleaner, first part: fix misheard names as each line is written down
(docs/PLAN.md, "Transcript Cleaner"; #127). Pure: no Discord, no database, no AI.

Every fix here is a silent one, so it only fixes what it's sure of, and leaves the
words as heard otherwise (a wrong fix is worse than a missed one):

- **Same letters, other spelling:** "Kazeth" → "Ka'zeth", "Bryn shander" → "Bryn
  Shander" (`scene.find_mentions` finds them). A word in lower case is only fixed if
  its letters differ, so "ring the bell" never becomes "Bell".
- **A spelling the DM fixed:** "Sara" → "Cerric", once the DM said that's who it means.
- **Sounds like one name:** a word speech-to-text didn't know ("Beleros"), or a name it
  split ("Ka Zeth"), that sounds like exactly one confirmed name (`lookup.by_sound`) and
  is spelled much like it. A word counts as unknown when speech-to-text gave it a
  capital, it isn't a common word or a game term, and nobody said it in lower case this
  session ("Thorn" next to "a thorn" is a word); all capitals are left alone. A word
  starting a sentence gets a capital anyway, so it counts only once it was also
  written with one mid-sentence. One word alone also needs the name in the scene (said
  in the last ~10 minutes) and spelled closer (at least 0.8 alike), since real names
  and brands sound like campaign names too ("Mary" and Mara, 0.75). Words in lower
  case are never changed this way ("Bell or us" waits for the DM's answer, later).

Only confirmed, non-secret names make a fix; a name DMbot only suggested never does.
Nothing is changed inside a secret name, a known name, a "keep as heard" word or the
name of someone at the table, and no fix goes where the words, with the words around
them, sound like a secret name ("Silas Vain" for the secret "Silas Vane"). The line is
checked again as written: a DM's fixed spelling needs no likeness, so "Silas Bane" with
the rule "Bane" → Vane would write "Silas Vane"; such a fix is taken back. So a fix can
never write a secret identity into a transcript. A fix writes the
name the way it was said ("the Frostwolfs" → "the Frostwolves", not "Frostwolf tribe"):
the spelling of what was said, never the meaning.
"""

from __future__ import annotations

import difflib
import logging
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field

from dmbot.memory.lookup import CampaignLookup, NameEntry
from dmbot.memory.models import CONFIRMED, name_key
from dmbot.memory.scan import COMMON, GAME_TERMS
from dmbot.memory.scene import PLAYER_CHARACTER, WORD, find_mentions, group_sizes
from dmbot.memory.sounds import sound_codes

log = logging.getLogger(__name__)

MAX_REBUILDS = 8  # the line as written is rebuilt at most this often, then left as heard
MAX_JOINED = 3  # a name split into at most this many words ("Ka Zeth", "Bry N Shander")
MIN_LETTERS = 4  # shorter words sound like too many names
MIN_LIKENESS = 0.7  # how alike the spelling must be (0 to 1) for a fix by sound
MAX_OPTIONS = 3  # names offered in one "Did they mean…?"; more sounding alike: no question
ENTRIES_PER_NAME = 8  # of one entry's names sounding alike, the most weighed per word
# One word alone, by sound: real first names sound like campaign names ("Mary" for the
# NPC or character Mara, 0.75), so one word must be spelled closer than a joined name.
MIN_LIKENESS_ONE_WORD = 0.8
# Runs of words checked against secret names, per line. Enough for any real campaign
# (a few dozen); past it, the line's remaining fixes are dropped: no fix is the safe way.
SECRET_CHECKS_PER_LINE = 400
WORDS_KEPT = 20_000  # per speaker and kind: the most recently said words are kept
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
class Question:
    """Words that sound like two or three confirmed names: the DM is asked which one
    ("Did they mean…?", #296). Left as heard until then."""

    start: int  # characters in the line as heard
    end: int
    heard: str
    options: tuple[tuple[str, str], ...]  # (entity ID, its own name), most alike first


@dataclass(frozen=True, slots=True)
class Cleaned:
    text: str
    fixes: tuple[Fix, ...]
    questions: tuple[Question, ...] = ()


@dataclass(slots=True)
class Vocabulary:
    """What this session's lines say about words, per speaker: words said in lower case
    (so a capitalized "Thorn" is a real word) and words written with a capital in the
    middle of a sentence (so the same word starting a sentence is a name too). In memory
    with the running session only; a speaker who stops being recorded is forgotten."""

    limit: int = WORDS_KEPT
    # Insertion-ordered, so the least recently said word goes first when one is full.
    lower: dict[int, dict[str, None]] = field(default_factory=dict)
    named: dict[int, dict[str, None]] = field(default_factory=dict)

    def note(self, speaker: int, text: str) -> None:
        lower = {w.casefold() for w in _lower_words(text)}
        for kept, new in ((self.lower, lower), (self.named, _named_words(text))):
            mine = kept.setdefault(speaker, {})
            for word in new:
                mine.pop(word, None)
                mine[word] = None
                if len(mine) > self.limit:
                    del mine[next(iter(mine))]

    def is_word(self, word: str) -> bool:
        return _said(self.lower, word.casefold())

    def is_name(self, word: str) -> bool:
        return _said(self.named, word.casefold())

    def forget_speaker(self, speaker: int) -> None:
        self.lower.pop(speaker, None)
        self.named.pop(speaker, None)


def _said(kept: dict[int, dict[str, None]], key: str) -> bool:
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
    scene: Collection[str] = (),
) -> Cleaned:
    """The line with the names it's sure were misheard fixed, and the fixes made.
    `vocabulary`: what this session's lines said about words (see `Vocabulary`), this
    line not included; `people`: display names of people at the table (and their first
    words), never changed; `scene`: entries said lately (see `SceneTracker.scene`)."""
    words = list(WORD.finditer(heard))
    keys = [name_key(_stem(w.group())) for w in words]
    person_keys = {name_key(p) for p in people} | {
        name_key(p.split()[0]) for p in people if p.split()
    }
    n = len(words)
    # Words that are already something DMbot knows: never changed by sound.
    known = [False] * n
    for size in group_sizes(lookup):
        for start in range(n - size + 1):
            end = start + size
            key = " ".join(keys[start:end])
            if (
                key in lookup.by_key
                or key in lookup.fixes
                or key in lookup.keep_keys
                or key in person_keys
            ):
                known[start:end] = [True] * (end - start)

    near = _SecretChecks()
    by_sound, asking = _by_sound(lookup, heard, words, known, vocabulary, scene)
    fixes = [
        fix
        for fix in [*_known_names(lookup, heard, person_keys), *by_sound]
        if not _near_secret(lookup, words, fix.start, fix.end, near)
    ]
    questions = tuple(q for q in asking if not _near_secret(lookup, words, q.start, q.end, near))
    fixes.sort(key=lambda f: f.start)
    text, fixes = _without_secrets(lookup, heard, fixes, near)
    if near.ran_out:
        log.debug("Secret-name check ran out for a line: %d fix(es) kept", len(fixes))
    return Cleaned(text, tuple(fixes), questions)


def _apply(heard: str, fixes: list[Fix]) -> tuple[str, list[tuple[int, int]]]:
    """The line with `fixes` (in order) applied, and where each written name now is."""
    out, spans, at, length = [], [], 0, 0
    for fix in fixes:
        before = heard[at : fix.start]
        out += [before, fix.written]
        length += len(before)
        spans.append((length, length + len(fix.written)))
        length += len(fix.written)
        at = fix.end
    out.append(heard[at:])
    return "".join(out), spans


def _without_secrets(
    lookup: CampaignLookup, heard: str, fixes: list[Fix], near: _SecretChecks
) -> tuple[str, list[Fix]]:
    """The cleaned line, checked again as written: a fix whose name, with the words
    around it, is (or sounds like) a secret name is taken back, and the line rebuilt,
    until none is. The heard words alone can't show this: a spelling the DM fixed needs
    no likeness, so "Silas Bane" with the rule "Bane" → Vane writes "Silas Vane"."""
    left = SECRET_CHECKS_PER_LINE  # one budget for the line as written, however rebuilt
    for _ in range(MAX_REBUILDS):
        text, spans = _apply(heard, fixes)
        if not fixes or not lookup.secret_lengths:
            return text, fixes
        words = list(WORD.finditer(text))
        secret = _secret_spans(lookup, words)
        checks = _SecretChecks(left=left)  # answers are new each time: the text changed
        bad = {
            i
            for i, (begin, end) in enumerate(spans)
            if any(begin < b and a < end for a, b in secret)
            or _near_secret(lookup, words, begin, end, checks)
        }
        left, near.ran_out = checks.left, near.ran_out or checks.ran_out
        if not bad:
            return text, fixes
        fixes = [fix for i, fix in enumerate(fixes) if i not in bad]
    near.ran_out = True  # rebuilt too often: keep the line as heard
    return heard, []


def _secret_spans(lookup: CampaignLookup, words: list[re.Match[str]]) -> list[tuple[int, int]]:
    """Where a secret name stands, word for word, in a line (characters)."""
    keys = [name_key(_stem(w.group())) for w in words]
    spans = []
    for size in group_sizes(lookup):
        for start in range(len(words) - size + 1):
            key = " ".join(keys[start : start + size])
            if any(e.secret for e in lookup.by_key.get(key, ())):
                spans.append((words[start].start(), words[start + size - 1].end()))
    return spans


def _known_names(lookup: CampaignLookup, heard: str, person_keys: set[str]) -> list[Fix]:
    """Fixes for names DMbot already matches word for word: another spelling of the
    same letters, or a spelling the DM fixed. The longest name wins where they overlap;
    a place that could be two different entries is left alone, and so is the name of
    someone at the table (a DM's rule "Sara" → Cerric never renames a player Sara)."""
    by_span: dict[tuple[int, int], list[tuple[str, str]]] = {}
    for found in find_mentions(lookup, heard):
        by_span.setdefault(found.span, []).append((found.entity_id, found.method))
    taken: list[tuple[int, int]] = []
    fixes = []
    for span in sorted(by_span, key=lambda s: (s[0] - s[1], s[0])):  # longest first
        if any(span[0] < end and start < span[1] for start, end in taken):
            continue
        taken.append(span)
        said_words = [name_key(_stem(w)) for w in heard[span[0] : span[1]].split()]
        if name_key(_stem(heard[span[0] : span[1]])) in person_keys or any(
            w in person_keys for w in said_words
        ):
            continue  # someone at the table ("Sara's turn", "thanks Sara Bell")
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
        if written is not None and said != _stem(said) and written == _stem(written):
            written += said[len(_stem(said)) :]  # keep the "'s": "Bane's dog" → "Vane's dog"
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
    scene: Collection[str],
) -> tuple[list[Fix], list[Question]]:
    """Unknown capitalized words (1 to MAX_JOINED in a row) that sound like exactly one
    confirmed name and are spelled much like it. A word starting a sentence has its
    capital anyway, so it counts only once it was also written with one mid-sentence
    (in this line or earlier this session): "Thorn bushes everywhere" is never
    "Thorin". One word alone also needs the name to be in the scene and spelled closer
    (MIN_LIKENESS_ONE_WORD): real names and brands sound like campaign names too
    ("Mary" and Mara), while a split name ("Ka Zeth") is no real word."""
    lower = {w.casefold() for w in _lower_words(heard)}
    named = _named_words(heard)
    starts = _starts(heard, words)

    def unknown(i: int) -> bool:
        word = words[i].group()
        if known[i] or not word[:1].isupper():
            return False
        if len(word) > 1 and word.isupper():
            return False  # all capitals: more likely "NPC" or "AC" than a name
        stem = _stem(word)
        key = name_key(stem)
        if key in COMMON or key in GAME_TERMS or stem.casefold() in lower:
            return False
        if vocabulary is not None and vocabulary.is_word(stem):
            return False
        if i in starts:
            return stem.casefold() in named or (vocabulary is not None and vocabulary.is_name(stem))
        return True

    # Once per word: as the last word of a run (may end in "'s") and inside one.
    as_last = [unknown(i) for i in range(len(words))]
    inside = [ok and not words[i].group().endswith(_POSSESSIVE) for i, ok in enumerate(as_last)]
    fixes: list[Fix] = []
    questions: list[Question] = []
    i = 0
    while i < len(words):
        asking: tuple[Question, int] | None = None  # the longest run that raised one
        for size in range(min(MAX_JOINED, len(words) - i), 0, -1):
            run = range(i, i + size)
            if not as_last[run[-1]] or not all(inside[j] for j in run[:-1]):
                continue
            if any(heard[words[j].end() : words[j + 1].start()] != " " for j in run[:-1]):
                continue  # only words with a plain space between are one split name
            start, end = words[i].start(), words[run[-1]].end()
            said = _stem(heard[start:end])
            found = _sounds_like(lookup, said, start)
            if isinstance(found, Question):
                # The DM is asked (no scene needed), unless fewer words make a sure fix.
                asking = asking or (found, size)
                continue
            if found is not None and size == 1 and not _one_word_ok(found, scene):
                found = None
            if found is not None:
                fixes.append(found)
                i += size
                break
        else:
            if asking is not None:
                questions.append(asking[0])
                i += asking[1]
            else:
                i += 1
    return fixes, questions


def _stem(word: str) -> str:
    """Without an "'s" at the end: "Beleros's" is matched as "Beleros"."""
    for ending in _POSSESSIVE:
        if word.endswith(ending):
            return word[: -len(ending)]
    return word


def _sounds_like(lookup: CampaignLookup, said: str, start: int) -> Fix | Question | None:
    """A fix when the words sound like one confirmed name; a question when they sound
    like two or three (each spelled much alike, none secret, none only suggested)."""
    joined = said.replace(" ", "")
    if len(name_key(joined)) < MIN_LETTERS:
        return None
    by_entity: dict[str, dict[str, NameEntry]] = {}  # entity → its names, once each
    for code in sound_codes(joined):
        for entry in lookup.by_sound.get(code, ()):
            if entry.secret:
                return None  # it could be a secret name: never guess around one
            mine = by_entity.setdefault(entry.entity_id, {})
            if len(mine) < ENTRIES_PER_NAME:  # enough to judge; bounds the work
                mine.setdefault(entry.alias_id, entry)
            if len(by_entity) > MAX_OPTIONS:
                return None  # sounds like too many names to ask about
    if not by_entity:
        return None
    if len(by_entity) == 1:
        usable = [e for entries in by_entity.values() for e in entries.values() if e.confirmed]
        if not usable:
            return None  # only a name DMbot suggested: never a silent fix
        best = max(usable, key=lambda e: (likeness(said, e.text), e.text))
        if likeness(said, best.text) < MIN_LIKENESS or best.text == said:
            return None
        return Fix(start, start + len(said), said, best.text, best.entity_id, SOUND)
    if any(not any(e.confirmed for e in entries.values()) for entries in by_entity.values()):
        return None  # one of them is only a suggestion: too unsure to ask
    options = []
    for entity_id, entries in by_entity.items():
        name = own_name(lookup, entity_id)
        alike = max(likeness(said, e.text) for e in entries.values() if e.confirmed)
        if name is None or alike < MIN_LIKENESS:
            return None  # not clearly one of these: leave it, don't ask
        entity = lookup.entities.get(entity_id)
        if entity is not None and entity.type == PLAYER_CHARACTER and alike < MIN_LIKENESS_ONE_WORD:
            return None  # a real first name next to a character ("Mary" for Mara): don't ask
        options.append((alike, name, entity_id))
    if any(name == said for _, name, _ in options):
        return None
    options.sort(key=lambda o: (-o[0], o[1]))
    return Question(start, start + len(said), said, tuple((e, n) for _, n, e in options))


def _one_word_ok(fix: Fix, scene: Collection[str]) -> bool:
    """Is there enough to fix one word alone: its name in the scene, and spelled closer
    than a joined name must be?"""
    return fix.entity_id in scene and likeness(fix.heard, fix.written) >= MIN_LIKENESS_ONE_WORD


@dataclass(slots=True)
class _SecretChecks:
    """Each run's answer within one line (several fixes share runs), and how many runs
    may still be checked."""

    seen: dict[tuple[int, int], bool] = field(default_factory=dict)  # (start, words)
    left: int = field(default_factory=lambda: SECRET_CHECKS_PER_LINE)
    ran_out: bool = False  # some fix was dropped because the checks ran out


def _near_secret(
    lookup: CampaignLookup,
    words: list[re.Match[str]],
    begin: int,
    end: int,
    near: _SecretChecks,
) -> bool:
    """Do the fixed words, with the words around them, sound like a secret name? Then
    they may be one misheard ("Silas Vain" for "Silas Vane"), and no fix may go there.
    Only runs about as long as a secret name are tried (one word fewer up to two more,
    as speech-to-text joins and splits words): trying every length made a 32-word
    secret name cost over half a second per line. `near` remembers each run's answer
    and limits the work per line: when it runs out, the answer is yes (no fix), so a
    campaign with very many secret names can't stall voice."""
    if not lookup.secret_lengths:
        return False
    inside = [i for i, w in enumerate(words) if w.start() < end and begin < w.end()]
    if not inside:
        return False
    first, last = inside[0], inside[-1]
    width = last - first + 1
    # A secret name may be heard as one word fewer, or split into up to MAX_JOINED - 1
    # more ("Silasvane" as "Si Las Vain"); the fixed words themselves always count.
    sizes = {
        n
        for k in lookup.secret_lengths
        for n in range(max(1, k - 1), k + MAX_JOINED)
        if width <= n <= len(words)
    } | {width}
    for size in sorted(sizes):
        for start in range(max(0, last - size + 1), min(first, len(words) - size) + 1):
            if (start, size) not in near.seen:
                if near.left <= 0:
                    near.ran_out = True
                    return True
                near.left -= 1
                joined = "".join(w.group() for w in words[start : start + size])
                codes = sound_codes(joined)
                near.seen[start, size] = (
                    any(e.secret for code in codes for e in lookup.by_sound.get(code, ()))
                    if codes
                    # Not in Latin letters, so no sound codes: compare the spelling with
                    # secret names that have none either ("Сайлас Вейна", "Сайлас Вейн").
                    else any(likeness(joined, k) >= MIN_LIKENESS for k in lookup.codeless_secrets)
                )
            if near.seen[start, size]:
                return True
    return False
