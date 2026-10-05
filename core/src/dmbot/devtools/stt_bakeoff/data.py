"""The bake-off script, its names, and the word lists built from them.

Mirrors docs/test-scripts/stt-bakeoff.md (a test checks they match).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

GAME, TRAP, OFF_TOPIC, EVERYDAY = "game", "trap", "off_topic", "everyday"


@dataclass(frozen=True, slots=True)
class Name:
    canonical: str
    variants: tuple[str, ...] = ()
    sounds_like: tuple[str, ...] = ()
    # Also an ordinary English word ("bell"): never counted as a false name.
    is_word: bool = False

    @property
    def forms(self) -> tuple[str, ...]:
        return (self.canonical, *self.variants)


@dataclass(frozen=True, slots=True)
class Line:
    n: int
    text: str
    category: str
    names: tuple[str, ...] = ()  # canonical names said in this line
    rules: tuple[str, ...] = ()  # rules words said in this line


NAMES: tuple[Name, ...] = (
    Name("Cerric", ("Ceric",), ("serik", "sair ick")),
    Name("Belleros", ("Bellaros",), ("bell air os", "bell or us")),
    Name("Bell", ("Belle",), (), is_word=True),
    Name("Hrothgar", ("Rothgar",), ("roth gar",)),
    Name("Vhalzimar", ("Valzimar",), ("val zimmer", "val zee mar")),
    Name("Ka'zeth", ("Kazeth",), ("ka zeth", "kah zeth")),
    Name("Mirelle", ("Mirel",), ("mih rel", "me rel")),
    Name("Orrin", ("Oren",), ("or in",)),
    Name("Ysolde", ("Isolde",), ("ih sold", "ee sold")),
    Name("Quillon", (), ("quill on", "kwill on")),
    Name("Dravenmoor", ("Draven Moor",), ("draven more",)),
    Name("Brynwater", ("Bryn Water",), ("brin water",)),
    Name("Kael", ("Cael",), ("kale", "kail")),
    Name("Nyxara", ("Nixara",), ("nix ara", "nicks ara")),
    Name("Thornewick", ("Thorne Wick", "Thornwick"), ("thorn wick",)),
    Name("Ilvaris", (), ("ill varis", "il vare is")),
    Name("Sorrowmere", ("Sorrow Mere",), ("sorrow mere", "sorrow meer")),
    Name("Gorrak", ("Gorak",), ("gore ack",)),
    Name("Elowen", (), ("ello wen", "el oh when")),
    Name("Varrow", (), ("vair oh", "varo")),
    Name("Zephyrine", ("Zephyrin",), ("zeffa reen", "zef ih reen")),
    Name("Tamsin", ("Tamzin",), ("tam zin",)),
    Name("Lirael", (), ("leery el", "lee ree el")),
    Name("Saelith", (), ("say lith",)),
    Name("Oskar Vane", ("Oscar Vane",), ("oscar vain",)),
    Name("Ashen Crown", (), ()),
)
NAME_BY_CANONICAL = {n.canonical: n for n in NAMES}

RULES_WORDS: tuple[str, ...] = (
    "Insight",
    "Wisdom",
    "Detect Magic",
    "Dexterity saving throw",
    "Cure Wounds",
    "Bardic Inspiration",
    "Fireball",
    "initiative",
    "short rest",
    "d20",
    "d6",
    "bludgeoning",
)


def _l(n: int, text: str, *names: str, rules: tuple[str, ...] = (), cat: str = GAME) -> Line:
    return Line(n, text, cat, tuple(names), rules)


LINES: tuple[Line, ...] = (
    _l(1, "Okay everyone, let's get started."),
    _l(
        2, "Last time, you had just arrived in Brynwater after three days on the road.", "Brynwater"
    ),
    _l(3, "Cerric, you're at the front of the group. What do you do?", "Cerric"),
    _l(4, "Cerric draws his sword and looks for tracks.", "Cerric"),
    _l(5, "Belleros will take the lead while the others wait by the gate.", "Belleros"),
    _l(6, "Hrothgar the blacksmith waves at you from across the square.", "Hrothgar"),
    _l(7, "I want to talk to Hrothgar about the broken shield.", "Hrothgar"),
    _l(8, "A tall elf named Mirelle steps out of the shadows.", "Mirelle"),
    _l(9, "Mirelle says the road to Dravenmoor is closed.", "Mirelle", "Dravenmoor"),
    _l(10, "Can I make an Insight check on Mirelle?", "Mirelle", rules=("Insight",)),
    _l(11, "Roll a d20 and add your Wisdom.", rules=("d20", "Wisdom")),
    _l(12, "I rolled a natural twenty, plus three, so twenty-three."),
    _l(13, "You can tell she's hiding something about Vhalzimar.", "Vhalzimar"),
    _l(14, "Who is Vhalzimar?", "Vhalzimar"),
    _l(15, "Vhalzimar is the wizard who rules the tower at Sorrowmere.", "Vhalzimar", "Sorrowmere"),
    _l(16, "Ka'zeth, the goblin you captured, starts laughing.", "Ka'zeth"),
    _l(17, "I ask Ka'zeth why he's laughing.", "Ka'zeth"),
    _l(18, 'Ka\'zeth says, "Orrin will find you before the snow comes."', "Ka'zeth", "Orrin"),
    _l(19, "Orrin is the captain of the guard in Thornewick.", "Orrin", "Thornewick"),
    _l(20, "I cast Detect Magic on the amulet.", rules=("Detect Magic",)),
    _l(21, "The amulet glows faintly. It once belonged to Ysolde.", "Ysolde"),
    _l(22, "Ysolde and Elowen were sisters, long ago.", "Ysolde", "Elowen"),
    _l(23, "Quillon, the halfling, wants to buy the amulet.", "Quillon"),
    _l(24, "I'm not selling it to Quillon.", "Quillon"),
    _l(25, "Kael, you hear footsteps on the roof.", "Kael"),
    _l(26, "Kael climbs up to take a look.", "Kael"),
    _l(27, "Make a Dexterity saving throw. The DC is fifteen.", rules=("Dexterity saving throw",)),
    _l(28, "That's a fourteen. Kael slips and falls.", "Kael"),
    _l(29, "You take two d6 plus three bludgeoning damage.", rules=("d6", "bludgeoning")),
    _l(30, "Nyxara the priestess runs over to help.", "Nyxara"),
    _l(31, "Nyxara casts Cure Wounds on Kael.", "Nyxara", "Kael", rules=("Cure Wounds",)),
    _l(32, "Gorrak and his orcs block the north road.", "Gorrak"),
    _l(33, "Gorrak demands a toll of fifty gold pieces.", "Gorrak"),
    _l(34, "Tamsin, you recognize one of the orcs.", "Tamsin"),
    _l(
        35,
        "Tamsin uses Bardic Inspiration on Belleros.",
        "Tamsin",
        "Belleros",
        rules=("Bardic Inspiration",),
    ),
    _l(36, "Belleros casts Fireball at the orcs.", "Belleros", rules=("Fireball",)),
    _l(37, "Roll initiative, everyone.", rules=("initiative",)),
    _l(38, "Bell, you're up first.", "Bell"),
    _l(39, "Bell and Cerric flank the orc on the left.", "Bell", "Cerric"),
    _l(40, "Zephyrine, the storm sorcerer, watches from the hill.", "Zephyrine"),
    _l(
        41,
        "Zephyrine and Lirael are both members of the Ashen Crown.",
        "Zephyrine",
        "Lirael",
        "Ashen Crown",
    ),
    _l(42, "The Ashen Crown worships Varrow, the god of winter.", "Ashen Crown", "Varrow"),
    _l(43, "A prayer to Varrow echoes across the valley.", "Varrow"),
    _l(44, "Saelith whispers that Ilvaris has been lying to you.", "Saelith", "Ilvaris"),
    _l(
        45,
        "Ilvaris and Oskar Vane meet in secret at Sorrowmere.",
        "Ilvaris",
        "Oskar Vane",
        "Sorrowmere",
    ),
    _l(
        46,
        "Oskar Vane leaves a letter for Elowen in Thornewick.",
        "Oskar Vane",
        "Elowen",
        "Thornewick",
    ),
    _l(47, "I want to go back to Brynwater and find Hrothgar.", "Brynwater", "Hrothgar"),
    _l(48, "Let's take a short rest before we go on.", rules=("short rest",)),
    _l(49, "He has a serrated blade, so he'll saw through the rope.", cat=TRAP),
    _l(50, "Ring the bell, or else they'll hear us.", cat=TRAP),
    _l(51, "My cousin Eric is visiting next week.", cat=TRAP),
    _l(52, "Sarah from work says hi to everyone.", cat=TRAP),
    _l(53, "Put the quill on the desk, please.", cat=TRAP),
    _l(54, "That bridge is old and the boards are rotten.", cat=TRAP),
    _l(55, "Grab an arrow from the barrow by the door.", cat=TRAP),
    _l(56, "The kale salad is in the fridge if anyone's hungry.", cat=TRAP),
    _l(57, "The sky turned orange as the sun went down.", cat=TRAP),
    _l(58, "Is anyone else free next Thursday?", cat=OFF_TOPIC),
    _l(59, "Hang on, my dog is barking at the mail carrier.", cat=OFF_TOPIC),
    _l(60, "Did anybody watch the game last night?", cat=OFF_TOPIC),
    _l(61, "I have to leave at ten tonight, I've got work early.", cat=OFF_TOPIC),
    _l(62, "The weather has been really cold this week.", cat=EVERYDAY),
    _l(63, "Can you pass me the dice and the snacks?", cat=EVERYDAY),
    _l(64, "I think we should go left at the next crossroads.", cat=EVERYDAY),
    _l(65, "Thanks everyone, that was a great session.", cat=EVERYDAY),
)
LINE_BY_N = {line.n: line for line in LINES}

# ---- word lists --------------------------------------------------------------------

NONE, SCENE, SCENE_SL, BIG, LEARNED = "none", "scene", "scene_sl", "big", "learned"
DISTRACTOR_COUNT = 270
_SYLLABLES = (
    "ar",
    "bel",
    "cor",
    "dra",
    "el",
    "fen",
    "gal",
    "har",
    "is",
    "jor",
    "kal",
    "lor",
    "mor",
    "nar",
    "or",
    "pel",
    "quor",
    "ran",
    "sel",
    "tor",
    "ul",
    "var",
    "wyn",
    "yor",
    "zan",
    "bryn",
    "thal",
    "vex",
    "myr",
    "dun",
)


@dataclass(frozen=True, slots=True)
class VocabEntry:
    content: str
    sounds_like: tuple[str, ...] = ()


def distractors(count: int = DISTRACTOR_COUNT, seed: int = 128) -> list[str]:
    """Made-up names that appear nowhere in the script, the same on every run."""
    rng = random.Random(seed)
    taken = {n.casefold().replace("'", "") for name in NAMES for n in name.forms}
    script_words = {w.strip(".,?!\"'").casefold() for line in LINES for w in line.text.split()}
    out: list[str] = []
    seen: set[str] = set()
    while len(out) < count:
        word = "".join(rng.choice(_SYLLABLES) for _ in range(rng.randint(2, 3)))
        if word in seen or word in taken or word in script_words:
            continue
        seen.add(word)
        out.append(word.capitalize())
    return out


def word_list(kind: str, learned: dict[str, set[str]] | None = None) -> list[VocabEntry]:
    """The custom words for one bake-off list (see docs/STT_BAKEOFF.md)."""
    if kind == NONE:
        return []
    if kind not in (SCENE, SCENE_SL, BIG, LEARNED):
        raise ValueError(f"unknown word list {kind!r}")
    with_sl = kind in (SCENE_SL, LEARNED)
    entries: list[VocabEntry] = []
    for name in NAMES:
        sl = list(name.sounds_like) if with_sl else []
        if kind == LEARNED and learned:
            sl += sorted(s for s in learned.get(name.canonical, set()) if s not in sl)
        entries.append(VocabEntry(name.canonical, tuple(sl)))
    entries += [VocabEntry(w) for w in RULES_WORDS]
    if kind == BIG:
        entries += [VocabEntry(w) for w in distractors()]
    return entries
