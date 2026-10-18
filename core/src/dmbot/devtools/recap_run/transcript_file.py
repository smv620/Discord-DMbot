"""A transcript file read for a recap test run (#1969). Pure: no database, no network, no AI.

The owner can test a recap on a game that was played before DMbot was listening, or recorded with
another tool. This module turns such a file into the same `Line`s the stored sessions give the
recap worker, and reads the optional names file (`--names`). Four shapes are accepted and told
apart by their content, never by the file's name; anything else is refused with a line that names
them (`FORMATS`):

* DMbot's own download from the website (raw or cleaned): `[0:42:10] (Mia) {Cerric}: words`;
* plain lines, `Name: words`, with an optional `[hh:mm:ss]` first;
* WebVTT and SRT whose cue text starts `Name:`.

Who is who: every speaker gets a made-up number in the order they first speak, and the name given
with `--dm` is the DM's. Nothing here prints a line of the file; errors name the format or the
speakers, never the words. The file is not a customer's data and is never stored: the caller
keeps it in memory for the run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dmbot.recaps.audio.names import SpokenName
from dmbot.recaps.audio.script import EMOTION, PERFORMANCE_KIND, Script
from dmbot.recaps.text import CUE, PERFORMANCE
from dmbot.transcript.models import Line

DMBOT, PLAIN, VTT, SRT = "dmbot", "plain", "vtt", "srt"
FORMATS = (
    "DMbot's own transcript download, plain 'Name: words' lines (an optional [hh:mm:ss] first), "
    "or WebVTT or SRT whose cue text starts 'Name:'"
)
SPEAKER_BASE = 100  # made-up speaker numbers, so none can be mistaken for a real account
LINE_MS = 1000  # a file with no times: one second a line keeps the order
# Braced tags in DMbot's download that say what a line is, not who a character is.
TAGS = frozenset({"narrating", "table_talk", "non-game_content"})

_CLOCK = r"(?:(\d+):)?(\d{1,2}):(\d{2})"
_DMBOT_HEAD = re.compile(r"^DMbot transcript:")
_DMBOT_LINE = re.compile(
    r"^\[(?P<at>\d+:\d{2}:\d{2})\] \((?P<who>[^()]+)\)"
    r"(?: \{(?P<char>[^{}]+)\})?(?P<typed> \(typed\))?: (?P<text>.+)$"
)
_DMBOT_OTHER = re.compile(r"^\[\d+:\d{2}:\d{2}\] \(")  # sidebar, skipped-note and DMbot lines
_PLAIN = re.compile(
    rf"^(?:\[(?P<at>{_CLOCK})\]\s*)?(?P<who>[^\s:\[\]{{}}()<>][^:\[\]{{}}()<>]{{0,59}}):\s+(?P<text>\S.*)$"
)
_TIMING = re.compile(r"^(?P<h>\d+:)?(?P<m>\d{1,2}):(?P<s>\d{2})[.,](?P<ms>\d{3})\s+-->\s+")
_CUE_SPEAKER = re.compile(r"^(?P<who>[^:\[\]{}()<>]{1,60}):\s*(?P<text>\S.*)$", re.S)


class TranscriptError(ValueError):
    """The file can't be used; the message is plain words, never a line of the file."""


@dataclass(frozen=True, slots=True)
class Utterance:
    speaker: str
    at_ms: int
    text: str


@dataclass(frozen=True, slots=True)
class Transcript:
    format: str
    utterances: tuple[Utterance, ...]
    characters: tuple[str, ...] = ()  # named characters found in DMbot's download

    @property
    def speakers(self) -> list[str]:
        """The speakers, in the order they first speak."""
        return list(dict.fromkeys(u.speaker for u in self.utterances))


def _ms(hours: str | None, minutes: str, seconds: str, millis: str = "0") -> int:
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)


def _clock_ms(clock: str) -> int:
    parts = clock.split(":")
    if len(parts) == 3:
        return _ms(parts[0], parts[1], parts[2])
    return _ms(None, parts[0], parts[1])


