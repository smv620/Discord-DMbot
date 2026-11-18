"""The weather a session described, for the after-session weather note (#4260 slice 10c-2;
Supervisor's ruling A on #4229, given to #4260 too). Pure: no database, no AI call, nothing written.

The session reader is asked for it in the same call as its claims (`WEATHER_ADDENDUM`, so the switch
costs no extra call). Only the three kinds the note judges are read (`weather_note.KINDS`: snow, a
hard frost, heat): rain, fog and wind fit every climate and are never asked for. What holds is
checked against the place's kept climate by `weather_note.check`, and a note goes to the DM alone,
after the session. Nothing is stored: weather is never stored and DMbot never makes any (design §6).
Behind DMBOT_WEATHER_NOTES (off) until dev1's counts on the invented `weather_cases` text.

Rules held in code, not trust:
- **Only the DM's narration.** Every item cites a line the DM spoke: a player's guess ("it'll snow
  tomorrow") never counts.
- **Said as weather that is there, in the line.** One of the kind's own words is in a cited DM line,
  and not negated before or after it ("no snow", "doesn't snow", "snow never falls"), not asked
  (with or without a "?"), not to come or only supposed ("will snow", "if it snows"), and not a
  spell or a figure of speech ("Scorching Ray", "snow-white", "a frosty glare"). The weather
  cited is the DM line that held, never a player's cited beside it.
- **A named place, in the batch.** The place is named in the lines read; "here" or "outside" is no
  place, and the caller matches the name to a kept place or drops it.
- **The transcript is data.** Words that read like an instruction are refused.

Counts and line numbers only leave this module; the words of a transcript are never logged.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from dmbot.maps.weather_note import FROST, HEAT, SNOW
from dmbot.memory.claims import LINES_MAX, reads_like_instruction
from dmbot.memory.models import NAME_MAX
from dmbot.memory.readings import PRONOUNS

MAX_PER_ANSWER = 10
PROMPT_SUFFIX = "+weather-1"  # added to the claims' prompt version when the addendum is sent

WEATHER_ADDENDUM = """

Also list the weather the DM describes in a named place in this stretch, but only snow, frost or
heat: snow falling or lying, a hard frost or frozen ground, or great heat. Only what the DM's own
lines describe as happening; never a player's guess, a forecast, a question, weather that is not
there ("no snow"), or a figure of speech ("cold as ice").
Give them as "weather", beside "claims":
{"claims": [...], "weather": [{"place": "Frostmere", "kind": "snow", "lines": [1]}]}
"place" is the place's name as said; "kind" is "snow", "frost" or "heat". Give "weather": [] if
there is none."""

_LIST_START = re.compile(r'\s*\{\s*"claims"\s*:')
# Each kind's own words: what a DM line must hold for the item to be heard as that weather
_WORDS = {
    SNOW: re.compile(r"\b(?:snow\w*|blizzards?|sleet)\b", re.I),
    FROST: re.compile(
        r"\b(?:frost\w*|hoarfrost|rime|frozen\s+(?:ground|river|lake|puddles?|earth|fields?)"
        r"|freezing\s+(?:cold|night|wind|air|fog))\b",
        re.I,
    ),
    HEAT: re.compile(
        r"\b(?:heat\s*waves?|heatwaves?|scorching|sweltering|blistering|baking\s+heat|the\s+heat)\b",
        re.I,
    ),
}
# A negation shortly before the word: "no snow", "doesn't snow", "hasn't seen snow"
_NEGATED = re.compile(
    r"\b(?:no|not|never|nor|none|without|hardly|rarely|seldom|\w+n't|\w+n’t)\W+(?:[\w'’-]+\W+){0,3}$",
    re.I,
)
# ... or after it, in the same clause: "snow never falls", "snow is nowhere to be seen"
_NEGATED_AFTER = re.compile(r"\b(?:never|nowhere|no\s+longer|not)\b", re.I)
# Weather to come, or only supposed: never weather that is there
_NOT_YET = re.compile(
    r"\b(?:will|won't|going\s+to|coming|tomorrow|tonight|next|soon|if|unless|might|could|would"
    r"|may|should|forecast\w*|expect\w*|threaten\w*)\b",
    re.I,
)
# A question with no "?" (speech to text often drops it): the line opens like one
_ASKED = re.compile(r"^\W*(?:is|are|was|were|does|do|did|can|could|will|would|has|have)\b", re.I)
# Spells and figures of speech that hold a kind's word but are no weather
_NOT_WEATHER = re.compile(
    r"\b(?:scorching\s+ray|sleet\s+storm|snowball|snow[\s-]white|heat\s+of\s+(?:the|battle|his"
    r"|her|their)|heat\s+metal|blistering\s+(?:pace|speed|welcome|attack|insult|retort)"
    r"|frosty\s+(?:glare|reception|look|stare|welcome|tone|silence|smile)|cone\s+of\s+cold"
    r"|ice\s+storm|white\s+as\s+snow)\b",
    re.I,
)
_NO_PLACE = frozenset({"here", "there", "outside", "inside", "the town", "the city", "the road"})


@dataclass(frozen=True, slots=True)
class Weather:
    """One kind of weather the DM described in a named place: the place as said, the kind, and the
    cited lines' numbers. No words of the transcript."""

    place: str
    kind: str
    lines: tuple[str, ...]


