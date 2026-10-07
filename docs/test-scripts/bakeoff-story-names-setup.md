# Setup for the bulk-import test (bakeoff-story-names.txt)

A live test of **/dmbot names → Add many** on a campaign that already has some of the names
(#368, design #369). Use a **new campaign**, so no other names get in the way.

## 1. Add these 10 names first

In Discord, type `/dmbot names`, press **Add many → Paste a list**, and paste this block:

```
Cerric | NPC
Mirelle | NPC
Kael | NPC
Gorrak | NPC
Quillon | NPC
Brynwater | place
Belleros | NPC | Bell
Oskar Vane | NPC | Vane
Varrow | god
Ashen Crown | group
```

It should say **Added 10 names.** The first three are left out of the test file. The other
seven are the originals of the file's seven deliberately wrong lines.

## 2. Upload the test file

Type `/dmbot names` and add `docs/test-scripts/bakeoff-story-names.txt` in the **file** box.

## 3. What should happen

### With today's code

The file has 26 lines:
- **19 new names** are added.
- The **3 close spellings** (Gorrack, Quilon, Brynnwater) are also added. Each sounds like a
  name you already have, so it waits in 📝 Check new names. They are not merged.
- The **2 swapped lines** (Bell, Vane) and the **2 different-kind lines** (Varrow, Ashen
  Crown) are skipped whole as "already known". The kinds you gave are kept, and nothing is
  said about the differences.

The message should read:

> 📥 **Added 22 names.**
> 📝 **3 names need you to check them** (no kind, or they sound like a name DMbot already
> knows). Press 📝 Check new names.
> Skipped: 4 names DMbot already knows.
> Wrong list? **Undo** takes the whole list back, until you check or change any of those names.

Then:
- **📝 Check new names** should offer Same as… for Gorrack, Quilon and Brynnwater.
- **Undo** should remove all 22 and leave your 10.

### Once #369 is built

- **Swapped lines:** Bell and Vane fold in quietly, with nothing new to add. The summary says
  each is another name for Belleros or Oskar Vane, and nothing changed.
- **Different kinds:** Varrow and Ashen Crown are counted as "kind differs", and DMbot asks
  once per pair:
  - "**Varrow** is a god here; your list says place. [Keep god] [Change to place]"
  - the same for Ashen Crown (group or place).

  If you ignore the question, DMbot keeps what it has.
- **Close spellings:** Gorrack, Quilon and Brynnwater each score 0.92 to 0.95 for likeness and
  sound the same as your names, so they are close names. They are saved as proposed names and
  asked about, grouped, with Same for all and Different for all, since there are 3 of them:
  "**3 names look like names DMbot already knows**"
  - `Gorrack → Gorrak?`
  - `Quilon → Quillon?`
  - `Brynnwater → Brynwater?`
- **Summary:** about "Added 22 names · 4 already known · 3 look like known names, check below
  · 2 kinds differ, check below". #369 decides whether the 3 close names count in "Added".
- **Undo:** reverts everything, including anything folded in.

## 4. Record it

Write down what the message said, compared with the above, in `docs/testing-history.log`.
