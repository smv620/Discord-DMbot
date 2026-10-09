# Attribution: System Reference Document 5.1

This work includes material taken from the System Reference Document 5.1 (“SRD 5.1”) by Wizards of the Coast LLC and available at https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 is licensed under the Creative Commons Attribution 4.0 International License available at https://creativecommons.org/licenses/by/4.0/legalcode.

(That is the statement the SRD asks for, word for word. DMbot is compatible with fifth edition.)

## What is here

`spells.json` and `conditions.json` hold every spell and every condition of the SRD 5.1 (the 2014 rules), and nothing from any other book. DMbot uses them only as the legacy fallback, tagged `[Legacy 2014]`, when the newer rules have nothing under the name. Each file says where it came from: the SRD 5.1 PDF that Wizards of the Coast publishes at the address in the file, with that PDF's SHA-256, so anyone can check it.

## Changes made (CC BY 4.0 asks us to say so)

The words are the SRD's. DMbot's own tool (`python -m dmbot.devtools.srd`) took them out of the PDF and:

- removed the stray tabs, no-break spaces and soft hyphens in the PDF's text, and closed up the spaces around hyphens ("15 - foot- radius" is "15-foot-radius");
- joined the lines of each paragraph, and put words back together where the PDF's text had cut them with a stray space ("hig her" is "higher"). A space is only taken out when the joined word is one that the SRD uses elsewhere;
- split the text into entries, and pulled each spell's level, school, casting time, range, components and duration out of its heading lines (this edition's spell heading has no list of classes, so these entries have none);
- recorded the page each entry is on, so DMbot can name it ("SRD 5.1, Spell Descriptions, p. 114");
- read "Component:" in Contagion's heading as "Components:".

Tables inside a spell are kept as plain lines of text, one line for each row.

## Known defects of the PDF

The PDF's text layer has lost a few words and letters altogether, so a handful of lines carry a stray letter where a word should be (for example the "At Higher Levels" line of Animal Friendship reads “one additional beast t level above 1st”). They cannot be put back from the PDF and are not corrected by hand: the files hold what the PDF holds. Where a spell's wording matters, check it against the SRD 5.1 itself.

## Making the files again

Download the PDF into a folder of its own (it is not kept in this repository), then, from `core/`:

    python -m dmbot.devtools.srd PATH/TO/SRD_CC_v5.1.pdf

The same PDF always gives the same files, so running the tool on the PDF and seeing no change (`git diff`) is how to check them. The tool recognizes which of the two SRDs it was given, and stops with a message rather than guessing when the layout is not what it expects.
