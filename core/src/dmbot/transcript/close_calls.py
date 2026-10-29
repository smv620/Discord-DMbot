"""Counting the Transcript Cleaner's close calls (#1577; docs/PLAN.md, "Transcript Cleaner",
Re-listen audio): how often a line lands within `BAND` of a likeness threshold, on either side,
so the re-listen decision rests on data. Pure: no Discord, no database.

A close call is a candidate name whose spelling-likeness to the heard words is within 0.05 of
`MIN_LIKENESS` or `MIN_LIKENESS_ONE_WORD`: just above is **fixed-near** (the Cleaner made the
fix), just below is **left-near** (it left the words as heard). Counted per session, in memory,
**counts only**: no text, no names and no speaker ids ever reach the counter in the bot. The
offline devtool (`dmbot.devtools.cleaner_close_calls`) alone keeps the ten nearest misses as
words, for dev1 to read on the owner's own recordings; the repo and the logs never hold them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BAND = 0.05  # how near a threshold counts as a close call
NEAREST_KEPT = 10  # misses the devtool lists


@dataclass(frozen=True, slots=True)
class Miss:
    """One close call with its words, kept only when `keep_words` is on (the devtool)."""

    heard: str
    candidate: str
    score: float
    threshold: float


@dataclass(slots=True)
class CloseCalls:
    """One session's close calls. `keep_words`: also remember the nearest misses as words (only
    the offline devtool turns this on)."""

    keep_words: bool = False
    lines: int = 0
    fixed_near: int = 0
    left_near: int = 0
    one_word: int = 0  # of those, how many were against the one-word threshold
    people_fixed_near: int = 0  # a table person's name, counted apart from a story name
    people_left_near: int = 0
    nearest: list[Miss] = field(default_factory=list)
    _seen: set[tuple[int, float]] = field(default_factory=set)

    def start_line(self) -> None:
        """A new line is being cleaned: the same place weighed again (the Cleaner tries a name as
        one, two or three words) counts once."""
        self.lines += 1
        self._seen.clear()

    def note(
        self,
        score: float,
        threshold: float,
        *,
        one_word: bool,
        person: bool,
        at: int = 0,
        heard: str = "",
        candidate: str = "",
    ) -> None:
        """Weigh one candidate found at offset `at` of the line: count it if its `score` is within
        `BAND` of `threshold`. `heard` and `candidate` are dropped at once unless `keep_words` is on
        (only the offline devtool): the bot's counter never holds text."""
        if abs(score - threshold) > BAND + 1e-9:
            return
        key = (at, threshold)
        if key in self._seen:
            return
        self._seen.add(key)
        fixed = score >= threshold
        if fixed:
            self.fixed_near += 1
            self.people_fixed_near += person
        else:
            self.left_near += 1
            self.people_left_near += person
        if one_word:
            self.one_word += 1
        if self.keep_words and heard and not fixed:
            self.nearest.append(Miss(heard, candidate, score, threshold))
            self.nearest.sort(key=lambda m: (m.threshold - m.score, m.heard))
            del self.nearest[NEAREST_KEPT:]

    def report(self) -> str:
        """The one line for the log: counts only."""
        people = ""
        if self.people_fixed_near or self.people_left_near:
            people = (
                f" (people: fixed-near {self.people_fixed_near}, left-near {self.people_left_near})"
            )
        return (
            f"Cleaner close calls: fixed-near {self.fixed_near}, left-near {self.left_near},"
            f" one-word {self.one_word} of {self.lines} lines{people}"
        )
