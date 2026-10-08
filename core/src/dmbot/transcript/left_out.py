"""Lines left out as off-topic, which the DM can put back (docs/PLAN.md, "Off-topic
filter"; #677). Pure: no Discord, no database.

A game line wrongly labelled off-topic is left out of the cleaned transcript and the
names scan. So the DM screen keeps one "🙈 Left out as off-topic" message per session,
edited in place, with the newest runs (one person's off-topic lines from one check):
one line and one **Put it back** button each. Putting a run back makes its lines game
talk again: saved or waiting to be saved, in the live channel if still recent, and in
the names scan.

It's shown at every DM-screen level, quiet included: it's the DM's only chance to undo,
and one message edited in place never pings. Each run keeps its number for the whole
session, so a number never changes meaning while the DM aims at it. The runs live with
the running session, in memory; a speaker who stops being recorded has theirs taken
down; at the session's end the buttons go.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from dmbot.transcript.topics import Waiting

SHOWN = 10  # runs in the message (the newest); older ones can't be put back here
WORDS_MAX = 60  # the first words shown for a run
MESSAGE_MAX = 2000  # Discord's limit for one message
HEADER = "🙈 **Left out as off-topic** (tap Put it back if it was game talk)"
NOTHING = "_Nothing left out right now._"
PUT_BACK = "Put it back"
ONLY_DM = "Only this campaign's DM can put these lines back."
EXPIRED = (
    "That's closed (the session ended, DMbot restarted, or it's too old). Those lines "
    "stay left out of the cleaned transcript."
)
ALREADY = "Those lines are already back."
STOPPED = "That person stopped being recorded, so this is closed."
NOT_SAVED = "Couldn't put those lines back just now. Try again in a moment."


def done_text(*, in_channel: bool) -> str:
    """After a press. `in_channel`: the live transcript channel shows the words again
    (it can only be changed for about 30 s after a line is posted)."""
    text = "↩️ Put back: those lines are in the cleaned transcript again."
    if not in_channel:
        text += " The live transcript channel still shows them as skipped."
    return text


@dataclass(slots=True)
class Run:
    id: str  # short, for the button
    number: int  # its number for the whole session
    speaker: int
    lines: list[Waiting]  # in the order heard
    put_back: bool = False

    @property
    def started_ms(self) -> int:
        return self.lines[0].started_ms


def runs_of(lines: Iterable[Waiting], hidden: set[tuple[int, int]]) -> list[list[Waiting]]:
    """The `hidden` lines (speaker, start) of one check, split into runs: one person's
    lines in a row. `lines`: all of that check's lines, in order; any other line ends a
    run, theirs or someone else's."""
    runs: list[list[Waiting]] = []
    in_run = False
    for line in lines:
        if (line.speaker, line.started_ms) not in hidden:
            in_run = False
        elif in_run and runs[-1][-1].speaker == line.speaker:
            runs[-1].append(line)
        else:
            runs.append([line])
            in_run = True
    return runs


@dataclass(slots=True)
class LeftOut:
    """This session's runs left out as off-topic, oldest first."""

    runs: list[Run] = field(default_factory=list)
    count: int = 0  # numbers handed out this session

    def add(self, hidden: list[Waiting], checked: Iterable[Waiting] = ()) -> list[Run]:
        """The lines one check left out. `checked`: all of that check's lines (these
        included), so any other line said in between ends a run. The runs added."""
        keys = {(w.speaker, w.started_ms) for w in hidden}
        order = sorted(list(checked) or hidden, key=lambda w: w.started_ms)
        added = []
        for lines in runs_of(order, keys):
            self.count += 1
            added.append(Run(secrets.token_hex(4), self.count, lines[0].speaker, lines))
        self.runs.extend(added)
        del self.runs[:-SHOWN]
        return added

    def shown(self) -> list[Run]:
        return self.runs[-SHOWN:]

    def find(self, run_id: str) -> Run | None:
        return next((r for r in self.runs if r.id == run_id), None)

    def drop_speaker(self, speaker: int) -> bool:
        """They stopped being recorded: their runs go. True if any did."""
        before = len(self.runs)
        self.runs = [r for r in self.runs if r.speaker != speaker]
        return len(self.runs) != before


def _first_words(run: Run) -> str:
    text = " ".join(run.lines[0].text.split())
    if len(text) > WORDS_MAX:
        return text[: WORDS_MAX - 1].rstrip() + "…"
    return text + "…" if len(run.lines) > 1 else text


def message_text(
    runs: list[Run],
    names: dict[int, str],
    when: Callable[[int], str],
    escape: Callable[[str], str] = str,
) -> tuple[str, list[Run]]:
    """The DM screen's message, and the runs it shows. `names`: speaker → display name
    (already safe to show); `when`: a line's start → its time in the session
    ("0:12:04"); `escape` makes the words safe (Discord markdown). Too long for
    Discord: the oldest whole lines go, and with them their buttons."""
    if not runs:
        return f"{HEADER}\n{NOTHING}", []
    lines = []
    for run in runs:
        said = f"[{when(run.started_ms)}] {names.get(run.speaker, 'Someone')}: "
        said += escape(_first_words(run))
        lines.append(f"{run.number}. Put back: {said}" if run.put_back else f"{run.number}. {said}")
    kept = len(lines)
    while kept > 1 and len(HEADER) + sum(len(x) + 1 for x in lines[-kept:]) > MESSAGE_MAX:
        kept -= 1
    return "\n".join([HEADER, *lines[-kept:]])[:MESSAGE_MAX], runs[-kept:]
