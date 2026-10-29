"""A recap written by someone at the table, kept as a session's recap (#2609).

Nothing here talks to the AI writer or decides anything: the DM adds the text, DMbot stores it
whole (up to `MAX_CHARS`, the writer's choice of length, #2597) with the name of who wrote it, and
only advises. Names in it that DMbot does not know go to the DM's name check as any recap's do;
a name or a link the DM keeps secret gives a note on the recap page, never a block; no fact goes
into story memory from it by itself. Pure helpers here; the store is `recaps.store`.
"""

from __future__ import annotations

import re
from collections.abc import Collection

from dmbot.recaps.fixed_lines import STORY_COLUMN_MAX
from dmbot.recaps.text import is_known_name, names_a_secret, proper_nouns

MAX_CHARS = STORY_COLUMN_MAX  # what the database column holds (migration 0225)
TITLE_MAX = 120
AUTHOR_MAX = 80
MAX_NAMES = 20  # names offered to the DM's name check from one recap

NOTE_SECRET = "This names something you keep secret: check before posting."
BAD_TEXT = "Add the recap's words, up to 50,000 characters, and who wrote it."
RIGHTS_ASK = "This was written by someone at your table, and you have their OK to use it."

_BLANKS = re.compile(r"\n{3,}")
_SPACES = re.compile(r"[ \t]+\n")


def clean_text(raw: object) -> str | None:
    """The recap's words as stored, or None when there are none or too many. Line breaks are made
    plain, trailing spaces and runs of blank lines trimmed; nothing else is changed."""
    if not isinstance(raw, str):
        return None
    text = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = _BLANKS.sub("\n\n", _SPACES.sub("\n", text)).strip()
    return text if 0 < len(text) <= MAX_CHARS else None


def clean_author(raw: object) -> str | None:
    """Who wrote it, one line of at most `AUTHOR_MAX` characters, or None."""
    if not isinstance(raw, str):
        return None
    name = " ".join(raw.split())
    return name if 0 < len(name) <= AUTHOR_MAX else None


def title_for(text: str, author: str) -> str:
    """A title for the recap page: the text's first line when it is short enough to be one, else
    "Recap by <who wrote it>"."""
    first = next((line.strip(" #*_\t") for line in text.splitlines() if line.strip()), "")
    if 3 <= len(first) <= 80 and not first.endswith((".", ",", ";")):
        return first
    return f"Recap by {author}"[:TITLE_MAX]


def unknown_names(
    text: str, allowed_names: Collection[str], secret_names: Collection[str]
) -> list[str]:
    """Capitalised names in the text that no list knows, for the DM's name check: never a secret
    name, never one the campaign already has, each once, at most `MAX_NAMES`. The DM decides which
    are names; DMbot only asks."""
    seen: set[str] = set()
    out: list[str] = []
    for noun in proper_nouns(text, sentence_starts=False):
        key = noun.casefold()
        if key in seen:
            continue
        seen.add(key)
        # (a word of a secret name is one too: "Vane" of "Silas Vane" is never offered)
        if (
            is_known_name(noun, allowed_names)
            or is_known_name(noun, secret_names)
            or names_a_secret(noun, secret_names, allowed_names)
        ):
            continue
        out.append(noun)
        if len(out) >= MAX_NAMES:
            break
    return out


def secret_note(text: str, allowed_names: Collection[str], secret_names: Collection[str]) -> str:
    """The note for the DM when the text names something kept secret, else "". Advice only."""
    return NOTE_SECRET if names_a_secret(text, secret_names, allowed_names) else ""
