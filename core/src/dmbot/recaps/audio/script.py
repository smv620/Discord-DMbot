"""The narration script (#1413): what the voice reads, and where the cues fall.

A recap's summary carries `《emotion》` cues (the mood of a section) and `((performance))` cues
(pace and volume inside a paragraph). They are instructions for the performance, never words to
read aloud, so they are taken out of the spoken text. Where the voice has a matching delivery tag
(ElevenLabs v3 audio tags such as `[whispers]`) the cue becomes that tag in the text sent to the
voice; the cue map keeps each cue's place in the plain spoken text, so the music can follow the
moods once the voice's timestamps come back.

The title line is read first. The housekeeping bullets are never read: this module is not given
them. One request per paragraph, none over `MAX_CHARS`, each with the neighbouring paragraphs'
words (`previous_text`, `next_text`) so the voice stays consistent across requests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dmbot.recaps.text import CUE, PERFORMANCE

MAX_CHARS = 4000  # one request's limit for the voice
NEIGHBOUR_CHARS = 300  # how much of the next or the last paragraph the voice is shown
EMOTION = "emotion"
PERFORMANCE_KIND = "performance"
SCENE_BREAK = re.compile(r"^\s*(?:[-*_~=•]\s*){3,}$")  # a line of only dashes or stars
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")

# The voice's own delivery tags where one fits (a cue with no match only moves the music).
_EMOTION_TAGS = {"whisper": "[whispers]", "whispered": "[whispers]", "sigh": "[sighs]"}
_PERFORMANCE_TAGS = {
    "pause": "[short pause]",
    "short pause": "[short pause]",
    "beat": "[short pause]",
    "long pause": "[long pause]",
    "hushed": "[whispers]",
    "whisper": "[whispers]",
    "sigh": "[sighs]",
}
SCENE_BREAK_TAG = "[long pause]"


@dataclass(frozen=True, slots=True)
class Cue:
    offset: int  # place in the whole plain spoken text (`Script.spoken`)
    tagged_offset: int  # place in its paragraph's tagged text (the voice's timestamps count tags)
    kind: str  # EMOTION or PERFORMANCE_KIND
    name: str  # as written, without the brackets, lower case
    tag: str  # the voice's delivery tag it became, or ""


@dataclass(frozen=True, slots=True)
class Paragraph:
    spoken: str  # plain words, no cue and no tag
    tagged: str  # what is sent: the words with delivery tags in place of cues
    start: int  # where `spoken` begins in `Script.spoken`
    previous_text: str
    next_text: str
    cues: tuple[Cue, ...]


@dataclass(frozen=True, slots=True)
class Script:
    paragraphs: tuple[Paragraph, ...]
    spoken: str  # all paragraphs' plain words, blank lines between

    @property
    def cues(self) -> tuple[Cue, ...]:
        return tuple(c for p in self.paragraphs for c in p.cues)

    @property
    def emotions(self) -> tuple[Cue, ...]:
        return tuple(c for c in self.cues if c.kind == EMOTION)


def title_line(title: str, day: int | None) -> str:
    """The line the voice opens with: "Session Summary: <title>, Day X" (the day only if known)."""
    clean = " ".join(CUE.sub(" ", PERFORMANCE.sub(" ", title)).split()).strip(" *")
    return f"Session Summary: {clean}" + (f", Day {day}" if day else "")


def _split_long(text: str) -> list[str]:
    """A paragraph over the limit, cut at sentence ends (a single over-long sentence at a space)."""
    if len(text) <= MAX_CHARS:
        return [text]
    out: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        while len(sentence) > MAX_CHARS:  # no sentence end in reach: cut at the last space
            cut = sentence.rfind(" ", 0, MAX_CHARS)
            cut = cut if cut > 0 else MAX_CHARS
            if current:
                out.append(current)
                current = ""
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + 1 + len(sentence) > MAX_CHARS:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return [p for p in out if p]


def _walk(block: str) -> tuple[str, str, list[tuple[int, int, str, str, str]]]:
    """One block of the summary as (spoken, tagged, cues). A cue is (spoken offset, tagged
    offset, kind, name, tag); offsets are within this block. Taking a cue out never leaves a
    double space or a space before a comma or full stop, and the offsets are counted on the
    text as it ends up."""
    block = re.sub(r"\s+", " ", block).strip()
    spoken = ""
    tagged = ""
    cues: list[list[object]] = []
    pos = 0
    pattern = re.compile(f"{CUE.pattern}|{PERFORMANCE.pattern}")
    for match in [*pattern.finditer(block), None]:
        text = block[pos : match.start() if match else len(block)]
        if text:
            if text[0] in ",.;:!?":  # no space before punctuation
                spoken = spoken.rstrip(" ")
                tagged = tagged.rstrip(" ")
                for cue in cues:  # a cue that sat in that space now sits at the new end
                    cue[0] = min(int(str(cue[0])), len(spoken))
                    cue[1] = min(int(str(cue[1])), len(tagged))
            elif text[0] == " " and (not spoken or spoken.endswith(" ")):
                text = text.lstrip(" ")
            spoken += text
            tagged += text
        if match is None:
            break
        raw = match.group(0)
        if raw.startswith("《"):
            kind, name = EMOTION, raw[1:-1].strip().lower()
            tag = _EMOTION_TAGS.get(name, "")
        else:
            kind, name = PERFORMANCE_KIND, raw[2:-2].strip().lower()
            tag = _PERFORMANCE_TAGS.get(name, "")
        cues.append([len(spoken), len(tagged), kind, name, tag])
        if tag:
            tagged += tag + " "
        pos = match.end()
    return (
        spoken.strip(),
        tagged.strip(),
        [(int(str(c[0])), int(str(c[1])), str(c[2]), str(c[3]), str(c[4])) for c in cues],
    )


def _blocks(summary: str) -> list[tuple[str, bool]]:
    """The summary's paragraphs, each with whether a scene break follows it."""
    out: list[tuple[str, bool]] = []
    for chunk in re.split(r"\n\s*\n", summary.strip()):
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        if lines and all(SCENE_BREAK.match(ln) for ln in lines):
            if out:
                out[-1] = (out[-1][0], True)
            continue
        text = " ".join(ln.strip() for ln in lines if not SCENE_BREAK.match(ln))
        if text:
            out.append((text, bool(lines) and SCENE_BREAK.match(lines[-1]) is not None))
    return out


