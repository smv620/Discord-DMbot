# D&D Beyond sheets: spell names heard right (#723)

One player with a D&D Beyond character set to **Public** (D&D Beyond: open the character,
press Edit, then Settings, Character Privacy: Public). The DM has added that character
(`/dmbot names`, then **🧑 Add a player's character**).

## Before the session

1. The player presses **📜 My character sheet** in DMbot's private message (the consent
   confirmation, or a session's reminder), then **Link my D&D Beyond sheet**, and pastes
   the character's address.
2. DMbot answers "✅ Linked. DMbot read …'s sheet (species · class level)".
3. The DM opens the character's card in `/dmbot names`: it shows "📜 Sheet: linked to
   D&D Beyond, read just now".

## The session

Start with `/dmbot start`. In `docker compose logs core`, look for:

`Character sheets: 1 linked, N names in the hints: …`

with the character's spells among the names.

The player reads these lines, naming three of the character's own spells or features in
the gaps (pick ones with unusual names), with a two-count stop between lines:

1. [Player] I cast ________ on the door.
2. [Player] Then I use ________ to get across.
3. [Player] If that fails, ________.

## What to check

- The transcript writes the three names as on the sheet (not as everyday words).
- Unlink (📜 My character sheet, then **Unlink**), stop and start again: the log line
  says `0 linked` and those names are gone from the hints.

Not yet in the session twin: replaying with a linked sheet needs the replay tool to load
sheet names (follow-up).
