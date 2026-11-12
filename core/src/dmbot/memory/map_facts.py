"""A place's land, climate and who rules it (#4256, MapBot layers slice 2; design
`docs/design/mapbot_layers.md` §5 to §8 and Supervisor's ruling on #4256): the fixed word lists,
the set phrases the text readers know, and reading a value up the place tree. Pure: no Discord, no
database. The store keeps the rows (`memory_map_facts`); every read of them goes through
`MemoryStore.map_facts`, which applies the players' rule (§8.3)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

from dmbot.memory import place_levels

TERRAIN, CLIMATE, CONTROL = "terrain", "climate", "control"
LAYERS = (TERRAIN, CLIMATE, CONTROL)
OTHER = "other"  # a value off the list, kept with the text's own words
WORDS_MAX = 60  # the text's own words for "other", and a part ("the northern half")
READ, CONFIRMED, REJECTED = "read", "confirmed", "rejected"
FROM_DM, FROM_MAP, FROM_TEXT = "dm", "map", "text"
RANK = {FROM_TEXT: 0, FROM_MAP: 1, FROM_DM: 2}  # precedence (§7.1): the DM > a map > the text

# The fixed lists (§6, §13.6). Terrain is the SRD 5.2.1's Travel Terrain table, which the rules
# index already holds (`rules/data/srd52/toolbox.json`), so a travel answer can read its pace.
VALUES: dict[str, tuple[str, ...]] = {
    TERRAIN: ("arctic", "coastal", "desert", "forest", "grassland", "hill", "mountain", "swamp",
              "underdark", "urban", "waterborne"),
    CLIMATE: ("arctic", "cold", "temperate", "warm", "hot"),
    CONTROL: (),  # a faction entry, never a word
}  # fmt: skip
# The value's shade (`how`): '' is none. Climate's dry or wet lives here (Supervisor, point 1).
HOWS: dict[str, tuple[str, ...]] = {
    TERRAIN: ("mostly", "partly"),
    CLIMATE: ("dry", "wet"),
    CONTROL: ("rules", "claims", "occupies", "protects"),
}
# A different value means (§6): terrain was wrong; climate and control can change in the story.
CORRECTED = frozenset({TERRAIN})


def valid(layer: str, value: str, how: str = "") -> bool:
    """Is this a value (or "other") and a shade the layer allows? Control's value is a faction, so
    only its `how` is checked."""
    if layer not in LAYERS or (how and how not in HOWS[layer]):
        return False
    return layer == CONTROL or value == OTHER or value in VALUES[layer]


def allowed_on(layer: str, *, form: str | None, level: str | None) -> bool:
    """May this place carry this layer? Terrain only on land (not water, not a road: 6a's form),
    climate only on a region or bigger, control on any place (Supervisor's ruling on #4256). A
    place whose size nobody knows may have a climate: refusing would lose what the DM says."""
    if layer == TERRAIN:
        return form is None
    if layer == CLIMATE:
        return level is None or place_levels.RANK[level] <= place_levels.RANK["region"]
    return layer == CONTROL


# ---- The set phrases the text readers know (§7.2; Supervisor's point 3: short on purpose, and each
# later addition scored on the sample). A missing row is the safe side.

# who rules it: the phrase, and the `how` it gives
CONTROL_PHRASES: dict[str, str] = {
    "ruled by": "rules", "held by": "rules", "under the banner of": "rules",
    "occupied by": "occupies",
}  # fmt: skip
# A land or climate word right beside a place's name. The word decides the layer.
WORD_FACTS: dict[str, tuple[str, str]] = {
    "frozen": (CLIMATE, "arctic"), "icy": (CLIMATE, "arctic"), "snowy": (CLIMATE, "cold"),
    "arctic": (CLIMATE, "arctic"), "temperate": (CLIMATE, "temperate"),
    "tropical": (CLIMATE, "hot"), "sweltering": (CLIMATE, "hot"),
    "desert": (TERRAIN, "desert"), "sandy": (TERRAIN, "desert"),
    "wooded": (TERRAIN, "forest"), "forested": (TERRAIN, "forest"),
    "marshy": (TERRAIN, "swamp"), "swampy": (TERRAIN, "swamp"),
    "mountainous": (TERRAIN, "mountain"), "hilly": (TERRAIN, "hill"),
    "coastal": (TERRAIN, "coastal"),
}  # fmt: skip
_SENTENCE_END = re.compile(r"[.!?;:\n]")
_ARTICLES = ("the", "a", "an")


class Found(NamedTuple):
    """One fact a reader found beside a name: its layer, value (the faction's name for control) and
    shade."""

    layer: str
    value: str
    how: str = ""


def _bare(word: str) -> str:
    return re.sub(r"[^\w'-]", "", word).casefold()


def _sentence_of(name: str, text: str) -> tuple[str, str] | None:
    """(the words before the name, the words after it), within its own sentence, at its first whole
    mention; None when the name is not in the text."""
    if not name.strip() or not text:
        return None
    found = re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.IGNORECASE)
    if found is None:
        return None
    before = _SENTENCE_END.split(text[max(0, found.start() - 120) : found.start()])[-1]
    after = _SENTENCE_END.split(text[found.end() : found.end() + 160])[0]
    return before, after


def read_facts(name: str, text: str, factions: Iterable[str]) -> list[Found]:
    """What the words right beside a place's name say, in three set shapes only:
    - "Bryn Shander, ruled by the Council of Speakers" or "Luskan … held by the Arcane
      Brotherhood": control, when what follows the phrase is one of `factions` (names, matched
      whole and in any case);
    - "the frozen north of Icewind Dale" or "the desert kingdom of Calimshan": a word from
      `WORD_FACTS` in the words just before "of";
    - "Icewind Dale, a frozen land": the same word right after the comma and its article.
    Anything else says nothing, a phrase further on in the sentence included. One fact per layer,
    the first found."""
    around = _sentence_of(name, text)
    if around is None:
        return []
    before, after = around
    out: dict[str, Found] = {}
    tail = " ".join(after.split()).casefold()
    for phrase, how in CONTROL_PHRASES.items():
        # right after the name: "Bryn Shander, ruled by …" or "Bryn Shander is held by …", never
        # a phrase about something later in the sentence ("… traded with Luskan, ruled by …")
        at = re.match(r"\s*,?\s*(?:(?:is|was|now)\s+)?" + re.escape(phrase) + r"\s+", tail)
        if at is None:
            continue
        rest = tail[at.end() :]
        for faction in sorted(factions, key=len, reverse=True):  # (the longest name first)
            f = " ".join(faction.split()).casefold()
            for lead in ("", "the "):
                if f and rest.startswith(lead + f) and not rest[len(lead + f) :][:1].isalnum():
                    out.setdefault(CONTROL, Found(CONTROL, faction, how))
        if CONTROL in out:
            break
    back = [w for w in (_bare(w) for w in before.split()[::-1]) if w]  # nearest first
    # "the frozen north of", "the desert kingdom of": an article, the word, one noun, then "of"
    # (so "a wooded hill east of Phandalin" places the hill, never Phandalin)
    if len(back) >= 4 and back[0] == "of" and back[2] in WORD_FACTS and back[3] in _ARTICLES:
        layer, value = WORD_FACTS[back[2]]
        out.setdefault(layer, Found(layer, value))
    if after.startswith(","):  # "Icewind Dale, a frozen land"
        ahead = [w for w in (_bare(w) for w in after[1:].split()) if w][:3]
        if len(ahead) >= 2 and ahead[0] in _ARTICLES and ahead[1] in WORD_FACTS:
            layer, value = WORD_FACTS[ahead[1]]
            out.setdefault(layer, Found(layer, value))
    return [out[k] for k in LAYERS if k in out]


# ---- Reading up the place tree (§6): terrain, climate and control only, never stored.


class Fact(NamedTuple):
    """One open row, as the card and the answers use it."""

    id: str
    place_id: str
    layer: str
    value: str  # the word, or the faction's id for control
    words: str  # the text's own words for "other"
    how: str
    part: str
    status: str
    source: str
    table_knows: bool


class Held(NamedTuple):
    """A layer's value for a place: its own facts, or a bigger place's (`from_id`), read up."""

    facts: tuple[Fact, ...]
    from_id: str | None = None  # None: the place's own


def read_up(
    place_id: str,
    layer: str,
    own: Mapping[str, Sequence[Fact]],
    inside: Mapping[str, Sequence[str]],
    levels: Mapping[str, str],
) -> Held | None:
    """The place's own facts in this layer, or else the nearest bigger place's: one step up the
    tree at a time (`inside`: a place to the places it is in), so Bryn Shander takes Ten-Towns'
    before Icewind Dale's. Climate is never read from a plane or across one (Supervisor's ruling:
    a demiplane's weather is not the Material Plane's). A loop in the data can't hang it. None:
    nothing on the way up."""
    mine = tuple(f for f in own.get(place_id, ()) if f.layer == layer)
    if mine:
        return Held(mine)
    seen = {place_id}
    step = [place_id]
    while step:
        nxt: list[str] = []
        for p in step:
            for outer in sorted(inside.get(p, ())):
                if outer in seen:
                    continue
                seen.add(outer)
                if layer == CLIMATE and levels.get(outer) == "plane":
                    continue  # (and not past it)
                theirs = tuple(f for f in own.get(outer, ()) if f.layer == layer)
                if theirs:
                    return Held(theirs, outer)
                nxt.append(outer)
        step = nxt
    return None


# ---- When a new statement meets an open row (§7.3)


KEEP, CORRECT, CLOSE, NOTE = "keep", "correct", "close", "note"


def meet(layer: str, old_source: str, old_status: str, new_source: str, changed: bool) -> str:
    """What a new value does to a different open one in the same layer and part:
    - CORRECT: the old was wrong (terrain from a higher source; any layer from a higher source with
      no words of change): it becomes rejected;
    - CLOSE: the story changed it (words of change, "fell to"): the old closes as history;
    - NOTE: the same or a lower source, or against the DM's own row: nothing changes, and it is
      counted for World notes (#4285).
    Never an alert or a question during play."""
    if old_status == CONFIRMED and new_source != FROM_DM:
        return NOTE  # (the DM's own row stands against everything but the DM)
    if layer in CORRECTED:
        return CORRECT if RANK[new_source] > RANK[old_source] or new_source == FROM_DM else NOTE
    if changed:  # (no reader says words of change yet: slice 2's phrases never do)
        return CLOSE
    return CORRECT if RANK[new_source] > RANK[old_source] or new_source == FROM_DM else NOTE
