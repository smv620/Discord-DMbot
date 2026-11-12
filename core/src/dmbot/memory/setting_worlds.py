"""Where a campaign's story is set, worked out from its shared adventure (#4173 slice 2b; the
owner's answer and Supervisor's ruling on #4173, 2026-11-11 and 2026-11-12). A fixed list of names
only: each published world's own name, the place DMbot makes the setting, and the titles of
published adventures set there. Never a description, a place inside a world, a map or any text
from the books (Supervisor's condition 1: no built-in gazetteer of a published setting), and never
shown on the website as content. Pure: no Discord, no database."""

from __future__ import annotations

import re
import unicodedata
from typing import NamedTuple


class World(NamedTuple):
    key: str
    label: str  # the world's own name, what a DM says ("Forgotten Realms")
    place: str  # the place DMbot makes the campaign's setting ("Faerûn")
    description: str  # the menu's second line (the ux-critic on #4173)


WORLDS: tuple[World, ...] = (
    World("forgotten_realms", "Forgotten Realms", "Faerûn", "Faerûn"),
    World("eberron", "Eberron", "Khorvaire", "Khorvaire"),
    World("greyhawk", "Greyhawk", "the Flanaess", "The Flanaess"),
    World("dragonlance", "Dragonlance", "Ansalon", "Ansalon"),
    World("exandria", "Exandria", "Exandria", "Critical Role's world"),
    World("ravenloft", "Ravenloft", "the Domains of Dread", "The Domains of Dread"),
)
BY_KEY = {w.key: w for w in WORLDS}

# Published adventures, by title, and the world each is set in. Titles only.
ADVENTURES: dict[str, str] = {
    "rime of the frostmaiden": "forgotten_realms",
    "lost mine of phandelver": "forgotten_realms",
    "dragon of icespire peak": "forgotten_realms",
    "storm king's thunder": "forgotten_realms",
    "princes of the apocalypse": "forgotten_realms",
    "out of the abyss": "forgotten_realms",
    "tomb of annihilation": "forgotten_realms",
    "waterdeep dragon heist": "forgotten_realms",
    "waterdeep dungeon of the mad mage": "forgotten_realms",
    "baldur's gate descent into avernus": "forgotten_realms",
    "hoard of the dragon queen": "forgotten_realms",
    "the rise of tiamat": "forgotten_realms",
    "phandelver and below": "forgotten_realms",
    "curse of strahd": "ravenloft",
    "eberron rising from the last war": "eberron",
    "dragonlance shadow of the dragon queen": "dragonlance",
    "explorer's guide to wildemount": "exandria",
    "call of the netherdeep": "exandria",
    "ghosts of saltmarsh": "greyhawk",
}
# A world's own name in the adventure's opening words ("an adventure for the Forgotten Realms")
_NAMED: dict[str, str] = {
    "forgotten realms": "forgotten_realms",
    "eberron": "eberron",
    "greyhawk": "greyhawk",
    "dragonlance": "dragonlance",
    "exandria": "exandria",
    "ravenloft": "ravenloft",
}
OPENING_CHARS = 4000  # the title page and introduction; never the whole book


def _plain(text: str) -> str:
    """Lower case, accents and punctuation gone, one space between words: "Baldur's Gate:
    Descent into Avernus.pdf" and "baldurs gate descent into avernus" match."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = re.sub(r"\.(pdf|docx?|txt|md|epub|html?)$", "", text.strip())
    text = text.replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


_TITLES = {_plain(t): w for t, w in ADVENTURES.items()}
_NAMES = {_plain(n): w for n, w in _NAMED.items()}


def world_for(file_name: str, opening: str = "") -> World | None:
    """The world an adventure is set in, in the ruled order: its file's name against the list of
    published titles (the whole title somewhere in the name: "Rime of the Frostmaiden (2020).pdf"),
    then a world's own name in its opening words. Two different worlds named in the opening say
    nothing (a book about travel between worlds), and so does nothing found: no guess."""
    name = f" {_plain(file_name)} "
    for title, world in sorted(_TITLES.items(), key=lambda kv: -len(kv[0])):
        if f" {title} " in name:
            return BY_KEY[world]
    words = f" {_plain(opening[:OPENING_CHARS])} "
    found = {world for named, world in _NAMES.items() if f" {named} " in words}
    return BY_KEY[found.pop()] if len(found) == 1 else None
