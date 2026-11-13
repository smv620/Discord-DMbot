"""Scene moves in a session's DM lines (#4173 slice 4; the owner's change on 2026-11-13: "the check
of if the party is leaving to a new scene or location should not be keyworded, it should be LLM
processed", and Supervisor's cascade the same day). Pure: no Discord, no database, no network.

1. **Likely lines, by numbers we already have.** Each DM line's stored Voyage numbers are compared
   with fixed example sentences: moves of four kinds and near misses that are not moves. A line goes
   on when its best move example passes `THRESHOLD` and beats its best near miss by `MARGIN`
   (`likely`). Every example here is invented, never from a book.
2. **The AI decides.** The likely lines, each with the line before and after, go to one call
   (`SYSTEM`, `items_text`); the reply names each move, the place as said and the line it is on
   (`parse_moves`). A move is the bot's working state only: never shown, never a question.

The walk (`session_chain.walk`) applies a move only to a place it knows, and "left" only to a place
open on that chain: the AI's word never makes a place or opens a level by itself.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

ARRIVE, LEAVE, HEADING, TRAVEL, NONE = "arrive", "leave", "heading", "travel", "none"
KINDS = (ARRIVE, LEAVE, HEADING, TRAVEL, NONE)
PROMPT_VERSION = "scene-moves-1"
# Set by dev1's sample session so at least 95% of its key's moves pass (Supervisor's ruling on
# #4173); `devtools/scene_moves_tune.py` prints the numbers. Until then they are on the generous
# side: a line too many costs a little, a move dropped is a wrong place.
THRESHOLD = 0.45
MARGIN = 0.0
CONTEXT = 1  # lines either side of a likely line, sent with it
LINES_PER_CALL = 120  # likely lines in one call (each with its context); more: another call
PLACE_MAX = 80  # a place as the reply names it, at most

# About twenty of each, invented. Arriving somewhere.
ARRIVING = (
    "You step into the tavern.",
    "You arrive at the gates of the city.",
    "The party reaches the village at dusk.",
    "At last you come to the old mill.",
    "You walk into the temple.",
    "You find yourselves in the market square.",
    "The boat docks at the harbour.",
    "You enter the cave.",
    "You ride into town.",
    "After the climb you stand at the top of the tower.",
    "The door opens and you are inside the manor.",
    "You make it to the camp before nightfall.",
    "You push through the doors of the inn.",
    "You are now in the throne room.",
    "The cart rolls into the courtyard.",
    "You descend into the cellar.",
    "You come out into a wide cavern.",
    "You cross the bridge into the keep.",
    "Welcome to the city, says the guard as you pass in.",
    "You settle in at the lodge for the night.",
)
LEAVING = (
    "You leave the town behind.",
    "The party walks out of the inn.",
    "You step outside into the snow.",
    "You head out of the city gates.",
    "As you depart the village, the bells ring.",
    "You climb out of the cellar.",
    "You leave the temple at dawn.",
    "The ship sails out of the harbour.",
    "You exit the cave into daylight.",
    "You ride away from the farm.",
    "The tower falls behind you as you go.",
    "You slip out of the manor unseen.",
    "You get out of the mine before it collapses.",
    "You pack up and leave the camp.",
    "You walk away from the market.",
    "The town is soon out of sight.",
    "You leave through the back door.",
    "You flee the burning hall.",
    "You set off from the lodge.",
    "You put the city behind you.",
)
HEADING_FOR = (
    "The party sets out for the next town.",
    "You head towards the mountains.",
    "They leave for the coast.",
    "You start the long walk to the capital.",
    "You make your way to the temple.",
    "The road to the city lies ahead of you.",
    "You turn north, bound for the fortress.",
    "You decide to go to the harbour.",
    "You are on your way to the mines.",
    "Off you go to the market.",
    "The caravan departs for the south.",
    "You march towards the enemy camp.",
    "You sail for the islands.",
    "You follow the river down to the lake.",
    "You set course for the lighthouse.",
    "You hurry back to the inn.",
    "You take the pass towards the valley.",
    "You head home.",
    "You leave for the tower at first light.",
    "You start towards the ruins.",
)
TRAVELLING = (
    "Three days on the road pass without trouble.",
    "You travel through the wilds for a week.",
    "The journey takes most of the day.",
    "You walk for hours through the snow.",
    "The trail winds on through the forest.",
    "Days of travel wear you down.",
    "You camp by the road and move on at dawn.",
    "The ship is at sea for two days.",
    "You cross the open tundra.",
    "The wagon bumps along the frozen track.",
    "Night falls while you are still on the road.",
    "You trek across the hills.",
    "The weather turns as you travel.",
    "Mile after mile of ice stretches ahead.",
    "You ride hard through the night.",
    "After a long journey you are tired.",
    "The path climbs into the mountains.",
    "You make good time on the road.",
    "You keep moving through the storm.",
    "The miles pass in silence.",
)
NEAR_MISSES = (
    "She left the letter in the town hall.",
    "Remember when we were in the capital?",
    "My cousin lives in the city.",
    "He tells you he came from the coast.",
    "Years ago the town was much bigger.",
    "The innkeeper says the road is closed.",
    "You could go to the temple, or the market.",
    "A messenger from the city arrives.",
    "The map shows the river and the lake.",
    "She leaves a coin on the table.",
    "The guards walk past the tavern.",
    "In the story, the king rode to the sea.",
    "You remember the night you left home.",
    "If you went north you would reach the hills.",
    "The merchant left the town last week.",
    "The leaves fall from the trees.",
    "You ask where the mine is.",
    "He points towards the mountains.",
    "The town is famous for its fish.",
    "Let's take a short break before we go on.",
)
EXAMPLES: Mapping[str, Sequence[str]] = {
    ARRIVE: ARRIVING, LEAVE: LEAVING, HEADING: HEADING_FOR, TRAVEL: TRAVELLING,
}  # fmt: skip


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Dot product over the two lengths; 0 when either is empty (as `voyage.cosine`)."""
    if len(a) != len(b):
        return 0.0
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if not na or not nb:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)


