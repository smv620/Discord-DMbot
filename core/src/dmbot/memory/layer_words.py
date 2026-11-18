"""A place's layer rows in plain words (#4256, #4257): one wording for the place card, World notes
and the recap. Moved out of `ui/place_world` for #4260 (slice 10b) so a reader with no Discord, such
as the recap's colour, says them exactly as the card does."""

from __future__ import annotations

from dmbot.memory import map_facts as mf
from dmbot.memory import place_peoples

STRENGTH = {"strong": "strongly", "some": "somewhat", "traces": "a little", "once": "once"}

LAND: dict[str, tuple[str, str]] = {
    "arctic": ("Ice and snow", "Arctic, glaciers, tundra"),
    "coastal": ("Coast", "Shore, beach, sea cliffs"),
    "desert": ("Desert", "Sand or bare rock"),
    "forest": ("Forest", "Woods or jungle"),
    "grassland": ("Grassland", "Plains, farms, meadows"),
    "hill": ("Hills", "Rolling hills, moors"),
    "mountain": ("Mountains", "Peaks and passes"),
    "swamp": ("Swamp", "Marsh, bog, wetland"),
    "underdark": ("Underdark", "Deep caves below the world"),
    "urban": ("Town or city", "Streets and buildings"),
    "waterborne": ("Open water", "Travel by boat or ship"),
}
WARMTH = {"arctic": "Freezing", "cold": "Cold", "temperate": "Mild", "warm": "Warm", "hot": "Hot"}
RULED = {"rules": "Ruled by", "claims": "Claimed by", "occupies": "Taken over by",
         "protects": "Protected by"}  # fmt: skip


def land_words(f: mf.Fact) -> str:
    word = f.words if f.value == mf.OTHER else LAND.get(f.value, (f.value,))[0].lower()
    return f"partly {word}" if f.how == "partly" else word


def climate_words(f: mf.Fact) -> str:
    word = f.words if f.value == mf.OTHER else WARMTH.get(f.value, f.value).lower()
    return f"{word} and {f.how}" if f.how in ("dry", "wet") else word


def culture_words(f: mf.Fact) -> str:
    """ "strongly dwarven", or "dwarven (in the dwarven quarter)" for part of the place."""
    word = place_peoples.words_of(f.value, f.words, plural=False)
    said = f"{STRENGTH[f.how]} {word}" if f.how in STRENGTH else word
    return f"{said} (in {f.part})" if f.part else said


def people_words(f: mf.Fact) -> str:
    """ "mostly halflings", or "halflings" when no share was said."""
    word = place_peoples.words_of(f.value, f.words, plural=True)
    share = place_peoples.HOW_WORDS.get(f.how, "")
    return f"{share} {word}" if share else word
