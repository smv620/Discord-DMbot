"""Older names of rules that the newest edition renamed (docs/PLAN.md, "Lookups must
match renamed content"; #866). Kept by hand.

A table or a DM may say a spell by the name it had in the 2014 books ("Melf's Acid
Arrow"); the 2024 books call it by a shorter one ("Acid Arrow"). `dmbot.rules.index` finds
the 2024 entry under either name, and only falls back to a legacy entry when no newer
entry matches any name. Each pair is (older name, the name in the SRD 5.2.1), and every
current name must be in the data: a test checks that, so a typo here can't hide.

Only renames go here, whether or not the rule's text changed with the name (a spell that
was rewritten and renamed is still the newer version of the same spell, and the older one
must not be used while this one exists). A rule that kept its name needs no entry, and
neither does one that was merged into another (that is a question for the rules advisor,
not a name).

Made by comparing every spell name in the 2014 SRD 5.1 with the 2024 SRD 5.2.1. Most
entries only drop a creator's name (the 2014 books' titles; the 5.1 SRD itself already
printed the short ones). Five were renamed for real: Nystul's Magic Aura (now Arcanist's),
Feeblemind (Befuddlement) and Branding Smite (Shining Smite), and Mordenkainen's Sword
and Bigby's Hand, which gained "Arcane".
"""

from __future__ import annotations

# The edition these current names belong to.
EDITION = "2024"

SPELL_ALIASES: tuple[tuple[str, str], ...] = (
    ("Melf’s Acid Arrow", "Acid Arrow"),  # 2014 PHB; 2024 dropped the wizard's name
    ("Bigby’s Hand", "Arcane Hand"),  # same; the hand is now "Arcane"
    ("Mordenkainen’s Sword", "Arcane Sword"),  # same; the sword is now "Arcane"
    ("Nystul’s Magic Aura", "Arcanist’s Magic Aura"),  # renamed, not only shortened
    ("Evard’s Black Tentacles", "Black Tentacles"),  # 2014 PHB; creator's name dropped
    ("Mordenkainen’s Faithful Hound", "Faithful Hound"),  # same
    ("Tenser’s Floating Disk", "Floating Disk"),  # same
    ("Otiluke’s Freezing Sphere", "Freezing Sphere"),  # 2014 PHB; creator's name dropped
    ("Tasha’s Hideous Laughter", "Hideous Laughter"),  # same
    ("Drawmij’s Instant Summons", "Instant Summons"),  # same
    ("Otto’s Irresistible Dance", "Irresistible Dance"),  # same
    ("Mordenkainen’s Magnificent Mansion", "Magnificent Mansion"),  # same
    ("Mordenkainen’s Private Sanctum", "Private Sanctum"),  # same
    ("Otiluke’s Resilient Sphere", "Resilient Sphere"),  # same
    ("Leomund’s Secret Chest", "Secret Chest"),  # same
    ("Rary’s Telepathic Bond", "Telepathic Bond"),  # same
    ("Leomund’s Tiny Hut", "Tiny Hut"),  # same
    ("Feeblemind", "Befuddlement"),  # 2014 SRD 5.1 name; 2024 renamed it (and reworked it)
    ("Branding Smite", "Shining Smite"),  # same: 2024 renamed it (and reworked it)
)

# Monsters the 2025 books renamed (or split, keeping one creature plainly the same). Made by
# comparing every creature name in the 2014 SRD 5.1 with the 2024 SRD 5.2.1: 34 names of
# the 5.1 have no 5.2.1 entry of that name; these 27 are the same creature under a new
# name. The other seven stay [Legacy 2014] entries: the 5.2.1 has no Duergar, Drow, Deep
# Gnome, Lizardfolk, Orc or Half-Red Dragon Veteran, and no single Succubus/Incubus
# (they are two entries now, each found by its own name). Each pair was checked by hand
# against the creature's challenge rating, size and kind (the Minotaur and the Veteran changed
# their numbers a little; the Kobold became a dragon; none is a different creature).
MONSTER_ALIASES: tuple[tuple[str, str], ...] = (
    ("Flying Sword", "Animated Flying Sword"),  # 5.1 name; the 5.2.1 calls it "Animated"
    ("Rug of Smothering", "Animated Rug of Smothering"),  # same
    ("Azer", "Azer Sentinel"),  # one azer is now a "Sentinel"
    ("Bugbear", "Bugbear Warrior"),  # the plain bugbear is the "Warrior" (a Stalker is new)
    ("Centaur", "Centaur Trooper"),  # renamed
    ("Shrieker", "Shrieker Fungus"),  # renamed
    ("Gnoll", "Gnoll Warrior"),  # the plain gnoll is the "Warrior"
    ("Goblin", "Goblin Warrior"),  # same (a Minion and a Boss are new)
    ("Hobgoblin", "Hobgoblin Warrior"),  # same (a Captain is new)
    ("Kobold", "Kobold Warrior"),  # same
    ("Merfolk", "Merfolk Skirmisher"),  # renamed
    ("Minotaur", "Minotaur of Baphomet"),  # renamed
    ("Sahuagin", "Sahuagin Warrior"),  # same
    ("Androsphinx", "Sphinx of Valor"),  # the sphinxes were renamed by what they stand for
    ("Gynosphinx", "Sphinx of Lore"),  # same
    ("Giant Poisonous Snake", "Giant Venomous Snake"),  # "poisonous" became "venomous"
    ("Poisonous Snake", "Venomous Snake"),  # same
    ("Swarm of Poisonous Snakes", "Swarm of Venomous Snakes"),  # same
    ("Giant Sea Horse", "Giant Seahorse"),  # one word now
    ("Sea Horse", "Seahorse"),  # same
    ("Quipper", "Piranha"),  # renamed
    ("Swarm of Quippers", "Swarm of Piranhas"),  # same
    ("Acolyte", "Priest Acolyte"),  # renamed
    ("Cult Fanatic", "Cultist Fanatic"),  # renamed
    ("Thug", "Tough"),  # renamed
    ("Tribal Warrior", "Warrior Infantry"),  # renamed
    ("Veteran", "Warrior Veteran"),  # renamed
)

ALL: tuple[tuple[str, str], ...] = SPELL_ALIASES + MONSTER_ALIASES
