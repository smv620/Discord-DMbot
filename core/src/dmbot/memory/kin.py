"""Family roles on a `kin_of` connection (niece, daughter, uncle…): which words, and how each reads
from each side (#2457). Pure: no Discord, no database.

A family connection with a role is stored once, in the role's direction: "Mira kin_of Aldric,
detail niece" means Mira is the niece of Aldric. It reads "is the niece of" on Mira's card and
"has a niece" on Aldric's: the same word both times, so a gender is never guessed for the other
side (Aldric may be an uncle or an aunt). With no role (an empty detail, or any other words) it
stays "is family of" both ways, as before.
"""

from __future__ import annotations

import re

# The words DMbot knows as a family role, kept as the speaker said them. Sorted longest first below
# so "grandmother" is never read as "mother".
ROLES = (
    "parent", "child", "sibling", "spouse", "grandparent", "grandchild",
    "mother", "father", "son", "daughter", "sister", "brother", "wife", "husband",
    "aunt", "uncle", "niece", "nephew", "cousin",
    "grandmother", "grandfather", "granddaughter", "grandson",
)  # fmt: skip

# Roles that are true of both names alike (Mira is Aldric's cousin and Aldric is Mira's): they read
# "is the cousin of" from either card, are never swapped on the website, and keep the id-sorted
# storage every two-way connection has.
MUTUAL = frozenset({"sibling", "spouse", "cousin"})

_ALT = "|".join(sorted(ROLES, key=len, reverse=True))
# "is the niece of", "niece of", "is a niece of", "the daughter to"
_FORWARD = re.compile(rf"^(?:(?:is|was)\s+)?(?:(?:the|a|an)\s+)?({_ALT})\s+(?:of|to)$")
# "has a niece", "has the niece": the connection read from the other name's side
_BACKWARD = re.compile(rf"^has\s+(?:(?:a|an|the)\s+)?({_ALT})$")


def role_of(detail: str) -> str | None:
    """The role a connection's detail is, or None when it is no family word of the list."""
    word = " ".join(detail.split()).casefold()
    return word if word in ROLES else None


def directional(detail: str) -> bool:
    """Is the detail a role that is true of one name only (niece, mother…)? Only then does the
    order of the two names matter (#2457)."""
    role = role_of(detail)
    return role is not None and role not in MUTUAL


def forward(role: str) -> str:
    """How the connection reads from its first name: "is the niece of"."""
    return f"is the {role} of"


def backward(role: str) -> str:
    """How it reads from the other name: "has a niece". The article fits every word of the list
    ("has an aunt", "has an uncle")."""
    article = "an" if role[0] in "aeiou" else "a"
    return f"has {article} {role}"


# The family words an AI says in its own way ("married to", "are siblings", "half-brother of",
# "son-in-law of", "older sister", "seems to be the cousin of"): a role word, its plural, or a
# marriage word, as whole words. Only for reading what an AI returned (the extractor's
# family-only mode, #2960); the roles DMbot stores are still `ROLES` and `parse`.
_LOOSE = re.compile(
    rf"(?<![a-z])(?:{_ALT}|siblings|twins?|twin|married|wed|widow(?:er)?|brothers|sisters"
    r"|cousins|parents|children|spouses)(?![a-z])"
)


def looks_like_family(words: str) -> bool:
    """Does an AI's relationship wording name a family tie in any of its own ways?"""
    return _LOOSE.search(" ".join(words.split()).casefold()) is not None


def parse(words: str) -> tuple[str, bool] | None:
    """(role, swapped) for family words someone typed, or None. `swapped`: the words read from the
    second name's side ("has a niece"), so a caller that can swap the two names must; one that
    cannot should treat the words as none."""
    clean = " ".join(words.split()).casefold()
    if m := _FORWARD.match(clean):
        return m.group(1), False
    if m := _BACKWARD.match(clean):
        return m.group(1), m.group(1) not in MUTUAL
    return None
