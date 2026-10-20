"""Finding a secret name in text DMbot writes or sends (#2087): one matcher, used everywhere.

A secret name is matched **as a whole name only** (owner, 2026-10-20). Any word of three letters
or more used to block a text, so a secret like "The White Death" blocked every recap that said
"death". Now the whole name must be there, with only these differences:

- case, accents, apostrophes and hyphens (`name_key`); a hyphen is read both ways, folded away
  ("Dun-Varra" is "DunVarra") and as a space ("Vex-gate" holds "Vex");
- a leading article (a, an, the) added, dropped or swapped;
- on any word, a possessive or plural (`'s`, `s'`, `s`); `es` on a word ending s, x, z, ch or sh
  or of four letters or more; `-ed` and `-ing` on a word of four letters or more; `-d` only after
  a final "e" ("Bran" is not "brand"; "Vexes", "Ghosts" and "Ghosted"; "Vex" is not "vexing");
- a title in its short or long form, with or without the full stop (Mr/Mister ... Prof/Professor);
  a title that ends a name takes the same endings ("the Lost General's");
- the whole name run together at word edges ("WhiteDeath", "TheWhiteDeath", "MisterWhite"), and
  several whole secret names glued into one word ("RaeAegis").

A match inside a longer allowed name does not count ("Arcane Brotherhood" inside the confirmed
"Arcane Brotherhood Hall" is the hall). A one-word secret under three letters is not looked for
bare; written with its article ("The Ox") it is looked for only with an article. The rule's name is
a fixed word so a test tool can say which rule fired without saying what was matched; the matched
text is never returned.

The recap text check, the narration's second lock, the transcript cleaner's checks and the Post
check all call this, so they cannot disagree. It is cheap enough to run on every draft: a text is
indexed by word once, a name is found from the places its first word stands, and an allowed name
is looked at only once a secret has been found. Pure functions; nothing here touches Discord or
the database.
"""

from __future__ import annotations

import bisect
import functools
import itertools
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from dmbot.memory.models import name_key

WHOLE_NAME = "whole-name"  # which rule found a secret name (never the name itself)
RUN_TOGETHER = "run-together"
MIN_ONE_WORD = 3  # a one-word secret shorter than this is not looked for bare
LONG_WORD = 4  # -ed and -ing (and -es on any word) only on a word at least this long
ARTICLES = ("a", "an", "the")
# After `name_key` an apostrophe is gone, so "'s" and "s'" are both just "s".
ES_LETTERS = ("s", "x", "z", "ch", "sh")  # a word ending so takes -es at any length ("Vexes")
MAX_FORMS = 64  # run-together spellings tried for one name (titles and articles multiply them)
# Each group is one title; any member stands for the others.
_TITLES = (
    ("mr", "mister"),
    ("mrs", "missus"),
    ("ms",),
    ("dr", "doctor"),
    ("st", "saint"),
    ("mt", "mount"),
    ("capt", "captain"),
    ("lt", "lieutenant"),
    ("sgt", "sergeant"),
    ("gen", "general"),
    ("prof", "professor"),
)
GROUP = {form: group for group in _TITLES for form in group}


def _tokens(text: str) -> list[str]:
    return name_key(text).split()


def _variants(text: str) -> list[list[str]]:
    """The words of a text, a hyphen folded away and again as a space, so "Dun-Varra" is both
    "dunvarra" and "dun varra"."""
    folded = _tokens(text)
    spaced = _tokens(text.replace("-", " "))
    return [folded] if folded == spaced else [folded, spaced]


@functools.lru_cache(maxsize=4096)  # the same words come up in every name and every text
def _endings(word: str) -> tuple[str, ...]:
    long = len(word) >= LONG_WORD
    out = ["", "s"]
    if long or word.endswith(ES_LETTERS):
        out.append("es")
    if long:
        out += ["ed", "ing"]
    if word.endswith("e"):  # "Ghoule" is "Ghouled"; "Bran" is never "Brand"
        out.append("d")
    return tuple(out)


def _forms(word: str, *, last: bool, titled: bool = True) -> frozenset[str]:
    """Every way a word of a name may be written in a text. A title takes the other form of itself
    and, only when it ends the name, the endings. A name that is only a title word ("Gen") is a
    name, not a title: it is not swapped for "General"."""
    if word in GROUP and titled:
        return frozenset(f + e for f in GROUP[word] for e in (_endings(f) if last else ("",)))
    return frozenset(word + e for e in _endings(word))


@dataclass(frozen=True, slots=True)
class _Name:
    words: tuple[str, ...]  # as compared: the article dropped (kept for a short name)
    forms: tuple[frozenset[str], ...]  # how each word may be written in the text


def _name(tokens: list[str]) -> _Name | None:
    """A name made ready for matching, or None when it is not looked for. A leading article is
    dropped; a name left with one word of under three letters is looked for only with an article
    before it ("The Ox"), never as the bare short word."""
    article = len(tokens) > 1 and tokens[0] in ARTICLES
    words = tokens[1:] if article else tokens
    if not words:
        return None
    forms = [
        _forms(w, last=i == len(words) - 1, titled=len(words) > 1) for i, w in enumerate(words)
    ]
    if len(words) == 1 and len(words[0]) < MIN_ONE_WORD:
        if not article:
            return None
        return _Name(("the", *words), (frozenset(ARTICLES), *forms))
    return _Name(tuple(words), tuple(forms))


