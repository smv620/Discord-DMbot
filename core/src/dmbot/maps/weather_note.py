"""The after-session weather note (#4260, MapBot layers slice 10c; docs/design/mapbot_layers.md §6
"Climate" and §9 "After the session"; Supervisor's ruling on #4260). Pure: no database, no Discord,
no AI. It says only whether weather the session described in a place sits badly with that place's
climate (and the season, when the campaign's calendar gives one), and the DM's private words for
it. Nothing is stored or changed: §6 says weather is never stored and DMbot never makes any.

Rules kept here:
- **The DM's only, private, after the session**, never during a scene and never as a correction
  (the ruling): a suggestion with its working, which the DM may ignore. Magic and story turns are
  the DM's to make, so the words say so.
- **Few false notes before many notes** (§13, rule 15: false notes are scored on the sample):
  only weather that plainly cannot fit is noted. Rain, fog, wind and storms fit everywhere and are
  never noted; only snow, a hard frost and heat are. Without a season only the always-wrong pairs
  are noted (heat in an arctic climate, snow in a hot one).
- **Seasons** are the calendar's, in these words (`SEASONS`, northern seasons assumed); the
  calendar gives none yet (`timebot/calendar.py`), so until it does, and for any other word, no
  season is known and only the always-wrong pairs are noted.
- **Only the DM's kept climate counts:** a climate DMbot read and the DM hasn't kept is a guess,
  and a note built on a guess is a false note.
- **Anything unclear is silence:** two kept climates that differ (one read up from the region, one
  of its own, or one that started at a point in the story, §6), a climate for only part of the
  place, or "other" in the DM's words: no note.

Not wired yet: the session reader of weather (an AI read) follows once AI calls are possible, with
its false-note count on the sample; the switch comes with it. For the wiring (the ux-critic): one
note per place and kind until that place's climate changes, and one session's notes in one
message, a line each, under one "🌦️ Weather check" heading.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from dmbot.memory import map_facts as mf

# The weather a session can describe that a climate can rule out. Anything else is never noted.
SNOW, FROST, HEAT = "snow", "frost", "heat"
KINDS = (SNOW, FROST, HEAT)
SEASONS = ("winter", "spring", "summer", "autumn")

# How each kind is said in the note (the session's own words are never repeated: it is a note
# about weather, not a transcript).
SAID = {SNOW: "snow", FROST: "a hard frost", HEAT: "hot weather"}
# A climate in the note's words (the place card's "Freezing", "Cold", …, lower case).
CLIMATE_SAID = {
    "arctic": "freezing",
    "cold": "cold",
    "temperate": "mild",
    "warm": "warm",
    "hot": "hot",
}

# Weather that never fits a climate, whatever the season.
_NEVER: frozenset[tuple[str, str]] = frozenset({("arctic", HEAT), ("hot", SNOW)})
# Weather that fits a climate only in the seasons listed (checked only when the season is known).
_ONLY_IN: dict[tuple[str, str], frozenset[str]] = {
    ("cold", HEAT): frozenset({"summer"}),
    ("temperate", HEAT): frozenset({"spring", "summer", "autumn"}),
    ("temperate", SNOW): frozenset({"winter", "spring", "autumn"}),
    ("temperate", FROST): frozenset({"winter", "spring", "autumn"}),
    ("warm", SNOW): frozenset({"winter"}),
    ("warm", FROST): frozenset({"winter", "spring", "autumn"}),  # (Mediterranean frosts)
    ("hot", FROST): frozenset({"winter"}),  # (a hot desert's winter nights freeze)
}


def fits(climate: str, kind: str, season: str | None) -> bool:
    """Whether weather of `kind` can happen in `climate` (in `season`, when known). Unknown words
    always fit: a missing rule is the safe side."""
    if (climate, kind) in _NEVER:
        return False
    allowed = _ONLY_IN.get((climate, kind))
    return allowed is None or season is None or season in allowed


@dataclass(frozen=True)
class Note:
    place: str  # the place's name, as the DM knows it
    kind: str  # SNOW, FROST or HEAT
    climate: str  # the climate it doesn't fit (`mf` value)
    season: str | None  # the calendar's season, when it gave one and the season decided it
    from_place: str | None  # where the climate was read from, when read up the tree


def check(
    place: str,
    kind: str,
    climates: Sequence[tuple[mf.Fact, str | None]],
    season: str | None = None,
) -> Note | None:
    """A note when weather of `kind` said in `place` plainly can't fit its kept climate, else None.
    `climates`: the place's climate rows, each with the name of the place it was read up from (None
    for its own). Rows the DM hasn't kept never count. No note (the safe side) when any kept row is
    one the note can't judge: "other" in the DM's own words, a climate for only part of the place,
    or a value it has no words for; nor when two kept rows give different climates, since DMbot
    doesn't guess which one the session meant."""
    if kind not in KINDS:
        return None
    season = season if season in SEASONS else None
    kept = [(f, src) for f, src in climates if f.layer == mf.CLIMATE and f.status == mf.CONFIRMED]
    if not kept or any(f.value not in CLIMATE_SAID or f.part.strip() for f, _src in kept):
        return None
    if len({f.value for f, _src in kept}) > 1:
        return None
    fact, src = kept[0]
    if fits(fact.value, kind, season):
        return None
    # (the season is said only when it decided the note: heat in an arctic climate needs none)
    decided = season if (fact.value, kind) not in _NEVER else None
    return Note(place, kind, fact.value, decided, src)


def words(note: Note) -> str:
    """The DM's private note: the weather and the place first, why it may not fit, and that the DM
    may ignore it. One line (the ux-critic's wording: "there", never "<place>'s", which reads badly
    after names like Baldur's Gate)."""
    climate = CLIMATE_SAID[note.climate]
    where = (
        f"it's in {note.from_place}, whose climate is {climate}"
        if note.from_place
        else f"the climate there is {climate}"
    )
    season = f", and it's {note.season} there by your calendar" if note.season else ""
    return (
        f"🌦️ Weather check: {SAID[note.kind]} in {note.place} this session, but {where}{season}. "
        "If that's on purpose, like magic or a story turn, you can ignore this."
    )
