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
from collections.abc import Collection
from dataclasses import dataclass

from dmbot.recaps import fixed_lines
from dmbot.recaps.text import CUE, PERFORMANCE
from dmbot.secrets_match import secret_rule as matcher

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
    "whispered": "[whispers]",
}
PERFORMANCE_WORDS = tuple(_PERFORMANCE_TAGS)  # the pacing words the cue check allows
SCENE_BREAK_TAG = "[long pause]"
CLOSING_PAUSE_TAG = "[long pause]"  # before the closing line (owner, 2026-10-24, #2627)

# The voice's delivery tag for each mood the music has a style for (#1983), keyed by the music
# table's own words (`music.style_key` maps a cue's word, synonyms included, to them). Fixed on
# purpose, as the music table is: only a value from this table is ever sent to the voice, never
# a word a model or a player wrote. "" is an explicit "music only" (the voice stays as it is).
# The tags are Eleven v3's square-bracket audio tags. NOT checked against ElevenLabs' tag list
# from the build machine (no network there): the starting map is the issue's, and the switch
# `DMBOT_RECAP_VOICE_TAGS=0` exists so dev1 can compare by ear and correct a tag the model ignores.
EMOTION_VOICE: dict[str, str] = {
    "tense": "[urgently]",
    "urgent": "[urgently]",
    "battle": "[urgently]",
    "somber": "[sadly]",
    "sorrowful": "[sadly]",
    "melancholy": "[sadly]",
    "hopeful": "[softly]",
    "tender": "[softly]",
    "triumphant": "[confidently]",
    "defiant": "[confidently]",
    "mysterious": "[quietly]",
    "eerie": "[quietly]",
    "ominous": "[quietly]",
    "dread": "[quietly]",
    "playful": "[playfully]",
    "wonder": "[in awe]",
    "calm": "",  # the narrator's own voice is calm already
    "grim": "",  # no tag chosen for it yet: music only
}


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
    closing: bool = False  # the recap's closing line: said after a long pause (#2627)


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


def _mood_tag(name: str, mood: str, emotion_tags: bool) -> tuple[str, str]:
    """(the voice tag for an emotion cue, the mood the narration is now in). Whisper and sigh are
    always tags. Another mood's tag (`EMOTION_VOICE`) is sent when `emotion_tags` is on, and only
    where the mood changes: a cue that repeats the mood the narration is already in ("tense" then
    "tension") adds none, so the voice is not overdone. An unknown word has no mood and no tag."""
    from dmbot.recaps.audio import music  # (music imports this module: late)

    fixed = _EMOTION_TAGS.get(name, "")
    if fixed:
        return fixed, mood
    key = music.style_key(name)
    if not key:
        return "", mood
    if not emotion_tags or key == mood:
        return "", key
    return EMOTION_VOICE.get(key, ""), key


