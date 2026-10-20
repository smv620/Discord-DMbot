"""Finding a secret name in text DMbot writes or sends (#2087): one matcher, used everywhere.

A secret name is matched **as a whole name only** (owner, 2026-10-20). Any word of three letters
or more used to block a text, so a secret like "The White Death" blocked every recap that said
"death". Now the whole name must be there, with only these differences:

- case, accents, apostrophes and hyphens (`name_key`); a hyphen is read both ways, folded away
  ("Dun-Varra" is "DunVarra") and as a space ("Vex-gate" holds "Vex");
- a leading article (a, an, the) added, dropped or swapped;
- on any word, a possessive or plural (`'s`, `s'`, `s`, `es`) or a verb ending (`-ed`, `-d`,
  `-ing`);
- a title in its short or long form, with or without the full stop (Mr/Mister ... Prof/Professor);
- the whole name run together at word edges ("WhiteDeath").

A match inside a longer allowed name does not count ("Arcane Brotherhood" inside the confirmed
"Arcane Brotherhood Hall" is the hall), and a one-word secret under three letters is not looked
for. The rule's name is a fixed word so a test tool can say which rule fired without saying what
was matched; the matched text is never returned.

The recap text check and the narration's second lock both call this, so they cannot disagree.
Pure functions; nothing here touches Discord or the database.
"""

from __future__ import annotations

from collections.abc import Collection

from dmbot.memory.models import name_key

WHOLE_NAME = "whole-name"  # which rule found a secret name (never the name itself)
RUN_TOGETHER = "run-together"
MIN_ONE_WORD = 3  # a one-word secret shorter than this is not looked for
ARTICLES = frozenset({"a", "an", "the"})
# After `name_key` an apostrophe is gone, so "'s" and "s'" are both just "s".
ENDINGS = ("", "s", "es", "ed", "d", "ing")
# Each group is one title; the first is the form every member is read as.
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
TITLE = {form: group[0] for group in _TITLES for form in group}


def _tokens(text: str) -> list[str]:
    return name_key(text).split()


def _variants(text: str) -> list[list[str]]:
    """The words of a text, a hyphen folded away and again as a space, so "Dun-Varra" is both
    "dunvarra" and "dun varra"."""
    folded = _tokens(text)
    spaced = _tokens(text.replace("-", " "))
    return [folded] if folded == spaced else [folded, spaced]


def _names(text: str) -> list[list[str]]:
    """A name's words as compared, each way a hyphen can be read, with a leading article dropped
    (a name that is only an article keeps it)."""
    return [
        tokens[1:] if len(tokens) > 1 and tokens[0] in ARTICLES else tokens
        for tokens in _variants(text)
    ]


def _same(seen: str, name: str) -> bool:
    """Whether a word of the text is this word of the name, with an ending or as the same title."""
    if seen in TITLE or name in TITLE:
        return TITLE.get(seen, seen) == TITLE.get(name, name)
    return any(seen == name + ending for ending in ENDINGS)


def _spans(seen: list[str], name: list[str]) -> list[tuple[int, int]]:
    """Where the name's words stand, one after the other, in the text's words."""
    n = len(name)
    return [
        (i, i + n)
        for i in range(len(seen) - n + 1)
        if all(_same(seen[i + j], name[j]) for j in range(n))
    ]


def _run_together(seen: list[str], name: list[str]) -> bool:
    """The whole name as one word ("WhiteDeath"), alone at word edges."""
    joined = "".join(name)
    return len(name) > 1 and any(w == joined + ending for w in seen for ending in ENDINGS)


def _glued(seen: list[str], keys: Collection[str]) -> bool:
    """A word that is nothing but whole secret names written one after the other ("RaeAegis")."""
    for word in seen:
        reach = [True] + [False] * len(word)
        for end in range(1, len(word) + 1):
            reach[end] = any(
                reach[end - len(k)] and word.endswith(k, 0, end) for k in keys if len(k) <= end
            )
        if reach[-1] and word not in keys:  # (a word that is a key alone is a plain match)
            return True
    return False


def secret_rule(text: str, secret_names: Collection[str], allowed_names: Collection[str]) -> str:
    """Which rule finds a secret name in the text, or "" for none: `whole-name` (the name, with
    the differences above) or `run-together` (the name or several names glued into one word). A
    name no word can be read from, in symbols, is looked for as written."""
    texts = _variants(text)
    folded = text.casefold()
    allowed = [a for n in allowed_names for a in _names(n) if a]
    keys: set[str] = set()
    for secret in secret_names:
        names = [n for n in _names(secret) if n]
        if not names:  # nothing a word can be read from: look for it as written
            if secret.strip() and secret.strip().casefold() in folded:
                return WHOLE_NAME
            continue
        usable = [n for n in names if not (len(n) == 1 and len(n[0]) < MIN_ONE_WORD)]
        for name in usable:
            for seen in texts:
                # A match standing inside a longer allowed name is that name, not the secret.
                contained = [
                    span for a in allowed if len(a) > len(name) for span in _spans(seen, a)
                ]
                for start, end in _spans(seen, name):
                    if not any(a <= start and end <= b for a, b in contained):
                        return WHOLE_NAME
        for name in usable:
            if any(_run_together(seen, name) for seen in texts):
                return RUN_TOGETHER
            keys.add("".join(name))
    keys = {k for k in keys if len(k) >= MIN_ONE_WORD}
    return RUN_TOGETHER if keys and any(_glued(seen, keys) for seen in texts) else ""


def names_a_secret(
    text: str, secret_names: Collection[str], allowed_names: Collection[str]
) -> bool:
    """Whether the text names anything the DM keeps secret (`secret_rule`)."""
    return bool(secret_rule(text, secret_names, allowed_names))
