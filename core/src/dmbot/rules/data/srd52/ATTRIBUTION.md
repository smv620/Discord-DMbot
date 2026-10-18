# Attribution: System Reference Document 5.2.1

This work includes material from the System Reference Document 5.2.1 (“SRD 5.2.1”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

(That is the statement the SRD asks for, word for word. DMbot is compatible with fifth edition.)

## What is here

`spells.json`, `conditions.json`, `rules.json`, `features.json`, `items.json` and `monsters.json` hold every spell, every condition, every other entry of the Rules Glossary (its Rules Definitions: Cover, the Dash action, Long Rest, ...), every class and subclass feature of the Classes chapter (Rage, Evasion, ...) and every magic item of the Magic Items A–Z chapter and every creature (the Monsters A–Z and Animals sections) of the SRD 5.2.1, and nothing from any other book. Each file says where it came from: the SRD 5.2.1 PDF that Wizards of the Coast publishes at the address in the file, with that PDF's SHA-256, so anyone can check it.

## Changes made (CC BY 4.0 asks us to say so)

The words are the SRD's. DMbot's own tool (`python -m dmbot.devtools.srd`) took them out of the PDF and:

- joined the lines of each paragraph and put back words the print had cut at the end of a line;
- split the text into entries, and pulled each spell's level, school, classes, casting time, range, components and duration out of its heading lines;
- recorded the page each entry is on, so DMbot can name it ("SRD 5.2.1, Spell Descriptions, p. 131");
- read three oddities as they were meant: the title of Acid Splash and the ability names in the stat blocks inside spells are set in small capitals, which come out of the PDF in mixed case ("Acid SplASh", "dex", "WiS"), so the title is written in ordinary capitals and the ability names as Str, Dex, Con, Int, Wis and Cha; and Barkskin's line says "Component:" for "Components:";
- read each creature's stat block into fields: size, type and alignment from the italic line, armor class and initiative, hit points and their dice, speed, the six ability scores (the table's modifiers and saves are read too; only the saving throws that differ from the modifier are kept, as “Dex +5, Wis +3”, the way the 5.1 prints them), skills, gear, resistances, vulnerabilities, immunities (damage and conditions together, as printed), senses, languages, challenge rating, XP and proficiency bonus; and kept the traits and actions as one paragraph each under their headings (Traits, Actions, Bonus Actions, Reactions, Legendary Actions), with a spell list line by line;
- left out of each Rules Glossary entry its tables (their cells do not read as sentences) and its “See also” line, which is kept apart as a list of the entries it points to;
- read the PDF's text with pypdfium2 (#1892), which keeps all of it (pypdf and pdfminer.six leave parts out: the ability table's Dex, Con, Wis and Cha labels, some numbers, an equals sign), and put back the space the PDF draws as a gap (before a number, between pieces, and the indent that starts a paragraph);
- split the Classes chapter into its features (each `Level N: Name` with its description, filed under its class and, for a subclass's, its subclass) and left out of it the class tables, the spell lists, the option lists (Metamagic, Eldritch Invocations, Beast Shapes) and each subclass's introduction, because their cells and lists do not read as one feature's sentences;
- split the Magic Items A–Z chapter into its items (one for each heading the SRD gives, 258 in all; a name that covers several bonuses, such as “Armor, +1, +2, or +3” or a family such as “Potions of Healing”, is one entry, as printed), read the italic line under each name into its category, the category’s bracket, its rarity as printed (“Uncommon (+1), Rare (+2), or Very Rare (+3)”) and whether it needs attunement and by whom, and left out of the chapter its introduction (rules about items, not items), the tables inside an item (the 1d100 effects of the Bag of Beans, the Apparatus of the Crab Levers, ...) and the stat blocks printed inside an item (the Giant Fly of the Figurine of Wondrous Power, the Avatar of Death of the Mysterious Deck), because their cells and lines do not read as one item’s sentences. The 41 items with something left out are: Ammunition of Slaying, Amulet of the Planes, Apparatus of the Crab, Armor of Resistance, Bag of Beans, Bag of Tricks, Belt of Giant Strength, Candle of Invocation, Carpet of Flying, Cube of Force, Deck of Illusions, Dragon Orb, Dragon Scale Mail, Efreeti Bottle, Elemental Gem, Feather Token, Figurine of Wondrous Power, Hat of Many Spells, Horn of Valhalla, Manual of Golems, Mysterious Deck, Necklace of Prayer Beads, Potion of Giant Strength, Potions of Healing, Potion of Resistance, Ring of Elemental Command, Ring of Resistance, Ring of Shooting Stars, Robe of Useful Items, Spell Scroll, Sphere of Annihilation, Staff of Fire, Staff of Frost, Staff of Healing, Staff of Power, Staff of Swarming Insects, Staff of the Magi, Staff of the Woodlands, Wand of Binding, Wand of Fear and Wand of Wonder (a test pins the list);
- closed the gap the PDF leaves before a comma after a number (“+7 , reach” is “+7, reach”, “7 ,200” is “7,200”).

Tables and stat blocks inside a spell are kept as plain lines of text: one line for each row of a table (with a space between its columns) and one for each entry of a stat block, however many lines the print wraps it over.

## Making the files again

Download the PDF into a folder of its own (it is not kept in this repository), then, from `core/`:

    pip install -e ".[srd]"
    python -m dmbot.devtools.srd PATH/TO/SRD_CC_v5.2.1.pdf

The same PDF always gives the same files, so running the tool on the PDF and seeing no change (`git diff`) is how to check them. If Wizards of the Coast publishes a new version, the tool stops with a message rather than guessing when the layout is not what it expects.
