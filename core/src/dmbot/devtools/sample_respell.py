"""The respelled sample session (dev only; #5133, Supervisor's ruling with the Expert's check).

The typed and no-capitals versions of the sample session write each name one way, so they can't
show one name heard several ways. Until the spoken recording exists (design slice 5), this fourth
version takes the typed session and writes a few key names the way speech-to-text might hear them,
from a fixed table: two or three spellings each, one of them split in two. A name's spellings take
turns through the session, so each is heard and the run is the same every time. The grouping of
slice 3 is judged on it: a name heard under several spellings is grouped as one.

Names from the public sample only. Pure: no database, no Discord, no AI.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# Each key name as the typed session writes it, then the spellings it is heard as, in turn. The
# first is the name itself, so the right spelling is still heard; one name is split in two words.
# Spellings speech-to-text might give, not ones picked to pass the grouping: a one-word name needs
# letters as alike as the Cleaner's (0.9), so "Valerith" and "Jebediah" won't fold with their names
# today, and the series shows that as plainly as it shows what does fold.
RESPELLINGS: Mapping[str, tuple[str, ...]] = {
    "Vaelrith": ("Vaelrith", "Vale rith", "Valerith"),  # (split in two; and one not folded today)
    "Bryn Shander": ("Bryn Shander", "Brin Shandar", "Bryn Shandar"),
    "Termalaine": ("Termalaine", "Termalane"),
    "Jebidiah": ("Jebidiah", "Jebediah"),  # (not folded today: one word, 0.875 alike)
}


def respell(text: str, table: Mapping[str, tuple[str, ...]] = RESPELLINGS) -> str:
    """`text` with each name in `table` written by its spellings in turn: the first time as the
    first spelling, the second as the second, and round again. Whole words only, as written ("Bryn
    Shandering" is left alone), so every other word of the session stays exactly as it was."""
    for name, spellings in table.items():
        if not spellings:
            continue
        seen = 0

        def turn(_found: re.Match[str], spellings: tuple[str, ...] = spellings) -> str:
            nonlocal seen
            out = spellings[seen % len(spellings)]
            seen += 1
            return out

        text = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", turn, text)
    return text