def build(title: str, day: int | None, summary: str) -> Script:
    """The script for one recap: the title line, then each paragraph of the summary. Housekeeping
    is not an input, so it can never be read."""
    pieces: list[tuple[str, str, list[tuple[int, int, str, str, str]]]] = []
    head = title_line(title, day)
    pieces.append((head, head, []))
    # A cue only stands for the part it opens; a split paragraph keeps its cues in the part
    # where they fall.
    for block, scene_break in _blocks(summary):
        spoken, tagged, cues = _walk(block)
        if not spoken.strip():
            continue
        if len(tagged) <= MAX_CHARS:
            parts = [(spoken, tagged, cues)]
        else:  # rare: cut the plain words, and carry the cues to the part they fall in
            parts = []
            consumed = 0
            for part in _split_long(spoken):
                begin = spoken.find(part, consumed)
                end = begin + len(part)
                consumed = end
                inside = [
                    (o - begin, o - begin, k, n, t) for o, _, k, n, t in cues if begin <= o < end
                ]
                parts.append((part, part, inside))
        for i, (sp, tg, cs) in enumerate(parts):
            last = i == len(parts) - 1
            if last and scene_break:
                tg = f"{tg} {SCENE_BREAK_TAG}"
            pieces.append((sp, tg, cs))
    paragraphs: list[Paragraph] = []
    cursor = 0
    spoken_all: list[str] = []
    for i, (sp, tg, cs) in enumerate(pieces):
        prev_text = pieces[i - 1][0][-NEIGHBOUR_CHARS:] if i > 0 else ""
        next_text = pieces[i + 1][0][:NEIGHBOUR_CHARS] if i + 1 < len(pieces) else ""
        cue_tuple = tuple(Cue(cursor + o, to, k, n, t) for o, to, k, n, t in cs)
        paragraphs.append(Paragraph(sp, tg, cursor, prev_text, next_text, cue_tuple))
        spoken_all.append(sp)
        cursor += len(sp) + 2  # the blank line between paragraphs
    return Script(tuple(paragraphs), "\n\n".join(spoken_all))