def likely(
    line: Sequence[float],
    moves: Sequence[Sequence[float]],
    misses: Sequence[Sequence[float]],
    threshold: float = THRESHOLD,
    margin: float = MARGIN,
) -> bool:
    """Does this DM line look like a scene move? Its best move example passes the threshold and
    beats its best near miss by the margin."""
    best_move = max((cosine(line, m) for m in moves), default=0.0)
    best_miss = max((cosine(line, m) for m in misses), default=0.0)
    return best_move >= threshold and best_move - best_miss >= margin


def with_context(likely_numbers: Collection[int], all_numbers: Sequence[int]) -> list[int]:
    """The likely lines with the DM line before and after each, in order, once each."""
    keep: set[int] = set()
    for i, n in enumerate(all_numbers):
        if n in likely_numbers:
            keep.update(all_numbers[max(0, i - CONTEXT) : i + CONTEXT + 1])
    return [n for n in all_numbers if n in keep]


SYSTEM = """You read lines a Dungeon Master said during a tabletop game session. For each line \
marked ASK, say whether it moves the players' party (or the group being narrated) to a new scene \
or place. The lines marked CONTEXT only help you understand; never answer about them.

Kinds of move:
- "arrive": the party is now at or in a place ("You step into the Gull's Rest").
- "leave": the party leaves a place it was at ("You leave Kelvenmoor behind").
- "heading": the party sets out for a place and is on the way, not there yet ("They left for \
Brindleford", "You head to the old mill"). "Left for X" is heading for X, never leaving X.
- "travel": the party is on the road between places, no place named ("Three days on the road").
- "none": not a move. Use it when a place is named but nobody goes anywhere: something left \
somewhere ("She left the letter in Kelvenmoor"), a memory or story ("Remember when you were in \
Brindleford?"), someone else's trip, a plan not carried out, a messenger from somewhere.

Answer with JSON only: {"moves": [{"line": <number>, "kind": "<kind>", "place": "<the place as \
said, or empty>"}]}. One entry for each ASK line that is a move or names a place; leave out ASK \
lines that are neither. Never make up a place: copy it as the line says it."""


def items_text(lines: Sequence[tuple[int, str, bool]]) -> str:
    """The lines for one call: (number, words, asked). Asked lines are marked ASK, the others
    CONTEXT."""
    return "\n".join(
        f"{n} {'ASK' if asked else 'CONTEXT'}: {' '.join(text.split())}" for n, text, asked in lines
    )


@dataclass(frozen=True, slots=True)
class Move:
    line: int
    kind: str
    place: str


@dataclass(frozen=True, slots=True)
class Parsed:
    moves: list[Move]
    stopped_early: bool = False  # (the reply was cut: what came before the cut is kept)
    unreadable: bool = False  # (no JSON at all: nothing found is not "nothing there")


_ITEM = re.compile(r"\{[^{}]*\}")


def parse_moves(text: str, asked: Collection[int], *, cut: bool = False) -> Parsed:
    """The moves in a reply, kept only for lines that were asked about, of a known kind, with a
    place that reads as a name (never instructions). A reply cut short keeps the whole items before
    the cut (CLAUDE.md: a cut answer is reported as stopped early, never as nothing found)."""
    raw: list[object]
    try:
        data = json.loads(text)
        raw = list(data.get("moves", [])) if isinstance(data, dict) else []
        stopped = False
    except (ValueError, TypeError):
        if not cut:
            return Parsed([], unreadable=True)
        raw, stopped = [], True
        for match in _ITEM.finditer(text):
            try:
                raw.append(json.loads(match.group(0)))
            except ValueError:
                continue
    out: list[Move] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        line, kind, place = item.get("line"), item.get("kind"), item.get("place", "")
        if type(line) is not int or line not in asked or kind not in KINDS:
            continue
        place = " ".join(str(place or "").split())[:PLACE_MAX]
        if place and not re.fullmatch(r"[\w'’ ,.-]+", place):
            continue  # (not a name: never followed)
        if kind in (ARRIVE, LEAVE, HEADING) and not place:
            continue  # (these need the place they name)
        out.append(Move(line, str(kind), place))
    return Parsed(out, stopped_early=stopped)