def detect(text: str) -> str:
    """Which format the text is, by its content. Raises `TranscriptError` naming the formats."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise TranscriptError("the file is empty")
    if _DMBOT_HEAD.match(lines[0]):
        return DMBOT
    if lines[0].lstrip("\ufeff").startswith("WEBVTT"):
        return VTT
    if any(_TIMING.match(line) for line in lines[:6]) and lines[0].isdigit():
        return SRT
    plain = sum(1 for line in lines if _PLAIN.match(line))
    if plain and plain * 10 >= len(lines) * 8:  # nearly every line has a name and words
        return PLAIN
    raise TranscriptError(f"I don't know this file's format. I can read: {FORMATS}.")


def _dmbot(text: str) -> Transcript:
    out: list[Utterance] = []
    characters: dict[str, None] = {}
    for raw in text.splitlines():
        line = raw.strip()
        found = _DMBOT_LINE.match(line)
        if found is None:
            continue  # the header, a skipped-chat note, a blank line
        who = found["who"].strip()
        if who == "DMbot" or "[DM Sidebar" in line[:60] or "[Table Q" in line[:60]:
            continue  # DMbot's own words and the DM's questions to it are not the table's
        char = (found["char"] or "").strip()
        if char and char.lower() not in TAGS:
            characters[char] = None
        out.append(Utterance(who, _clock_ms(found["at"]), found["text"].strip()))
    if not out:
        raise TranscriptError("the DMbot download has no spoken lines in it")
    return Transcript(DMBOT, tuple(out), tuple(characters))


def _plain(text: str) -> Transcript:
    out: list[Utterance] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        found = _PLAIN.match(line)
        if found is None:
            raise TranscriptError("a line has no 'Name: words' form (the first is not named here)")
        # A line with no time follows the one before it, so a mixed file keeps its order.
        after = out[-1].at_ms + LINE_MS if out else 0
        at = _clock_ms(found["at"]) if found["at"] else after
        out.append(Utterance(found["who"].strip(), at, found["text"].strip()))
    return Transcript(PLAIN, tuple(out))


def _cues(text: str, kind: str) -> Transcript:
    """WebVTT and SRT: a timing line, then the cue's text, which starts `Name:`."""
    out: list[Utterance] = []
    block: list[str] = []
    start = 0
    count = 0

    def close() -> None:
        nonlocal block
        if not block:
            return
        said = _CUE_SPEAKER.match(" ".join(block))
        if said is None:
            raise TranscriptError(f"cue {count} has no 'Name:' at the start of its text")
        out.append(Utterance(said["who"].strip(), start, said["text"].strip()))
        block = []

    for raw in text.lstrip("\ufeff").splitlines():
        line = raw.strip()
        timing = _TIMING.match(line)
        if timing is not None:
            close()
            count += 1
            start = _ms(
                (timing["h"] or "").rstrip(":") or None, timing["m"], timing["s"], timing["ms"]
            )
            continue
        if not line:
            close()
        elif line.startswith(("WEBVTT", "NOTE", "STYLE")) or (line.isdigit() and not block):
            continue  # the header, a note, an SRT's cue number
        elif count:
            block.append(re.sub(r"</?[a-z][^>]*>", "", line))  # <i>, <b>: styling, not words
    close()
    if not out:
        raise TranscriptError("the file has no cues in it")
    return Transcript(kind, tuple(out))


def parse(text: str) -> Transcript:
    """The transcript in this text, whichever of the four formats it is."""
    kind = detect(text)
    if kind == DMBOT:
        return _dmbot(text)
    if kind == PLAIN:
        return _plain(text)
    return _cues(text, kind)


def speaker_ids(transcript: Transcript) -> dict[str, int]:
    """A made-up number for each speaker, in the order they first speak."""
    return {name: SPEAKER_BASE + n for n, name in enumerate(transcript.speakers)}


def dm_id(transcript: Transcript, dm: str) -> int:
    """The number of the speaker named `dm` (case ignored). Raises if the file has no such
    speaker, naming who it does have so the name can be fixed."""
    ids = speaker_ids(transcript)
    for name, number in ids.items():
        if name.casefold() == dm.strip().casefold():
            return number
    raise TranscriptError(
        f"--dm {dm!r} isn't a speaker in the file. The speakers are: " + ", ".join(sorted(ids))
    )


def lines_of(transcript: Transcript) -> list[Line]:
    """The file as the recap worker reads a session: one line per utterance, in the file's order
    (a file is written in the order things were said; sorting by time would scramble one whose
    times restart or are missing)."""
    ids = speaker_ids(transcript)
    return [Line(u.at_ms, ids[u.speaker], u.text, u.text) for u in transcript.utterances]


def parse_names(text: str) -> list[SpokenName]:
    """The names file: one name a line, `Ka'zeth = Kah-zeth` to give how it is said, and an
    optional `secret:` first for a name the recap must never use. Blank lines and `#` lines are
    ignored."""
    out: list[SpokenName] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        secret = False
        if line.casefold().startswith("secret:"):
            secret = True
            line = line[len("secret:") :].strip()
        name, _, said = line.partition("=")
        name, said = name.strip(), said.strip()
        if not name:
            raise TranscriptError(f"line {number} of the names file has no name")
        out.append(SpokenName(name, said or None, secret=secret))
    return out


def cue_map(summary: str, script: Script) -> list[str]:
    """Every `《emotion》` and `((performance))` cue in the narration, in order, with the voice
    tag it became or "music only" (a cue the voice has no matching tag for still moves the
    music)."""
    found: list[tuple[int, str, str]] = []
    for match in CUE.finditer(summary):
        found.append((match.start(), EMOTION, match.group()[1:-1]))
    for match in PERFORMANCE.finditer(summary):
        found.append((match.start(), PERFORMANCE_KIND, match.group()[2:-2]))
    found.sort()
    tags: list[tuple[str, str, str]] = [(c.kind, c.name, c.tag) for c in script.cues]
    rows: list[str] = []
    for _, kind, name in found:
        tag = ""
        for i, (k, n, t) in enumerate(tags):
            if k == kind and n == name.strip().lower():
                tag = t
                del tags[i]
                break
        arrow = tag or "music only"
        shown = f"《{name}》" if kind == EMOTION else f"(({name}))"
        rows.append(f"  {shown} -> {arrow}")
    return rows


CONSENT_QUESTION = (
    "Did everyone whose words are in this file agree to be recorded and to this test? (yes/no) "
)


def consent_given(answer: str) -> bool:
    """Only the word yes. `--yes` covers the cost question and never this one."""
    return answer.strip().lower() == "yes"
