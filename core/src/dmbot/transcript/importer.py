"""Reading a transcript file (#1969, moved here for #2105): the parser the recap test tool and the
campaign importer share. Pure: no database, no network, no AI.

A DM can load a game that was played before DMbot was listening, or recorded with another tool.
This module turns such a file into utterances (speaker, time, words). Four shapes are accepted and
told apart by their content, never by the file's name; anything else is refused with a line that
names them (`FORMATS`):

* DMbot's own download from the website (raw or cleaned): `[0:42:10] (Mia) {Cerric}: words`;
* plain lines, `Name: words`, with an optional `[hh:mm:ss]` first. Craig's plain-text export is a
  known source (#1977), so this reader is forgiving: up to 10 header lines before the first
  utterance are skipped, a line that is not `Name: words` after an utterance is that utterance
  carrying on (joined with a space; refused if over a quarter of the lines would be joined, as
  then it is not this format), and a name may hold `( ) # . ' _` (`Rowan (rowan_v)`, `kai#0`).
  Craig marks talk it cut out with a braced placeholder, `{non-game context}` (#1995; the only one
  seen so far): a line that is only such a placeholder is dropped and counted apart, never as a
  joined line, and a placeholder at the start of an utterance is stripped (an utterance left empty
  is dropped);
* the same with the name in square brackets, `[Name]:  words` (a Google Doc's transcript), or
  `[Name] words` when lines with a colon after the name are not most of the file; a bracket that
  holds a clock,
  `[0:42:10]`, stays a time;
* WebVTT and SRT whose cue text starts `Name:`, or WebVTT cues with a voice tag, `<v Name>words</v>`
  (a caption track from a recording).

A byte-order mark some tools write first is dropped before anything is told apart. A file that
cannot be read is refused with a `code` the page puts in plain words (`TranscriptError.code`).

Nothing here prints a line of the file; errors name the format or the speakers, never the words.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

DMBOT, PLAIN, VTT, SRT = "dmbot", "plain", "vtt", "srt"
FORMATS = (
    "DMbot's own transcript download, plain 'Name: words' lines (an optional [hh:mm:ss] first, "
    "or the name in [square brackets]), or WebVTT or SRT captions that name who is speaking"
)
# Why a file could not be read, for the page to say in plain words (no word of the file in any).
UNKNOWN_FORMAT = "unknown_format"  # none of the formats above
NO_SPEAKER_NAMES = "no_speaker_names"  # captions with no names in them
UNREADABLE = "unreadable"  # recognised, but a line or a cue could not be read
SPEAKER_BASE = 100  # made-up speaker numbers, so none can be mistaken for a real account
LINE_MS = 1000  # a file with no times: one second a line keeps the order
# Braced tags in DMbot's download that say what a line is, not who a character is.
TAGS = frozenset({"narrating", "table_talk", "non-game_content"})

log = logging.getLogger(__name__)

_CLOCK = r"(?:(\d+):)?(\d{1,2}):(\d{2})"
_DMBOT_HEAD = re.compile(r"^DMbot transcript:")
_DMBOT_LINE = re.compile(
    r"^\[(?P<at>\d+:\d{2}:\d{2})\] \((?P<who>[^()]+)\)"
    r"(?P<tag> \[(?:DM Sidebar|Table Q)[^\]]*\])?"
    r"(?: \{(?P<char>[^{}]+)\})?(?P<typed> \(typed\))?: (?P<text>.+)$"
)
_DMBOT_OTHER = re.compile(r"^\[\d+:\d{2}:\d{2}\] \(")  # sidebar, skipped-note and DMbot lines
# A plain name may hold ( ) # . ' _ (Craig writes "Display (username)", "name#0") but never
# brackets, braces or <>: DMbot's own download, with braces, is tried before this format.
_PLAIN = re.compile(
    rf"^(?:\[(?P<at>{_CLOCK})\]\s*)?(?P<who>[^\s:\[\]{{}}<>][^:\[\]{{}}<>]{{0,59}}):\s+(?P<text>\S.*)$"
)
# The name in square brackets, as a Google Doc's transcript writes it: `[Name]:  words`. A bracket
# that holds a clock has a colon in it, so it is never taken for a name.
_BRACKET_COLON = re.compile(
    rf"^(?:\[(?P<at>{_CLOCK})\]\s*)?\[(?P<who>[^\[\]{{}}<>:]{{1,60}})\]\s*:\s*(?P<text>\S.*)$"
)
# `[Name] words`, with no colon: tried only when `Name: words` and `[Name]: words` lines are not
# most of the file, so a wrapped line that starts with a note such as `[laughs]` is not a speaker.
_BRACKET_ONLY = re.compile(r"^\[(?P<who>[^\[\]{}<>:]{1,60})\]\s+(?P<text>\S.*)$")
_PLAIN_STYLES = ((_PLAIN, _BRACKET_COLON), (_BRACKET_ONLY,))
_Style = tuple[re.Pattern[str], ...]


def _match(style: _Style, line: str) -> re.Match[str] | None:
    for pattern in style:
        found = pattern.match(line)
        if found is not None:
            return found
    return None


def _at(found: re.Match[str]) -> str | None:
    """The clock a plain line starts with, if it has one (the bracketed-name form has none)."""
    return found["at"] if "at" in found.re.groupindex else None


# Craig's cut-out placeholder: a braced note of up to about 40 characters, alone on its line or
# at the start of what someone said. DMbot's own download, which uses braces for characters, is
# tried before this format, so this only ever reads a plain file.
_CUT_OUT = re.compile(r"^\{[^{}]{1,40}\}$")
_CUT_OUT_LEAD = re.compile(r"^(?:\{[^{}]{1,40}\}\s*)+")
HEADER_MAX = 10  # lines before the first utterance that a plain file may have
JOINED_MAX_SHARE = 4  # at most one line in this many may be a wrapped line (a quarter)
_TIMING = re.compile(r"^(?P<h>\d+:)?(?P<m>\d{1,2}):(?P<s>\d{2})[.,](?P<ms>\d{3})\s+-->\s+")
# WebVTT's voice tag, `<v Name>` or `<v.loud Name>`, with or without a closing `</v>`.
# The class part excludes "." and "<" so the repeat cannot backtrack catastrophically: `<v` followed
# by thousands of dots, or `<v.a` repeated, is read in linear time.
_VOICE = re.compile(r"<v(?:\.[^\s.<>]+)*\s+(?P<who>[^\s<>][^<>]{0,59})>", re.I)
_CUE_SPEAKER = re.compile(r"^(?P<who>[^:\[\]{}()<>]{1,60}):\s+(?P<text>\S.*)$", re.S)


class TranscriptError(ValueError):
    """The file can't be used; the message is plain words, never a line of the file. `code` is
    one of the codes above, for the page."""

    def __init__(self, message: str, code: str = UNREADABLE) -> None:
        super().__init__(message)
        self.code = code


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
    skipped: int = 0  # plain format: header lines before the first utterance (a count only)
    joined: int = 0  # plain format: wrapped lines joined to the utterance before them
    cut_out: int = 0  # plain format: lines that were only a cut-out placeholder, dropped
    timed: bool = (
        True  # the file has times; a plain file with none is kept in order by line (#2013)
    )

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
    lines = [line.strip() for line in text.lstrip("\ufeff").splitlines() if line.strip()]
    if not lines:
        raise TranscriptError("the file is empty")
    if _DMBOT_HEAD.match(lines[0]):
        return DMBOT
    if lines[0].startswith("WEBVTT"):
        return VTT
    if any(_TIMING.match(line) for line in lines[:6]) and lines[0].isdigit():
        return SRT
    if _plain_style([line for line in lines if not _CUT_OUT.match(line)]) is not None:
        return PLAIN
    raise TranscriptError(
        f"I don't know this file's format. I can read: {FORMATS}.", UNKNOWN_FORMAT
    )


def _dmbot(text: str) -> Transcript:
    out: list[Utterance] = []
    characters: dict[str, None] = {}
    for raw in text.splitlines():
        line = raw.strip()
        found = _DMBOT_LINE.match(line)
        if found is None:
            continue  # the header, a skipped-chat note, a blank line
        who = found["who"].strip()
        if who == "DMbot" or found["tag"]:
            continue  # DMbot's own words and the DM's questions to it are not the table's
        char = (found["char"] or "").strip()
        if char and char.lower() not in TAGS:
            characters[char] = None
        out.append(Utterance(who, _clock_ms(found["at"]), found["text"].strip()))
    if not out:
        raise TranscriptError("the DMbot download has no spoken lines in it")
    return Transcript(DMBOT, tuple(out), tuple(characters))


def _plain_split(
    lines: list[str], style: _Style = _PLAIN_STYLES[0]
) -> tuple[int, list[tuple[re.Match[str] | None, str]]] | None:
    """(how many header lines come first, every line after them with its match or None), or None
    when no `Name: words` line comes within the first `HEADER_MAX + 1` lines."""
    for header, line in enumerate(lines[: HEADER_MAX + 1]):
        if _match(style, line):
            return header, [(_match(style, rest), rest) for rest in lines[header:]]
    return None


def _plain_style(lines: list[str]) -> _Style | None:
    """The first style of plain lines under which most of the lines have a name and words."""
    for style in _PLAIN_STYLES:
        split = _plain_split(lines, style)
        if split is not None and sum(1 for found, _ in split[1] if found) * 2 > len(split[1]):
            # One form per file: once most lines are `[Name]: words`, a wrapped line such as
            # ` then he said: stop` is a continuation, not a new speaker.
            bracketed = sum(1 for _, line in split[1] if _BRACKET_COLON.match(line))
            if len(style) > 1 and bracketed * 2 > len(split[1]):
                return (_BRACKET_COLON,)
            return style
    return None


def _plain(text: str) -> Transcript:
    numbered = [(n, raw.strip()) for n, raw in enumerate(text.splitlines(), start=1) if raw.strip()]
    cut_out = sum(1 for _, line in numbered if _CUT_OUT.match(line))
    numbered = [(n, line) for n, line in numbered if not _CUT_OUT.match(line)]
    style = _plain_style([line for _, line in numbered]) or _PLAIN_STYLES[0]
    split = _plain_split([line for _, line in numbered], style)
    if split is None:
        raise TranscriptError(f"no 'Name: words' line in the first {HEADER_MAX + 1} lines")
    header, rows = split
    wrapped = [numbered[header + i][0] for i, (found, _) in enumerate(rows) if found is None]
    if len(wrapped) * JOINED_MAX_SHARE > len(rows):  # then it isn't this format
        raise TranscriptError(f"line {wrapped[0]} has no 'Name: words' form")
    out: list[Utterance] = []
    for found, line in rows:
        if found is None:  # an utterance carrying on: the first row always matches
            last = out[-1]
            out[-1] = Utterance(last.speaker, last.at_ms, f"{last.text} {line}")
            continue
        # A line with no time follows the one before it, so a mixed file keeps its order.
        after = out[-1].at_ms + LINE_MS if out else 0
        clock = _at(found)
        at = _clock_ms(clock) if clock else after
        # A leading placeholder is stripped; an utterance left empty stays until the end so that the
        # wrapped lines after it join to it and not to the speaker before.
        said = _CUT_OUT_LEAD.sub("", found["text"].strip())
        out.append(Utterance(found["who"].strip(), at, said))
    kept = tuple(Utterance(u.speaker, u.at_ms, u.text.strip()) for u in out if u.text.strip())
    timed = any(_at(found) for found, _ in rows if found is not None)
    return Transcript(
        PLAIN, kept, skipped=header, joined=len(wrapped), cut_out=cut_out, timed=timed
    )


def _cues(text: str, kind: str) -> Transcript:
    """WebVTT and SRT: a timing line, then the cue's text, which starts `Name:`."""
    out: list[Utterance] = []
    unnamed: list[int] = []  # cues with no speaker named
    named = 0  # cues that did
    block: list[str] = []
    start = 0
    count = 0

    def close() -> None:
        nonlocal block, named
        if not block:
            return
        text = " ".join(block)
        if _VOICE.search(text):
            # `<v Ann>one</v> <v Bo>two</v>`: each voice is a speaker. Words before the first tag
            # carry on the speaker of the cue before.
            parts = _VOICE.split(text)  # [before, who, words, who, words, ...]
            if parts[0].strip() and out:
                out[-1] = Utterance(
                    out[-1].speaker, out[-1].at_ms, f"{out[-1].text} {parts[0].strip()}"
                )
            before = len(out)
            for who, words in zip(parts[1::2], parts[2::2], strict=True):
                spoken = re.sub(r"</?[a-z][^<>]*>", "", words, flags=re.I).strip()
                if spoken:
                    out.append(Utterance(who.strip(), start, spoken))
            named += len(out) > before  # an empty `<v X></v>` names no one
            block = []
            return
        said = _CUE_SPEAKER.match(text)
        if said is None:
            unnamed.append(count)
            block = []
            return
        named += 1
        out.append(Utterance(said["who"].strip(), start, said["text"].strip()))
        block = []

    rows = [raw.strip() for raw in text.splitlines()]
    skipping = False  # inside a NOTE or STYLE block, which runs to the next blank line
    for i, line in enumerate(rows):
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
            skipping = False
        elif skipping or line.startswith(("WEBVTT", "NOTE", "STYLE")):
            skipping = not line.startswith("WEBVTT")  # (WEBVTT's own line ends the header)
        elif i + 1 < len(rows) and _TIMING.match(rows[i + 1]):
            continue  # a cue's number (SRT) or identifier (WebVTT), on the line before its timing
        elif count:
            # <v Name> is kept for `close` (it names who speaks); <i>, <b> and the like are styling
            block.append(re.sub(r"</?(?!v[\s.>])[a-z][^<>]*>", "", line, flags=re.I))
    close()
    if not out and not unnamed:
        raise TranscriptError("the file has no cues in it")
    if unnamed and named > len(unnamed):
        # Most cues name who speaks, so a stray `[music]` is a sound note, not a missing speaker.
        # Counted in the log only (never a word of the file).
        log.info("Transcript import: %d unnamed cue(s) skipped of %d", len(unnamed), count)
    elif unnamed:
        raise TranscriptError(f"cue {unnamed[0]} does not say who is speaking", NO_SPEAKER_NAMES)
    return Transcript(kind, tuple(out))


def parse(text: str) -> Transcript:
    """The transcript in this text, whichever of the four formats it is."""
    text = text.lstrip("\ufeff")  # a byte-order mark some tools write first
    kind = detect(text)
    if kind == DMBOT:
        return _dmbot(text)
    if kind == PLAIN:
        return _plain(text)
    return _cues(text, kind)
