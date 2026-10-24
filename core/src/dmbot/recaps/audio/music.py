"""The music and ambience requests (#1413): plans made before the voice exists.

**Music.** One composition plan, one section for each run of the same `《emotion》` cue, each
section as long as its share of the spoken words (an estimate: the voice's real timings replace
these in #1383), the whole piece 3 to 10 minutes. Styles come from a fixed table, orchestral and
cinematic fantasy; the words never name a game, a film, a composer or an artist (an IP rule
from CLAUDE.md), and the music is always instrumental.

**Ambience.** One looping sound prompt for each kind of scene found in the cues and the setting
words (tavern, forest, rain, cave, battle, camp), 20 to 30 seconds, so the audio step can keep
one for each campaign and scene kind.

Nothing here reads a secret: it works from the narration the DM already reviewed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from dmbot.recaps.audio.script import PERFORMANCE_KIND, Script

MIN_SECONDS = 180
MAX_SECONDS = 600
MIN_SECTION_MS = 3000  # the provider's shortest section
MAX_SECTION_MS = 120_000  # and its longest
SPOKEN_CHARS_PER_SECOND = 14.0  # about 150 words a minute
MAX_SECTIONS = 10
AMBIENCE_SECONDS = 25.0  # inside the 20 to 30 asked for
MAX_AMBIENCE = 3

GLOBAL_POSITIVE = ("orchestral", "cinematic fantasy", "instrumental", "sits quietly under a voice")
GLOBAL_NEGATIVE = ("vocals", "lyrics", "spoken words", "electronic dance", "loud percussion")

# emotion -> (positive local styles, negative local styles). Fixed on purpose: nothing a player
# or a model wrote reaches a music prompt.
STYLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "tense": (("low strings", "slow pulse", "restrained"), ("bright", "cheerful")),
    "somber": (("solo cello", "slow", "soft"), ("upbeat", "bright")),
    "sorrowful": (("solo cello", "slow", "soft"), ("upbeat", "bright")),
    "hopeful": (("warm strings", "gentle harp", "rising"), ("dark", "ominous")),
    "triumphant": (("brass", "full strings", "steady drums"), ("quiet", "tentative")),
    "mysterious": (("celesta", "soft woodwinds", "spacious"), ("loud", "cheerful")),
    "eerie": (("sustained strings", "distant chimes", "sparse"), ("cheerful", "full band")),
    "urgent": (("driving strings", "fast pulse", "tight"), ("slow", "relaxed")),
    "tender": (("soft piano", "warm strings", "slow"), ("harsh", "loud")),
    "melancholy": (("solo violin", "soft piano", "slow"), ("upbeat", "bright")),
    "ominous": (("low brass", "deep drums", "slow"), ("bright", "cheerful")),
    "dread": (("low drones", "dissonant strings", "sparse"), ("cheerful", "bright")),
    "wonder": (("harp", "shimmering strings", "open"), ("dark", "heavy")),
    "calm": (("soft strings", "light woodwinds", "gentle"), ("loud", "tense")),
    "playful": (("pizzicato strings", "light woodwinds", "bouncy"), ("dark", "slow")),
    "battle": (("pounding drums", "driving brass", "fast strings"), ("quiet", "relaxed")),
    "grim": (("low strings", "slow drums", "heavy"), ("bright", "cheerful")),
    "defiant": (("brass", "steady drums", "rising strings"), ("quiet", "tentative")),
}
DEFAULT_STYLE = ("soft strings", "gentle", "cinematic"), ("loud", "vocals")
# Other words an emotion cue may use, mapped to a table entry.
SYNONYMS = {
    "tension": "tense", "suspense": "tense", "sad": "somber", "grief": "sorrowful",
    "hope": "hopeful", "victory": "triumphant", "mystery": "mysterious", "spooky": "eerie",
    "fear": "dread", "afraid": "dread", "danger": "urgent", "love": "tender", "gentle": "tender",
    "peaceful": "calm", "quiet": "calm", "wistful": "melancholy", "fight": "battle",
    "action": "battle", "combat": "battle", "angry": "grim", "fun": "playful", "joy": "playful",
    "awe": "wonder", "determined": "defiant", "resolute": "defiant",
}  # fmt: skip

# Words that are never allowed in a music or sound prompt. A test checks the fixed tables
# against this short list; the real protection is that the tables are fixed.
DENY = (
    "dungeons", "d&d", "zelda", "skyrim", "witcher", "star wars", "lord of the rings", "tolkien",
    "hans zimmer", "john williams", "howard shore", "game of thrones", "elder scrolls", "baldur",
    "final fantasy", "harry potter", "mozart", "beethoven", "disney", "marvel", "pixar", "dnd",
    "warcraft", "pokemon", "nintendo", "hobbit", "narnia", "lotr",
)  # fmt: skip


def style_key(emotion: str) -> str:
    """The table entry an emotion cue uses ("" when it has none: the default style)."""
    word = re.sub(r"[^a-z ]", "", emotion.lower()).strip()
    word = SYNONYMS.get(word, word)
    return word if word in STYLES else ""


# How the piece ends (owner, 2026-10-24, #2629): never an abrupt stop. The final section is an
# ending that fits the mood of the recap's last scene: a gentle fade down to silence for the quiet
# moods, a rising swell that resolves on one held chord for the driving ones. The same kind tells
# the mix how to fade the music, as a safety (a chord that rings past the cut never clicks off).
DIMINUENDO, CRESCENDO = "diminuendo", "crescendo"
ENDING_PROMPTS = {
    DIMINUENDO: "a gentle diminuendo to silence on the final chord",
    CRESCENDO: "a rising crescendo that resolves on one final held chord",
}
ENDINGS: dict[str, str] = {
    "calm": DIMINUENDO, "somber": DIMINUENDO, "sorrowful": DIMINUENDO,
    "melancholy": DIMINUENDO, "tender": DIMINUENDO, "mysterious": DIMINUENDO,
    "eerie": DIMINUENDO, "dread": DIMINUENDO, "ominous": DIMINUENDO, "grim": DIMINUENDO,
    "wonder": DIMINUENDO,
    "triumphant": CRESCENDO, "tense": CRESCENDO, "urgent": CRESCENDO, "battle": CRESCENDO,
    "defiant": CRESCENDO, "hopeful": CRESCENDO, "playful": CRESCENDO,
}  # fmt: skip
DEFAULT_ENDING = DIMINUENDO  # a mood with no entry (the default style) fades down
CLOSING_SECONDS = 4.5  # the closing line, when its real length is not known
ENDING_TAIL_SECONDS = 1.5  # the music runs this long after the last word (the render's tail)


def ending_for(emotion: str) -> str:
    """How a piece ending in this mood ends: `DIMINUENDO` or `CRESCENDO`."""
    return ENDINGS.get(emotion, DEFAULT_ENDING)


@dataclass(frozen=True, slots=True)
class Section:
    emotion: str
    start_char: int  # where it begins in the plain spoken text
    chars: int  # how many spoken characters it covers
    duration_ms: int
    ending: bool = False  # the last section: a composed ending (#2629)


def total_seconds(spoken_chars: int) -> int:
    """How long the music is: as long as the voice is expected to be, within 3 to 10 minutes."""
    return int(min(MAX_SECONDS, max(MIN_SECONDS, round(spoken_chars / SPOKEN_CHARS_PER_SECOND))))


def _merge_shortest(spans: list[tuple[str, int, int]]) -> None:
    """Fold the shortest run into its neighbour (the one before it, or after if it is first), so
    no mood at either end of the piece is lost and the opening and closing keep their place."""
    i = min(range(len(spans)), key=lambda j: spans[j][2])
    into = i - 1 if i > 0 else i + 1
    name, start, chars = spans[into]
    spans[into] = (name, min(start, spans[i][1]), chars + spans[i][2])
    del spans[i]


def sections(
    script: Script,
    *,
    seconds: int | None = None,
    starts: Mapping[int, float] | None = None,
    closing_seconds: float | None = None,
) -> list[Section]:
    """One section for each run of the same mood, from the cue map (two cues with the same style,
    such as "tense" and "tension", are one run). Text before the first cue is a calm opening.
    More than `MAX_SECTIONS` moods are merged, shortest first. A run longer than the provider's
    longest section is split into equal parts of the same style. The durations add up to exactly
    the total (3 to 10 minutes), none under the provider's 3 seconds. `starts` maps a cue's place
    in the spoken text to the second the voice reaches it (from the voice's timestamps); with it
    each section is as long as the voice spends in it, and without it as long as its share of
    the words. The last section is an ending (#2629): it covers the closing line and the 1.5
    seconds after it (`closing_seconds` is the line's length, `CLOSING_SECONDS` when not known),
    never under the provider's 3 seconds; a last section too short to hold both a body and an
    ending is itself the ending."""
    spoken_len = max(1, len(script.spoken))
    total_ms = (seconds if seconds is not None else total_seconds(spoken_len)) * 1000
    # An estimate is held to 3 to 10 minutes; once the voice's real timings are known (`starts`)
    # the music is exactly as long as the voice, only held to what the provider can make.
    floor = MIN_SECTION_MS if starts is not None else MIN_SECONDS * 1000
    total_ms = max(floor, min(MAX_SECONDS * 1000, total_ms))
    marks: list[tuple[int, str]] = []
    for cue in script.emotions:
        mood = style_key(cue.name) or "calm"
        if marks and marks[-1][1] == mood:
            continue  # the same style again: one run
        marks.append((cue.offset, mood))
    if not marks or marks[0][0] > 0:
        if not marks or marks[0][1] != "calm":
            marks.insert(0, (0, "calm"))
        else:
            marks[0] = (0, "calm")
    spans: list[tuple[str, int, int]] = []  # (mood, where it starts, its weight)
    for i, (start, name) in enumerate(marks):
        if starts is not None:
            begin = 0.0 if start == 0 else starts.get(start, 0.0)
            nxt = marks[i + 1][0] if i + 1 < len(marks) else None
            end = total_ms / 1000 if nxt is None else starts.get(nxt, begin)
            spans.append((name, start, max(1, round((end - begin) * 1000))))
        else:
            end_char = marks[i + 1][0] if i + 1 < len(marks) else spoken_len
            spans.append((name, start, max(1, end_char - start)))
    # one place is kept for the ending that may be split off the last run
    while len(spans) > MAX_SECTIONS - 1 or (
        len(spans) > 1 and len(spans) * MIN_SECTION_MS > total_ms
    ):
        _merge_shortest(spans)  # and a short piece can't hold more sections than 3 s each
    # Shares of the total in proportion to the words each covers, none under 3 s; the rounding
    # and the lifting of short ones are settled on the longest, so the sum is exact.
    sum_w = sum(c for _, _, c in spans)
    base = [max(MIN_SECTION_MS, total_ms * c // sum_w) for _, _, c in spans]
    longest = max(range(len(base)), key=lambda i: base[i])
    base[longest] += total_ms - sum(base)  # the merging above leaves room for 3 s each
    out: list[Section] = []
    for (name, start, chars), ms in zip(spans, base, strict=True):
        parts = -(-ms // MAX_SECTION_MS)
        each, left = divmod(ms, parts)
        for k in range(parts):
            out.append(Section(name, start, chars // parts, each + (left if k == parts - 1 else 0)))
    return _with_ending(out, closing_seconds)


def _with_ending(out: list[Section], closing_seconds: float | None) -> list[Section]:
    """Make the last section an ending: split off its last stretch (the closing line and the tail
    after it), or, when it is too short for a body and an ending, the whole of it."""
    last = out[-1]
    closing = CLOSING_SECONDS if closing_seconds is None else max(0.0, closing_seconds)
    want = max(MIN_SECTION_MS, round((closing + ENDING_TAIL_SECONDS) * 1000))
    if last.duration_ms >= want + MIN_SECTION_MS:
        out[-1] = Section(last.emotion, last.start_char, last.chars, last.duration_ms - want)
        out.append(Section(last.emotion, last.start_char + last.chars, 0, want, ending=True))
    else:
        out[-1] = Section(last.emotion, last.start_char, last.chars, last.duration_ms, ending=True)
    return out


def composition_request(
    script: Script,
    *,
    seconds: int | None = None,
    starts: Mapping[int, float] | None = None,
    closing_seconds: float | None = None,
) -> dict[str, Any]:
    """The music request: a composition plan with one section for each mood, instrumental, the
    last one a composed ending (#2629)."""
    plan_sections = []
    for s in sections(script, seconds=seconds, starts=starts, closing_seconds=closing_seconds):
        pos, neg = STYLES.get(s.emotion, DEFAULT_STYLE)
        name = s.emotion.capitalize()
        if s.ending:
            pos = (*pos, ENDING_PROMPTS[ending_for(s.emotion)])
            neg = (*neg, "abrupt stop", "cut off")
            name = f"{name} ending"
        plan_sections.append(
            {
                "section_name": name,
                "positive_local_styles": list(pos),
                "negative_local_styles": list(neg),
                "duration_ms": s.duration_ms,
                "lines": [],
            }
        )
    return {
        "composition_plan": {
            "positive_global_styles": list(GLOBAL_POSITIVE),
            "negative_global_styles": list(GLOBAL_NEGATIVE),
            "sections": plan_sections,
        },
        "force_instrumental": True,
    }


# --- ambience ---

# scene kind -> (words that show it, the sound prompt). Fixed prompts: no lyrics, no voices.
SCENES: dict[str, tuple[tuple[str, ...], str]] = {
    "tavern": (
        ("tavern", "inn", "alehouse", "taproom", "pub"),
        "Warm tavern room ambience: low murmur of unintelligible crowd, wooden cups, a crackling "
        "hearth, seamless loop, no music",
    ),
    "forest": (
        ("forest", "woods", "woodland", "grove", "thicket"),
        "Quiet forest ambience: leaves in a light breeze, distant birdsong, seamless loop, "
        "no music",
    ),
    "rain": (
        ("rain", "storm", "downpour", "thunder", "drizzle"),
        "Steady rain on leaves and stone with faint distant thunder, seamless loop, no music",
    ),
    "cave": (
        ("cave", "cavern", "tunnel", "dungeon", "crypt", "catacomb"),
        "Cave ambience: slow water drips, deep echoing air, faint stone creaks, seamless loop, "
        "no music",
    ),
    "battle": (
        ("battle", "fight", "clash", "ambush", "skirmish", "war"),
        "Distant battle ambience: muffled clashing metal, shouts far off, wind, seamless loop, "
        "no music",
    ),
    "camp": (
        ("camp", "campfire", "bonfire", "firelight"),
        "Night campfire ambience: soft crackling fire, crickets, a faint breeze, seamless loop, "
        "no music",
    ),
}


@dataclass(frozen=True, slots=True)
class Ambience:
    kind: str  # the scene kind: the cache key, with the campaign
    text: str
    duration_seconds: float = AMBIENCE_SECONDS
    loop: bool = True

    def as_request(self) -> dict[str, object]:
        return {"text": self.text, "duration_seconds": self.duration_seconds, "loop": self.loop}


def ambience_requests(script: Script) -> list[Ambience]:
    """The loops this recap wants, in the order the scenes first appear, at most three. The
    scene words are looked for in the performance cues and in the narration."""
    hits: list[tuple[int, str]] = []
    cue_text = " ".join(c.name for c in script.cues if c.kind == PERFORMANCE_KIND)
    haystack = f"{script.spoken.lower()}\n{cue_text}"
    for kind, (words, _) in SCENES.items():
        # Whole words (and their plurals): "campaign" is not a camp, nor "public" a pub.
        found = [m.start() for w in words if (m := re.search(rf"\b{re.escape(w)}s?\b", haystack))]
        if found:
            hits.append((min(found), kind))
    hits.sort()
    return [Ambience(kind, SCENES[kind][1]) for _, kind in hits[:MAX_AMBIENCE]]


def ending_of(script: Script, *, seconds: int | None = None) -> str:
    """How this recap's music ends (`DIMINUENDO` or `CRESCENDO`), by the mood of its last scene."""
    return ending_for(sections(script, seconds=seconds)[-1].emotion)