def _walk(
    block: str, mood: str = "", emotion_tags: bool = True
) -> tuple[str, str, list[tuple[int, int, str, str, str]], str]:
    """One block of the summary as (spoken, tagged, cues, the mood it ends in). A cue is (spoken
    offset, tagged offset, kind, name, tag); offsets are within this block. Taking a cue out
    never leaves a double space or a space before a comma or full stop, and the offsets are
    counted on the text as it ends up. `mood` is the one the narration was in before this block."""
    block = re.sub(r"\s+", " ", block).strip()
    spoken = ""
    tagged = ""
    cues: list[list[object]] = []
    before = mood  # the mood before the latest cue, to undo a tag with nothing after it
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
            before = mood
            tag, mood = _mood_tag(name, mood, emotion_tags)
        else:
            kind, name = PERFORMANCE_KIND, " ".join(raw[2:-2].lower().split())  # (spaces folded)
            tag = _PERFORMANCE_TAGS.get(name, "")
        cues.append([len(spoken), len(tagged), kind, name, tag])
        if tag:
            tagged += tag + " "
        pos = match.end()
    if cues and cues[-1][2] == EMOTION and cues[-1][4] in EMOTION_VOICE.values():
        tail = str(cues[-1][4])
        if tagged.endswith(tail + " "):
            # A mood tag with no words after it would be read to nothing, and would count as sent:
            # leave it out and leave the mood as it was, so the next cue of that mood sends it.
            tagged = tagged[: -len(tail) - 1]
            cues[-1][4] = ""
            mood = before
    spoken, tagged = spoken.strip(), tagged.strip()
    return (  # a cue at the very end sits at the end of the trimmed text, not past it
        spoken,
        tagged,
        [
            (
                min(int(str(c[0])), len(spoken)),
                min(int(str(c[1])), len(tagged)),
                str(c[2]),
                str(c[3]),
                str(c[4]),
            )
            for c in cues
        ],
        mood,
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


class SecretInScript(ValueError):
    """A secret name stands in the text meant for the voice (the caller must not send it).
    `rule` says which check found it, as a fixed word and never the name: `whole-name`, `word`
    or `run-together`."""

    def __init__(self, message: str, rule: str = "") -> None:
        super().__init__(message)
        self.rule = rule


def secret_rule(text: str, secret_names: Collection[str], allowed_names: Collection[str]) -> str:
    """Which rule finds a secret name in the text meant for the voice, or "" for none. It is the
    one shared matcher (`dmbot.secrets_match`), the very one the recap text's own check uses, so
    the two locks can never disagree about a name (#2050, #2087): the whole name with its
    differences (case, articles, endings, titles, accents, hyphens), the name run together at
    word edges ("WhiteDeath"), or several secret names glued into one word ("RaeAegis")."""
    return matcher(text, secret_names, allowed_names)


def _chunks(block: str, room: int) -> list[str]:
    """A paragraph as pieces whose sent text fits in `room` characters: cut at sentence ends,
    and a single over-long sentence at a space that is not inside a cue."""
    if len(_walk(block)[1]) <= room:
        return [block]
    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(block):
        while len(_walk(sentence)[1]) > room:
            cut = len(sentence[:room].rstrip())
            while cut > 0:
                cut = sentence.rfind(" ", 0, cut)
                head = sentence[:cut] if cut > 0 else ""
                if (
                    cut > 0
                    and head.count("《") == head.count("》")
                    and head.count("((") == head.count("))")
                ):
                    break
            if cut <= 0:
                cut = room  # no space to cut at: a hard cut (a cue could split; very rare)
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        candidate = f"{current} {sentence}".strip()
        if current and len(_walk(candidate)[1]) > room:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return [p for p in pieces if p.strip()]


def build(
    title: str,
    day: int | None,
    summary: str,
    *,
    secret_names: Collection[str] = (),
    allowed_names: Collection[str] = (),
    emotion_tags: bool = True,
) -> Script:
    """The script for one recap: the title line, then each paragraph of the summary. Housekeeping
    is not an input, so it can never be read. `secret_names`: if any stands in the title, the
    summary or a cue, `SecretInScript` is raised (the recap's own checks already refuse such a
    summary; this is the second lock, because what is built here goes to a provider).
    `allowed_names`: the confirmed names, which a secret name's word may share, as in the recap's
    own check, so the two locks agree.
    `emotion_tags`: send the voice a delivery tag where the mood changes (`EMOTION_VOICE`, #1983);
    False sends only whisper, sigh and the pauses, for comparing by ear."""
    if rule := secret_rule(f"{title} {summary}", secret_names, allowed_names):
        raise SecretInScript("a secret name is in the recap text", rule)
    pieces: list[tuple[str, str, list[tuple[int, int, str, str, str]]]] = []
    head = title_line(title, day)
    pieces.append((head, head, []))
    room = MAX_CHARS - len(SCENE_BREAK_TAG) - 1  # a scene break's tag is added to the last part
    mood = ""  # the mood the narration is in; a tag is sent where it changes
    for block, scene_break in _blocks(summary):
        parts = _chunks(block, room)
        for i, part in enumerate(parts):
            spoken, tagged, cues, after = _walk(part, mood, emotion_tags)
            if not spoken.strip():
                continue  # (a cue alone in a paragraph says nothing: its mood is not taken)
            mood = after
            if i == len(parts) - 1 and scene_break:
                tagged = f"{tagged} {SCENE_BREAK_TAG}"
            pieces.append((spoken, tagged, cues))
    paragraphs: list[Paragraph] = []
    cursor = 0
    spoken_all: list[str] = []
    for i, (sp, tg, cs) in enumerate(pieces):
        prev_text = pieces[i - 1][0][-NEIGHBOUR_CHARS:] if i > 0 else ""
        next_text = pieces[i + 1][0][:NEIGHBOUR_CHARS] if i + 1 < len(pieces) else ""
        shift = 0
        closing = i == len(pieces) - 1 and i > 0 and fixed_lines.is_closing(sp)
        if closing:
            # The closing line is said after a dramatic pause (#2627): the tag asks the voice for
            # it, and the render adds silence before it (`CLOSING_GAP_SECONDS`).
            tg = f"{CLOSING_PAUSE_TAG} {tg}"
            shift = len(CLOSING_PAUSE_TAG) + 1  # (the voice's timestamps count the tag)
        cue_tuple = tuple(Cue(cursor + o, to + shift, k, n, t) for o, to, k, n, t in cs)
        paragraphs.append(Paragraph(sp, tg, cursor, prev_text, next_text, cue_tuple, closing))
        spoken_all.append(sp)
        cursor += len(sp) + 2  # the blank line between paragraphs
    return Script(tuple(paragraphs), "\n\n".join(spoken_all))
