# Setup for the bulk-import test (bakeoff-story-names.txt)

A live test of **/dmbot names → Add many** on a campaign that already has some of the names
(#368, design #369). It takes about ten minutes. A computer is easier than a phone, because
step 2 needs a file.

## 0. Make a new campaign

Make a new campaign just for this test, with you as its DM (for example with `/dmbot start`
→ **New campaign**, named "Import test"). Don't play, record or add names in it before the
test: names DMbot hears or adds count as "already known" and change the numbers below.

## 1. Add these 10 names first

In Discord, type `/dmbot names`, choose the new campaign, press **Add many → Paste a list**,
and paste this block:

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

It should say **📥 Added 10 names.** and nothing about checking names. Cerric, Mirelle and Kael
aren't in the test file. The other 7 are in it on purpose, but spelled differently, swapped,
or with a different kind.

## 2. Upload the test file

Download the file:
https://github.com/smv620/Discord-DMbot/raw/development/docs/test-scripts/bakeoff-story-names.txt

Then type `/dmbot names`, choose the same campaign, add the downloaded file in the **file**
box, and send.

## 3. What should happen

Which version applies? If #369 ("Add many: duplicates and near-duplicates") isn't merged and
deployed yet, use **A**. Check `docs/testing-status.log`, or ask the server session.

### A. Before #369

The file has 26 names (lines starting with ### don't count). The message should read:

> 📥 **Added 22 names.**
> 📝 **3 names need you to check them** (no kind, or they sound like a name DMbot already
> knows). Press 📝 Check new names.
> Skipped: 4 names DMbot already knows.
> Wrong list? **Undo** takes the whole list back, until you check or change any of those names.

Why:
- 19 names are new and are added.
- Gorrack, Quilon and Brynnwater sound like Gorrak, Quillon and Brynwater, so they are added
  but wait for you to check them. They are not joined to your names.
- Bell, Vane, Varrow and Ashen Crown are names you already have, so they are skipped. Your
  kinds stay as they were.

Then, in this order:
1. Press **📝 Check new names**. It should offer **Same as…** for Gorrack, Quilon and
   Brynnwater. **Look only. Don't choose anything**, because choosing one turns Undo off.
2. Press **Undo**. It should take away all 22 names and leave your 10.

### B. After #369

The first message should start:

> 📥 **Added 22 names** · 4 already known · 3 look like known names, check below · 2 have a
> different kind, check below.
> • **Bell** is already another name for **Belleros**. Nothing changed.
> • **Vane** is already another name for **Oskar Vane**. Nothing changed.

Below it come two more messages:
- **"🔎 3 names look like names DMbot already knows."** with Gorrack → Gorrak?, Quilon →
  Quillon? and Brynnwater → Brynwater?. Each has numbered buttons (**1. Same**,
  **1. Different**, **1. Remove**), and there are **Same for all 3** and **Different for
  all 3**. Nothing is joined until you choose.
- **"🏷 2 names have a different kind in your list."** Varrow: DMbot has god, your list says
  place. Ashen Crown: DMbot has group, your list says place. The buttons are **1. Keep
  god** / **1. Change to place**, **2. Keep group** / **2. Change to place**, and **Keep
  all** / **Change all**. If you ignore them, DMbot keeps what it has.

Then, in this order:
1. **Look only. Don't choose anything yet**, because choosing one turns Undo off.
2. Press **Undo** under the first message. It should take away all 22 names and leave your 10.
3. Upload the file again and try the buttons: **Same for all** should make Gorrack, Quilon
   and Brynnwater other names of your three names, and **1. Change to place** should
   change Varrow.

(A file and test in the code check these numbers: `test_the_owners_bulk_import_test_reads_as_its_setup_says`.)

## 4. Report it

Copy DMbot's whole message and paste it to the server session, with a tick or a cross for
each:

- [ ] Added 22
- [ ] 3 to check (A), or 3 that look like known names (B)
- [ ] Skipped 4 (A), or 4 already known and 2 kinds differ (B)
- [ ] Same as… offered for Gorrack, Quilon and Brynnwater
- [ ] Undo left just your 10 names