class _Text:
    """One reading of the text: its words, and where each stands."""

    def __init__(self, seen: list[str]) -> None:
        self.seen = seen
        self._spans: dict[_Name, list[tuple[int, int]]] = {}  # per name, once per reading
        # Keyed by the name itself, never id(): a freed name's id is reused by the next one, which
        # would hand back another secret's spans and miss it.
        self._starts: dict[_Name, list[int]] = {}
        self.index: dict[str, list[int]] = {}
        for i, word in enumerate(seen):
            self.index.setdefault(word, []).append(i)

    def spans(self, name: _Name) -> list[tuple[int, int]]:
        """Where the name's words stand, one after the other. Found from the places its first
        word may stand, so a name that is not in the text costs a few lookups. Kept for the next
        ask: an allowed name is asked about for every secret hit, and a text with many hits would
        otherwise look for the same name again each time (#2100)."""
        found = self._spans.get(name)
        if found is None:
            found = self._spans[name] = self._find(name)
        return found

    def inside(self, outer: _Name, start: int, end: int) -> bool:
        """Whether the words [start, end) stand inside one occurrence of `outer`. Its occurrences
        all have the same length, so they are searched by where they start (a bisect), not
        one by one: a text with thousands of hits stays quick."""
        n = len(outer.words)
        starts = self._starts.get(outer)
        if starts is None:  # sorted: the spans come from sorted starts
            starts = self._starts[outer] = [a for a, _ in self.spans(outer)]
        i = bisect.bisect_left(starts, end - n)
        return i < len(starts) and starts[i] <= start

    def _find(self, name: _Name) -> list[tuple[int, int]]:
        n = len(name.words)
        starts = sorted(i for form in name.forms[0] for i in self.index.get(form, ()))
        return [
            (i, i + n)
            for i in starts
            if i + n <= len(self.seen)
            and all(self.seen[i + j] in name.forms[j] for j in range(1, n))
        ]


def _joined(words: Sequence[str], article: str) -> set[str]:
    """The name written as one word, in every spelling of its titles, with and without an article,
    the endings only on the last word."""
    choices = [GROUP.get(w, (w,)) for w in words]
    count = 1
    for c in choices:
        count *= len(c)
    if count > MAX_FORMS:  # a name with many titles: the spelling as written only
        choices = [(w,) for w in words]
    out: set[str] = set()
    for combo in itertools.product(*choices):
        base = "".join(combo)
        for lead in ("", article):
            for e in _endings(combo[-1]):
                out.add(lead + base + e)
    return out


def _glued(seen: list[str], keys: Collection[str]) -> bool:
    """A word that is nothing but whole secret names written one after the other ("RaeAegis")."""
    shortest = min(map(len, keys))
    for word in seen:
        if len(word) < 2 * shortest or word in keys:
            continue
        reach = [True] + [False] * len(word)
        for end in range(1, len(word) + 1):
            reach[end] = any(
                reach[end - len(k)] and word.endswith(k, 0, end) for k in keys if len(k) <= end
            )
        if reach[-1]:
            return True
    return False


def secret_rule(text: str, secret_names: Collection[str], allowed_names: Collection[str]) -> str:
    """Which rule finds a secret name in the text, or "" for none: `whole-name` (the name, with
    the differences above) or `run-together` (the name or several names glued into one word). A
    name no word can be read from, in symbols, is looked for as written."""
    texts = [_Text(seen) for seen in _variants(text)]
    folded = text.casefold()
    allowed_cache: list[_Name] | None = None
    run_together: set[str] = set()
    glue: set[str] = set()
    for secret in secret_names:
        readings = [_tokens(secret), _tokens(secret.replace("-", " "))]
        if not readings[0]:  # nothing a word can be read from: look for it as written
            if secret.strip() and secret.strip().casefold() in folded:
                return WHOLE_NAME
            continue
        seen_names: set[tuple[str, ...]] = set()
        for tokens in readings:
            name = _name(tokens)
            if name is None or name.words in seen_names:
                continue
            seen_names.add(name.words)
            for reading in texts:
                for start, end in reading.spans(name):
                    # A match standing inside a longer allowed name is that name, not the secret.
                    if allowed_cache is None:
                        allowed_cache = [
                            a for n in allowed_names for t in _variants(n) if (a := _name(t))
                        ]
                    if not any(
                        len(a.words) > len(name.words) and reading.inside(a, start, end)
                        for a in allowed_cache
                    ):
                        return WHOLE_NAME
            words = tokens[1:] if len(tokens) > 1 and tokens[0] in ARTICLES else tokens
            if (
                len(tokens) > 1
                and tokens[0] in ARTICLES
                and len(words) == 1
                and len(words[0]) < MIN_ONE_WORD
            ):
                # A short name that is only looked for with its article ("The Ox") run together:
                # "theox", "anox" and their endings, never the bare "ox". Not "a" + word: it
                # would match ordinary words ("a" + "go" is "ago").
                run_together |= {
                    art + words[0] + e for art in ("the", "an") for e in _endings(words[0])
                }
            if len(words) > 1:
                run_together |= _joined(words, article="the") | _joined(words, article="a")
                run_together |= _joined(words, article="an")
            if len(words) >= 1 and sum(map(len, words)) >= MIN_ONE_WORD:
                glue |= {
                    "".join(c) for c in itertools.product(*(GROUP.get(w, (w,)) for w in words))
                }
    if run_together and any(w in run_together for reading in texts for w in reading.seen):
        return RUN_TOGETHER
    if glue and any(_glued(reading.seen, glue) for reading in texts):
        return RUN_TOGETHER
    return ""


def names_a_secret(
    text: str, secret_names: Collection[str], allowed_names: Collection[str]
) -> bool:
    """Whether the text names anything the DM keeps secret (`secret_rule`)."""
    return bool(secret_rule(text, secret_names, allowed_names))
