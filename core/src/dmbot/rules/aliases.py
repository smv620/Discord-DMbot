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
printed the short ones). Four were renamed for real: Nystul's Magic Aura (now Arcanist's),
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
