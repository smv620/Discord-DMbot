# Attribution: System Reference Document 5.2.1

This work includes material from the System Reference Document 5.2.1 (“SRD 5.2.1”) by Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is licensed under the Creative Commons Attribution 4.0 International License, available at https://creativecommons.org/licenses/by/4.0/legalcode.

(That is the statement the SRD asks for, word for word. DMbot is compatible with fifth edition.)

## What is here

`spells.json` and `conditions.json` hold every spell and every condition of the SRD 5.2.1, and nothing from any other book. Each file says where it came from: the SRD 5.2.1 PDF that Wizards of the Coast publishes at the address in the file, with that PDF's SHA-256, so anyone can check it.

## Changes made (CC BY 4.0 asks us to say so)

The words are the SRD's. DMbot's own tool (`python -m dmbot.devtools.srd`) took them out of the PDF and:

- joined the lines of each paragraph and put back words the print had cut at the end of a line;
- split the text into entries, and pulled each spell's level, school, classes, casting time, range, components and duration out of its heading lines;
- recorded the page each entry is on, so DMbot can name it ("SRD 5.2.1, Spell Descriptions, p. 131");
- read three printing slips as they were meant: the title of Acid Splash is set in small capitals, Barkskin's line says "Component:" for "Components:", and the ability names in the stat blocks inside spells (set in small capitals, so they come out as "dex" or "WiS") are written once as Str, Dex, Con, Int, Wis and Cha;

Tables and stat blocks inside a spell are kept as plain lines of text, one for each line of a table and each entry of a stat block.

## Making the files again

Download the PDF into a folder of its own (it is not kept in this repository), then, from `core/`:

    python -m dmbot.devtools.srd PATH/TO/SRD_CC_v5.2.1.pdf

The same PDF always gives the same files, so running the tool on the PDF and seeing no change (`git diff`) is how to check them. If Wizards of the Coast publishes a new version, the tool stops with a message rather than guessing when the layout is not what it expects.
