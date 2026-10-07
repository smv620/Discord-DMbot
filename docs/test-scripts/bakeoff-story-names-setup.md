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

The message should read:

> 📥 **Added 22 names** · 2 already known · 3 look like names DMbot already knows, check below
> · 2 kinds differ, check below

And:
- It says Bell is another name for Belleros and Vane is another name for Oskar Vane, and that
  nothing changed.
- **"3 names look like names DMbot already knows"**, with Gorrack → Gorrak?, Quilon →
  Quillon? and Brynnwater → Brynwater?, and **Same for all** and **Different for all**
  buttons.
- It asks about kinds: "**Varrow** is a god here; your list says place", with **Keep god** and
  **Change to place**. The same goes for Ashen Crown (group or place). If you ignore it,
  DMbot keeps what it has.
- **Undo** still takes everything back, if you press it before choosing anything.

If #369 ends up counting differently (for example 19 added rather than 22), the server session
updates this line when it deploys #369.

## 4. Report it

Copy DMbot's whole message and paste it to the server session, with a tick or a cross for
each:

- [ ] Added 22
- [ ] 3 to check (A), or 3 that look like known names (B)
- [ ] Skipped or already known 4 (A), or 2 already known and 2 kinds differ (B)
- [ ] Same as… offered for Gorrack, Quilon and Brynnwater
- [ ] Undo left just your 10 names
