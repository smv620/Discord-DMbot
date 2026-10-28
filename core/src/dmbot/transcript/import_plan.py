"""What an imported transcript keeps, and why the rest is dropped (#2105). Pure: no database.

A DM maps each speaker label in a file to a server member, or drops it. Consent is enforced here,
in code, before anything is stored: only a mapped member who has agreed now (the caller passes
`ConsentStore.consenting`, which reads the current terms the way the live recording does) keeps
their lines. Everything else is dropped first: an unmapped or dropped speaker, a member who has not
agreed, and a bot. Nothing is returned but counts and the kept lines; a preview stores nothing.

Lines with no time get one estimated from their words at the speaking pace `session_hours` uses
(7,000 spoken words an hour), so the session's length and its game talk can be told like a live
session's. Craig's placeholders ("{non-game context}") were cut out by the parser and are counted
as left out.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from dmbot.transcript.importer import Transcript
from dmbot.transcript.models import Line

DROP = "drop"  # the mapping's value for a speaker whose lines are not kept
NOT_MATCHED, NOT_AGREED, BOT, LEFT_OUT = "not_matched", "not_agreed", "bot", "left_out"
REASONS = (NOT_MATCHED, NOT_AGREED, BOT, LEFT_OUT)
SPOKEN_WORDS_PER_HOUR = 7000  # a table's talk, to estimate the hours of a file with no times
WORD_MS = 3_600_000 // SPOKEN_WORDS_PER_HOUR  # one spoken word, in milliseconds
MAX_LINE_MS = 120_000  # a single line is never counted as longer than two minutes
MAX_LINE_CHARS = 2_000  # a longer line is stored as several, so one can't carry a whole file


def spoken_ms(text: str) -> int:
    """How long a line takes to say, from its words."""
    return min(max(len(text.split()), 1) * WORD_MS, MAX_LINE_MS)


@dataclass(frozen=True, slots=True)
class SpeakerCount:
    label: str  # exactly as in the file
    lines: int


def speakers(transcript: Transcript) -> list[SpeakerCount]:
    """The speaker labels in the order they first speak, with their line counts."""
    counts = Counter(u.speaker for u in transcript.utterances)
    return [SpeakerCount(label, counts[label]) for label in transcript.speakers]


@dataclass(slots=True)
class SpeakerReport:
    label: str
    kept: int = 0
    dropped: dict[str, int] = field(default_factory=lambda: dict.fromkeys(REASONS, 0))


@dataclass(frozen=True, slots=True)
class Plan:
    """The outcome for one file: the lines to keep (with the member's account as their speaker)
    and a count per speaker. `left_out` is the placeholder lines the parser cut out."""

    lines: tuple[Line, ...]
    speakers: tuple[SpeakerReport, ...]
    left_out: int

    @property
    def kept(self) -> int:
        return len(self.lines)


def timed(transcript: Transcript) -> list[tuple[int, int]]:
    """(start in ms from the start of the file, duration in ms) for each utterance. A file with
    times keeps them; one without gets the words' running time. A line's duration is its spoken
    length, never longer than the gap to the next line of the file."""
    out: list[tuple[int, int]] = []
    clock = 0
    uts = transcript.utterances
    for i, u in enumerate(uts):
        start = u.at_ms if transcript.timed else clock
        length = spoken_ms(u.text)
        if transcript.timed and i + 1 < len(uts) and uts[i + 1].at_ms > start:
            length = min(length, uts[i + 1].at_ms - start)
        out.append((start, length))
        clock = start + length
    return out


def _pieces(text: str) -> list[str]:
    """The words of one line, in pieces of at most `MAX_LINE_CHARS` cut between words (a single
    word longer than that is cut where it stands)."""
    out: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > MAX_LINE_CHARS:
            if current:
                out.append(current)
                current = ""
            out.append(word[:MAX_LINE_CHARS])
            word = word[MAX_LINE_CHARS:]
        if current and len(current) + 1 + len(word) > MAX_LINE_CHARS:
            out.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        out.append(current)
    return out or [text[:MAX_LINE_CHARS]]


def make_plan(
    transcript: Transcript,
    mapping: Mapping[str, int | str | None],
    *,
    agreed: Collection[int],
    bots: Collection[int] = (),
    members: Collection[int] | None = None,
    base_ms: int = 0,
) -> Plan:
    """Decide every line of the file. `mapping`: label -> member id, or `DROP`/None. `agreed`:
    the members who have agreed now. `bots`: member ids that are bots. `members`: the ids that
    may be mapped at all (the server's people); None skips that check. `base_ms`: when the
    session starts (Unix milliseconds); each kept line starts at that plus its time in the file."""
    reports = {s.label: SpeakerReport(s.label) for s in speakers(transcript)}
    kept: list[Line] = []
    for u, (start, length) in zip(transcript.utterances, timed(transcript), strict=True):
        report = reports[u.speaker]
        who = mapping.get(u.speaker)
        member = who if isinstance(who, int) and not isinstance(who, bool) else None
        if member is None or (members is not None and member not in members):
            report.dropped[NOT_MATCHED] += 1
        elif member in bots:
            report.dropped[BOT] += 1
        elif member not in agreed:
            report.dropped[NOT_AGREED] += 1
        else:
            report.kept += 1
            pieces = _pieces(u.text) if len(u.text) > MAX_LINE_CHARS else [u.text]
            share = max(length // len(pieces), 1)
            for n, piece in enumerate(pieces):
                kept.append(
                    Line(base_ms + start + n * share, member, piece, piece, duration_ms=share)
                )
    return Plan(tuple(kept), tuple(reports.values()), transcript.cut_out)
