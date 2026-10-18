# Attribution: System Reference Document 5.1

This work includes material taken from the System Reference Document 5.1 (“SRD 5.1”) by Wizards of the Coast LLC and available at https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 is licensed under the Creative Commons Attribution 4.0 International License available at https://creativecommons.org/licenses/by/4.0/legalcode.

(That is the statement the SRD asks for, word for word. DMbot is compatible with fifth edition.)

## What is here

`spells.json`, `conditions.json`, `features.json`, `items.json` and `monsters.json` hold every spell, every condition, every class and subclass feature (the Classes chapter), every magic item (the Magic Items chapter) and every creature of the SRD 5.1 (the 2014 rules: the Monsters pages, the Miscellaneous Creatures appendix and the Nonplayer Characters appendix), and nothing from any other book. DMbot uses them only as the legacy fallback, tagged `[Legacy 2014]`, when the newer rules have nothing under the name. Each file says where it came from: the SRD 5.1 PDF that Wizards of the Coast publishes at the address in the file, with that PDF's SHA-256, so anyone can check it.

## Changes made (CC BY 4.0 asks us to say so)

The words are the SRD's. DMbot's own tool (`python -m dmbot.devtools.srd`) took them out of the PDF and:

- removed the stray tabs, no-break spaces and soft hyphens in the PDF's text, and closed up the spaces around hyphens ("15 - foot- radius" is "15-foot-radius");
- split the Classes chapter into its features (each heading that the class table names, with its description, filed under its class and the level the table gives; the subclass's features under the subclass, with the level their first sentence names). A heading that is not a feature ("Cantrips", "Ritual Casting", the pacts of the Warlock) is kept as a line of the feature above it. Left out: the class tables, the proficiencies and the starting equipment, the lists of options (the fighting styles, the metamagic options, the eldritch invocations), the introductions to the subclasses and each subclass's own introduction and spell list, and the flavour sections after a class;
- split the Magic Items chapter into its items (one for each heading the SRD gives, 239 in all; a name that covers several bonuses, such as “Armor, +1, +2, or +3”, is one entry, as printed), read the italic line under each name into its category, the category’s bracket, its rarity and whether it needs attunement and by whom, and wrote the category and the rarity with the capitals the 5.2.1 files use (“Wondrous item, very rare” is “Wondrous Item”, “Very Rare”; the Spell Scroll’s “varies” is “Rarity Varies”). An item keeps the rarity this edition prints, even where the 5.2.1 prints another (the Potion of Invisibility is very rare here). Left out of the chapter: its introduction (the sections Magic Items, Attunement, Wearing and Wielding Items, Multiple Items of the Same Kind, Paired Items, Activating an Item, Command Word, Consumables, Spells and Charges: rules about items, not items), its Sentient Magic Items section (Creating Sentient Magic Items, Abilities, Communication, Senses, Alignment, Special Purpose and Conflict), its Artifacts section (the Orb of Dragonkind, the one artifact), and the tables inside an item (the 1d100 effects of the Bag of Beans, the Apparatus of the Crab Levers, ...) and the stat blocks printed in the Deck of Many Things (the Avatar of Death), because their cells and lines do not read as one item’s sentences. The 30 items with something left out are: Apparatus of the Crab, Armor of Resistance, Bag of Beans, Bag of Tricks, Belt of Giant Strength, Candle of Invocation, Carpet of Flying, Cube of Force, Deck of Illusions, Deck of Many Things, Dragon Scale Mail, Efreeti Bottle, Elemental Gem, Feather Token, Figurine of Wondrous Power, Horn of Valhalla, Iron Flask, Manual of Golems, Necklace of Prayer Beads, Potion of Giant Strength, Potion of Healing, Potion of Resistance, Ring of Resistance, Ring of Shooting Stars, Robe of Useful Items, Spell Scroll, Sphere of Annihilation, Staff of Power, Staff of the Magi, Wand of Wonder (a test pins the list);
- joined the lines of each paragraph, and put words back together where the PDF's text had cut them with a stray space ("hig her" is "higher", "atta cked" is "attacked"). A space is taken out when the joined word is one the SRD uses (the words of the 5.2.1 data and the words this PDF uses often), or when neither piece is a word and the PDF has the joined word whole elsewhere (“exc ess”, with “excess” on another page); two real words the SRD merely does not use (“gum arabic”, “rotten egg”) are never joined on a guess. Two words that are both words are joined when they make a more common word, the 5.2.1 data never has them side by side, and this PDF has the joined word at least twenty times as often as the pair (“dark vision”, “drag on”, “an other”; “up on” and “has ten” are the book's own and stay). Where the PDF put a letter on the wrong side of a space, the letter is moved across ("the n ature" is "the nature", "a t arget" is "a target", "forth e duration" is "for the duration"). A hyphen at the end of a line is dropped when the word without it is one the SRD uses and the hyphenated word is not;
- closed the space the PDF leaves before a full stop, comma, semicolon or colon;
- split the text into entries, and pulled each spell's level, school, casting time, range, components and duration out of its heading lines (this edition's spell heading has no list of classes, so these entries have none);
- recorded the page each entry is on, so DMbot can name it ("SRD 5.1, Spell Descriptions, p. 114");
- read "Component:" in Contagion's heading as "Components:";
- read each creature's stat block into fields (size, type and alignment, armor class and what follows it, hit points and their dice, speed, the six ability scores, saving throws, skills, damage vulnerabilities, resistances and immunities, condition immunities, senses, languages, challenge rating and XP), and kept the traits and actions as one paragraph each under their headings (Actions, Reactions, Legendary Actions), with a spell list line by line. The group names in front of a creature ("Fungi", "Golems") and the book's own paragraphs between stat blocks are not part of any entry;
- closed a space inside a die ("3 d10" is "3d10") and a number the print had split ("17d10 + 8 5" is "17d10 + 85", "3, 900 XP" is "3,900 XP", "+1 0 to hit" is "+10 to hit").

Tables inside a spell or condition are kept as plain lines of text, one line for each row (a row that the print wraps over two lines is joined, so the words of a wrapped cell come at the end of the row).

## Known defects of the PDF

The PDF's text layer has lost a few words and letters altogether, and two defects are left in the files, neither corrected by hand (the files hold what the PDF holds):

- Animal Friendship's “At Higher Levels” line reads “one additional beast t level above 1st”, a word lost for good;

Where a spell's or creature's wording matters, check it against the SRD 5.1 itself. A few plain words (“attacked”, “defends”, “dread”, “dimly”, “lit”, “bed”, “linen”, “strip”, “ideas”) are listed in the tool as known words, because neither the 5.2.1 data nor this PDF spells them out often enough for the cut-word reading to know them.

The `document` block of each file records the SHA-256 of the 5.2.1 data files whose words the tool used (`word_list_sha256`), so the same PDF gives the same files only against the same 5.2.1 data (and the same tool: its short list of known words and its thresholds are in `parse51.py`).

## Making the files again

Download the PDF into a folder of its own (it is not kept in this repository), then, from `core/`:

    pip install -e ".[srd]"
    python -m dmbot.devtools.srd PATH/TO/SRD_CC_v5.1.pdf

The same PDF always gives the same files, so running the tool on the PDF and seeing no change (`git diff`) is how to check them. The tool recognizes which of the two SRDs it was given, and stops with a message rather than guessing when the layout is not what it expects.
