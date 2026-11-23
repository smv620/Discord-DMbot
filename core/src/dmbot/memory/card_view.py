"""The three lines at the top of a character's card (#5050): who they are, where and when they were
first met, when they were last mentioned. One place builds the words, so Discord's name card and
the website's page (slice d) say the same thing. Made from what the background job kept
(`CardSummaries`), never from the transcript, so opening a card never waits.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from dmbot.memory import card_lines
from dmbot.memory.card_summaries import Mention, Summary


@dataclass(frozen=True, slots=True)
class CardView:
    """The finished lines, label and all, drawn as they are. `first` and `last` are None while the
    job has not worked out the mentions yet (the first line then says it is still writing)."""

    who: str
    first: str | None
    last: str | None

    def lines(self) -> list[str]:
        return [x for x in (self.who, self.first, self.last) if x]


def _line(label: str, mention: Mention | None, numbers: Mapping[int, int]) -> str | None:
    """None when the session is no longer in the campaign's list (it was deleted): better no line
    than a wrong number."""
    if mention is None or mention.session_at not in numbers:
        return None
    return card_lines.when_line(
        label, numbers[mention.session_at], mention.session_at, mention.scene
    )


def view(
    summary: Summary | None,
    name: str,
    numbers: Mapping[int, int],
    lore: tuple[str, str, int] | None = None,
) -> CardView:
    """The lines for a DM. `numbers`: each session's number by its start. `lore`: the label,
    heading and time of the upload they were first read from; it is the first line when it came
    before the table's first mention (or the table never mentioned them), else the session is
    (ruling on #5050: whichever came first). A character the job has not reached yet reads "still
    writing", never a guess."""
    if summary is None:
        return CardView(card_lines.who_line(None, name, pending=True), None, None)
    who = card_lines.who_line(
        summary.who, name, pending=summary.made_at is None, hidden=summary.hidden
    )
    if summary.scanned_at is None:
        return CardView(who, None, None)
    first = _line(card_lines.FIRST_MET, summary.first, numbers)
    if lore and (summary.first is None or lore[2] < summary.first.session_at):
        first = card_lines.lore_line(lore[0], lore[1])
    if summary.first is None and summary.last is None:
        return CardView(who, first, card_lines.NEVER_AT_THE_TABLE)
    return CardView(who, first, _line(card_lines.LAST_MENTIONED, summary.last, numbers))
