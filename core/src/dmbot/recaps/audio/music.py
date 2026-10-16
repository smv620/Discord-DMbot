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
from dataclasses import dataclass
from typing import Any

from dmbot.recaps.audio.script import PERFORMANCE_KIND, Script

MIN_SECONDS = 180
MAX_SECONDS = 600
MIN_SECTION_MS = 3000  # the provider's shortest section
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
    "final fantasy", "harry potter", "mozart", "beethoven", "disney", "marvel", "pixar",
)  # fmt: skip


def style_key(emotion: str) -> str:
    """The table entry an emotion cue uses ("" when it has none: the default style)."""
    word = re.sub(r"[^a-z ]", "", emotion.lower()).strip()
    word = SYNONYMS.get(word, word)
    return word if word in STYLES else ""


@dataclass(frozen=True, slots=True)
class Section:
    emotion: str
    start_char: int  # where it begins in the plain spoken text
    chars: int  # how many spoken characters it covers
    duration_ms: int


def total_seconds(spoken_chars: int) -> int:
    """How long the music is: as long as the voice is expected to be, within 3 to 10 minutes."""
    return int(min(MAX_SECONDS, max(MIN_SECONDS, round(spoken_chars / SPOKEN_CHARS_PER_SECOND))))


def sections(script: Script, *, seconds: int | None = None) -> list[Section]:
    """One section for each run of the same emotion cue, from the cue map. Text before the first
    cue is a calm opening. The durations add up to exactly the total, none under the provider's
    3 seconds."""
    spoken_len = max(1, len(script.spoken))
    total_ms = (seconds if seconds is not None else total_seconds(spoken_len)) * 1000
    total_ms = max(MIN_SECONDS * 1000, min(MAX_SECONDS * 1000, total_ms))
    marks: list[tuple[int, str]] = []
    for cue in script.emotions:
        if marks and marks[-1][1] == cue.name:
            continue  # the same mood again: one run
        marks.append((cue.offset, cue.name))
    if not marks or marks[0][0] > 0:
        marks.insert(0, (0, "calm"))
    while len(marks) > MAX_SECTIONS:  # too many moods: drop the shortest-lived last ones
        marks.pop()
    spans = []
    for i, (start, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else spoken_len
        spans.append((name, start, max(1, end - start)))
    # Proportional shares, none under the provider's 3 seconds; the rounding and the lifting of
    # short ones are settled on the longest section, so the sum is exactly the total.
    sum_w = sum(c for _, _, c in spans)
    base = [max(MIN_SECTION_MS, total_ms * c // sum_w) for _, _, c in spans]
    longest = max(range(len(base)), key=lambda i: base[i])
    base[longest] += total_ms - sum(base)
    if base[longest] < MIN_SECTION_MS:  # only when the sections can't all fit: fewer moods
        raise ValueError("too many moods for the length")
    return [
        Section(name, start, chars, ms)
        for (name, start, chars), ms in zip(spans, base, strict=True)
    ]


def composition_request(script: Script, *, seconds: int | None = None) -> dict[str, Any]:
    """The music request: a composition plan with one section for each mood, instrumental."""
    plan_sections = []
    for s in sections(script, seconds=seconds):
        pos, neg = STYLES.get(style_key(s.emotion), DEFAULT_STYLE)
        plan_sections.append(
            {
                "section_name": (style_key(s.emotion) or "calm").capitalize(),
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
    haystack = f"{script.spoken.lower()} {cue_text}"
    for kind, (words, _) in SCENES.items():
        found = [m.start() for w in words if (m := re.search(rf"\b{re.escape(w)}", haystack))]
        if found:
            hits.append((min(found), kind))
    hits.sort()
    return [Ambience(kind, SCENES[kind][1]) for _, kind in hits[:MAX_AMBIENCE]]
