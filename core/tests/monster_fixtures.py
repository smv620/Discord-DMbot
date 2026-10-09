"""Made-up lines in the shape of the SRD 5.2.1's stat blocks, for the monster tests (no PDF
is needed, and none is kept in the repository)."""

from __future__ import annotations

from dmbot.devtools.srd.pdf import Line, Piece

TITLE, ITALIC, BOLD, BOLD_ITALIC, PLAIN, SANS = (
    "GillSans-SemiBold",
    "Optima-Italic",
    "Optima-Bold",
    "Optima-BoldItalic",
    "Optima-Regular",
    "GillSans",
)


def say(text: str, font: str, *, y: float = 500.0, page: int = 290) -> Line:
    return Line(63.0, y, [Piece(63.0, y, f"ABCDEF+{font}", text)], page)


def labelled(label: str, value: str) -> Line:
    """A stat block line: the label in bold, then its value."""
    pieces = [
        Piece(63.0, 500.0, f"ABCDEF+{BOLD}", label),
        Piece(95.0, 500.0, "OPT+Optima-Regular", value),
    ]
    return Line(63.0, 500.0, pieces, 290)


def goblin() -> list[Line]:
    return [
        say("Goblin Warrior", TITLE),
        say("Small Fey (Goblinoid), Chaotic Neutral", ITALIC),
        say("AC 15  Initiative +2 (12)", BOLD),
        say("HP 10 (3d6)", BOLD),
        say("Speed 30 ft.", BOLD),
        say("MOD SAVE MOD SAVE MOD SAVE", SANS),
        say("Str 8 −1 −1 Dex 15 +2 +4 Con 10 +0 +0", "GillSans-SemiBold-SC700"),
        say("Int 10 +0 +0 WIS 8 −1 −1 Cha 8 −1 −1", "GillSans-SemiBold-SC700"),
        labelled("Skills ", "Stealth +6"),
        labelled("Gear ", "Leather Armor, Scimitar,"),
        say("Shield, Shortbow", PLAIN),
        labelled("Senses ", "Darkvision 60 ft.; Passive Perception 9"),
        labelled("Languages ", "Common, Goblin"),
        say("CR 1/4 (XP 50; PB +2)", BOLD),
        say("Traits", SANS),
        say("Keen Sight. The goblin sees well", BOLD_ITALIC),
        say("at long range, even in the dark.", PLAIN),
        say("Actions", SANS),
        say(
            "Scimitar. Melee Attack Roll: +4, reach 5 ft. Hit: 5 (1d6 + 2) Slashing dam-",
            BOLD_ITALIC,
        ),
        say("age, plus 2 (1d4) if the attack roll had Advantage.", PLAIN),
        say("Spellcasting (At Will", BOLD_ITALIC),
        say("or 1/Day). The goblin casts a spell.", PLAIN),
        say("At Will: Light", BOLD),
    ]