@dataclass(slots=True)
class Parsed:
    weather: list[Weather] = field(default_factory=list)
    refused: int = 0
    dropped: dict[str, int] = field(default_factory=dict)

    def drop(self, why: str) -> None:
        self.dropped[why] = self.dropped.get(why, 0) + 1
        self.refused += 1


def check(place: str, kind: str, line: str, said: str) -> str:
    """Why this weather is refused, or "" when it holds (pure; the rules above). `line`: the cited
    DM line; `said`: every line of the batch, where the place must be named."""
    name = " ".join(place.split())
    if (
        not name
        or len(name) > NAME_MAX
        or name.casefold() in PRONOUNS
        or name.casefold() in _NO_PLACE
        or re.search(rf"\b{re.escape(name)}\b", said, re.IGNORECASE) is None
    ):
        return "weather_place"
    words = _WORDS.get(kind)
    if words is None:
        return "weather_kind"
    # (the place's own name never counts as weather: "Frostmere" holds "frost")
    line = re.sub(rf"\b{re.escape(name)}\b", lambda m: " " * len(m.group()), line, flags=re.I)
    if _NOT_WEATHER.search(line):
        return "weather_figure"  # (a spell or a figure of speech: "Scorching Ray", "snow-white")
    m = words.search(line)
    if m is None:
        return "weather_not_in_line"
    starts = max(line.rfind(c, 0, m.start()) for c in ".!?;:") + 1
    ends_at = re.search(r"[.?!:;]", line[m.end() :])
    clause = line[starts : m.end() + (ends_at.start() if ends_at else len(line))]
    before = line[starts : m.start()]
    if _NEGATED.search(before) or _NEGATED_AFTER.search(
        line[m.end() : m.end() + (ends_at.start() if ends_at else 999)]
    ):
        return "weather_not_there"
    if (ends_at is not None and ends_at.group() == "?") or _ASKED.search(clause):
        return "weather_asked"
    if _NOT_YET.search(clause):
        return "weather_not_yet"
    return ""


def parse_weather(answer: str, lines: Mapping[str, str], dm_lines: Iterable[str]) -> Parsed:
    """The weather in a claims answer that carries the addendum, each item checked against the
    cited lines (`lines`: the batch's numbers to their words; `dm_lines`: the numbers the DM
    spoke). Malformed parts are refused and counted; the same place and kind twice is one."""
    out = Parsed()
    dm = set(dm_lines)
    text = answer.strip()
    start = _LIST_START.search(text)
    try:
        data: Any = json.loads(text[start.start() :] if start else text)
    except (json.JSONDecodeError, ValueError):
        return out
    items = data.get("weather") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return out
    said = "\n".join(lines.values())
    seen: set[tuple[str, str]] = set()
    if len(items) > MAX_PER_ANSWER * 2:
        out.dropped["weather_too_many"] = len(items) - MAX_PER_ANSWER * 2
        out.refused += len(items) - MAX_PER_ANSWER * 2
    for item in items[: MAX_PER_ANSWER * 2]:
        if len(out.weather) >= MAX_PER_ANSWER:
            out.drop("weather_too_many")
            continue
        if not isinstance(item, dict):
            out.drop("weather_malformed")
            continue
        place, kind = str(item.get("place") or ""), str(item.get("kind") or "").casefold()
        cited = item.get("lines")
        numbers = (
            tuple(str(n) for n in cited[:LINES_MAX] if str(n) in lines)
            if isinstance(cited, list)
            else ()
        )
        if not numbers:
            out.drop("weather_lines")
            continue
        if not any(n in dm for n in numbers):
            out.drop("weather_not_dm")
            continue
        if reads_like_instruction(place):
            out.drop("weather_instruction")
            continue
        why, held = "weather_not_in_line", ""
        for n in numbers:
            if n in dm:
                why = check(place, kind, lines[n], said)
                if not why:
                    held = n
                    break
        if not held:
            out.drop(why)
            continue
        key = (" ".join(place.split()).casefold(), kind)
        if key in seen:
            continue
        seen.add(key)
        out.weather.append(Weather(" ".join(place.split()), kind, (held,)))
    return out


def once_each(found: Iterable[Weather]) -> list[Weather]:
    """One item per (place, kind) across a session's batches, the first kept."""
    out: list[Weather] = []
    seen: set[tuple[str, str]] = set()
    for w in found:
        key = (w.place.casefold(), w.kind)
        if key not in seen:
            seen.add(key)
            out.append(w)
    return out
