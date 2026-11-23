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
    # The two key names only the session holds (the lore doesn't know them): the ones the grouping
    # must join, since the others above are names DMbot already has, heard wrong
    "Dwarven Valley": ("Dwarven Valley", "Dwarvin Valley", "Dwarven Vally"),
    "Clan Battlehammer": ("Clan Battlehammer", "Clan Battle hammer", "Clan Batlehammer"),
}


# What starts a transcript line before the words said: a time, the speaker and a character tag
# (`[0:00:02] (Mia) {Jebidiah}:`, `Jebidiah:`). Never respelled: one speaker or character must stay
# one, and every label must still match its --players entry.
_PREFIX = re.compile(r"^(?:\[[^\]\n]*\]\s*)?[^:\n]{1,80}?:\s")


def respell(text: str, table: Mapping[str, tuple[str, ...]] = RESPELLINGS) -> str:
    """`text` with each name in `table`, where it is said, written by its spellings in turn: the
    first time as the first spelling, the second as the second, and round again. Only the words
    said are touched, never a line's speaker or character tag, and whole words only, as written
    ("Bryn Shandering" is left alone), so every other word of the session stays exactly as it was.
    (`sample_run` checks the respelled file reads with the same speakers and lines as the typed.)"""
    turns = {name: 0 for name in table}
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        found = _PREFIX.match(line)
        head, said = (line[: found.end()], line[found.end() :]) if found else ("", line)
        for name, spellings in table.items():
            if not spellings:
                continue

            def turn(
                _m: re.Match[str], name: str = name, spellings: tuple[str, ...] = spellings
            ) -> str:
                out_ = spellings[turns[name] % len(spellings)]
                turns[name] += 1
                return out_

            said = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", turn, said)
        out.append(head + said)
    return "".join(out)
