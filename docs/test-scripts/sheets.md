# D&D Beyond sheets: spell names heard right (#723)

**Who:** dev1 (on the server) and one player with a D&D Beyond character set to **Public**
(on D&D Beyond: open the character, press **Edit**, then **Settings**, and set
**Character Privacy** to **Public**). The DM has added that character to the campaign
(`/dmbot names`, then **🧑 Add a player's character**).

## Before the session (the player)

1. Type `/consent give` in the server. DMbot's answer has a **📜 My character sheet**
   button (an older consent message doesn't).
2. Press **📜 My character sheet**, then **Link my D&D Beyond sheet**, and paste the
   character's address from the top of its D&D Beyond page.
3. DMbot answers "✅ Linked. DMbot read …'s sheet (species · class level)".

The DM can check the character's card in `/dmbot names`: it shows "📜 Sheet: linked to
D&D Beyond, read …".

## The session

1. **(dev1)** Start with `/dmbot start`, then look in `docker compose logs core` for:

   `Character sheets: 1 linked, N names in the hints; from D&D Beyond: …`

   Only 15 names join the hints. **Tell the player three of the names listed there**,
   preferably unusual ones.
2. **(the player)** Read these lines, saying one of those three names in each gap, with a
   two-count stop between lines:
   1. [Player] I cast ________ on the door.
   2. [Player] Then I use ________ to get across.
   3. [Player] If that fails, ________.
3. **(dev1)** Stop the session with `/dmbot stop`.

## What to check

The live transcript channel (and the cleaned transcript download) should write the three
names as on the sheet, not as everyday words.

**What to tell dev1 / write in the testing log:** the log line (names only), and for each
of the three names, how the transcript wrote it.

## Extra (dev1)

The player presses **📜 My character sheet**, then **Forget my sheet**. Start a session
again: the log line says `0 linked` and `from D&D Beyond: none`.

Not yet in the session twin: replaying with a linked sheet needs the replay tool to load
sheet names (follow-up).
