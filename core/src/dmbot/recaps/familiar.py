"""The name the table uses for each character (#2627), so the writer says "Tavin", not "Tavin
Ashgrove of the Greyfen", where the table only ever says "Tavin".

For each character with a name of more than one word, count how often each form of it is said in
the session's cleaned transcript and in the earlier recap: the full name, its parts, and a
confirmed nickname. The familiar name is the form said most. If none is said, it is the first part
of the name. Pure counting over names the recap may already use: a secret name is never an
input (the caller passes only allowed names), and a form that would name a secret goes by the
rules that already exist (`names_a_secret`): then the full name stays. This is an instruction to
the writer, never a check: a recap that uses a full name is not failed or rewritten.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping

from dmbot.memory.models import name_key
from dmbot.secrets_match import names_a_secret

SHORTEST_PART = 3  # "of", "the" and "de" are no one's name
LINKING = frozenset({"of", "the", "de", "von", "van", "da", "di", "le", "la", "al", "bin"})


def _count(form: str, haystack: str) -> int:
    key = name_key(form)
    return len(re.findall(rf"(?<!\w){re.escape(key)}(?!\w)", haystack)) if key else 0


def parts_of(name: str) -> list[str]:
    """The words of a name that could stand for it alone, in order."""
    return [
        w for w in name.split() if len(name_key(w)) >= SHORTEST_PART and name_key(w) not in LINKING
    ]


def familiar_names(
    names: Collection[str],
    texts: Iterable[str],
    *,
    aliases: Mapping[str, str] | None = None,
    secret_names: Collection[str] = (),
    allowed_names: Collection[str] = (),
) -> dict[str, str]:
    """{full name: familiar name} for the names that have one different from the full name.
    `texts`: what the table said and the earlier recap. `aliases`: another spelling -> the name
    (confirmed nicknames only). A single-word name has no other form and is left out."""
    haystack = " ".join(name_key(t) for t in texts)
    nicknames: dict[str, list[str]] = {}
    for spelling, full in (aliases or {}).items():
        nicknames.setdefault(full, []).append(spelling)
    out: dict[str, str] = {}
    for full in names:
        parts = parts_of(full)
        if len(full.split()) < 2 or not parts:
            continue
        forms = [full, *parts, *nicknames.get(full, [])]
        counts = {f: _count(f, haystack) for f in forms}
        # a part said inside the full name is the full name's, not the part's own
        inside = counts[full]
        for part in parts:
            counts[part] -= inside * _count(part, name_key(full))
        best = max(counts.values())
        if best == 0:
            familiar = parts[0]  # no form is said: the first part of the name
        else:
            # most said wins; a tie goes to the shorter form (the one a table would settle on)
            familiar = min((f for f, c in counts.items() if c == best), key=lambda f: (len(f), f))
        if name_key(familiar) == name_key(full):
            continue
        if secret_names and names_a_secret(familiar, secret_names, allowed_names):
            continue  # a part of a secret name: the rules that exist decide, the full name stays
        out[full] = familiar
    return out
