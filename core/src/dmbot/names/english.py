"""Whether a word is an ordinary English word (#5154): the SCOWL list in `data/` (its attribution
is beside it). Loaded once, on first use, and kept for the process: about 8 MB as a frozenset,
measured; under the 15 MB Supervisor set before a sorted tuple would be needed."""

from __future__ import annotations

import gzip
from functools import cache
from importlib import resources


@cache
def _words() -> frozenset[str]:
    raw = resources.files("dmbot.names").joinpath("data/english_words.txt.gz").read_bytes()
    return frozenset(gzip.decompress(raw).decode("ascii").split())


def is_english_word(word: str) -> bool:
    """`word`, in lower case, is in the list (letters only: "hope" yes, "hrothgar" no)."""
    return word.casefold() in _words()
