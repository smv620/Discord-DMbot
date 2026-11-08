# Story graph answer keys (public sample data)

These files say what DMbot's story graph **should** hold after it reads a sample lore document and a sample session.
The player characters and many other names are invented for testing; some towns, gods and non-player characters come from a published adventure (names only, none of its text). The players agreed that their lines may be used for testing, development and public examples.
The documents themselves are linked, not copied.

## The sample documents

- Sample lore (**the one the lore key follows**, the small test bed, about 2,200 words): <https://docs.google.com/document/d/1zbt7g3r6uFfiSYr3fl_gyaxARhwJPizauI0jmiEa2aw/edit?usp=sharing>
- A longer lore document, also public (about 13,500 words, with session recaps, DM prep notes and an entities tab). **No key follows it yet**: <https://docs.google.com/document/d/1lHeFJAheOyz-suXZS644R86dLMt_d5VX7ib6Ig4Ox6Y/edit?usp=sharing>
- Sample session (**the one the session key follows**, read together with the small lore): <https://docs.google.com/document/d/1BtHZ4O75886WHOoTq0FCPst2hdg7YLnsaCiI-kNsv-E/edit?usp=sharing>

## The test players

| Player character | Player (not on Discord) |
|---|---|
| Belleros Stormgrace | `player_jac` |
| Cerric Vanya'elen | `player_ist` |
| Claude Ambere | `player_owe` |
| Okthandom Korran | `player_eri` |

These names are made up, and the players are **not on Discord**: the session file names its speakers `player_jac`, `player_ist`, `player_owe` and `player_eri`, and the DM's lines `DM`. They are imported the way the website's import takes a speaker who is not on Discord, with the uploader's (the DM's) confirmation. The older labels (`user_ist`, `ist`, and so on) still work, so an older session file still runs. No real account is needed beyond the DM's own.

## The keys

| File | What it is |
|---|---|
| [lore_key.md](lore_key.md) / [.json](lore_key.json) | What the small sample lore should produce, with the document line for each tie |
| [session_key.md](session_key.md) / [.json](session_key.json) | What the sample session should produce, with the transcript line for each tie |
| [master_key.md](master_key.md) / [.json](master_key.json) | The two together, each tie once: lore first, then session (where they disagree about what is current, the session wins). **This is the one the check scores.** |

A key entry marked `"by_design": true` is a **kind** of creature the rules already hold (a yeti, an ice troll, a mind flayer): DMbot leaves it out of story memory on purpose (`in_the_rules`), so the check does not count it as a miss when it is absent; it lists it on its own "left out by design" line so a change in that rule shows up (Supervisor, 2026-11-06, #3545). A particular creature the lore names or sets apart (a named owlbear) is an entry and stays must-find.

A key entry that is a person or group the story describes but never names ("Okthandom's child", "The beggar woman") is matched by its **description**: DMbot calls such an entry `unnamed - <description>` (owner, 2026-11-07, #3736; older backups say `<description> (name unknown)`), and the check compares the description with the key's name and aliases, ignoring a leading "a", "an" or "the". A description in the backup never matches a name in the key, or the other way round. A key entry marked `"unnamed": true` is stricter: it is found only by an unnamed entry's description, never by a name, so a description stored as a name (a person named "maul barbarian") does not count for it, and a must-not name that is such an entry's own description is a miss only as a name (#3545). An entry may carry `"must_find": false`, as a tie may: a good find, not counted as a must when missing. An unnamed entry the key does not list is counted on its own line ("Unnamed people not in the key"), not among the extra names, since the owner's rule keeps every one of them.

The `.md` files are for reading, the `.json` files for the check; each pair comes from one list, so they cannot disagree.

## The check script

`core/src/dmbot/devtools/graph_key_check.py` scores a campaign **backup** against a key and prints counts and ids: for the entries and for each group of ties (family, work, feelings, things, places, associations) how many were **found**, **missing**, of the **wrong type** (for example family without its role, or "in" where a word was expected), found **only as a waiting suggestion**, plus the **extra** entries and ties the key does not name, the **must-not-find** names that appeared, and the inferred ties that were stored as facts. It reads the backup file only: no database, no Discord, no AI.

```bash
cd core
python -m dmbot.devtools.graph_key_check BACKUP.dmbot.json.gz --docs lore      # after the lore is read
python -m dmbot.devtools.graph_key_check BACKUP.dmbot.json.gz --docs both      # after the session is read
# options: --counts-only (ids and counts, no names)  --json OUT.json  --key OTHER_KEY.json
```

### The one command (#3547): what dev1 runs now

`sample_run` does steps 1 to 4 below by itself, with no one in Discord: it deletes the last run's sample campaign and makes a new one (only that exact name, and only when it is plainly the tool's), queues the lore the way the website's upload does and waits for the running bot to read it, accepts what was found, backs up and scores the lore **exactly as the reader left it** (the report says how many of the four player characters the read found by itself), only then sets the four itself (each to its player who is not on Discord, through the store's own "who plays" calls), imports the session file as a past session through the website import's own code, with the four players as speakers without Discord and the DM's confirmation recorded as the import records it, waits for the bot, accepts again, backs up and scores both, and writes the recap with no audio. It prints one block of counts and times to paste on #3545, where regressions are reported. Download the two documents above as plain text files first (the session in a timestamped form the import reads, such as `[0:00:05] player_ist: ...`, with the DM's lines as `DM`).

```bash
cd core
python -m dmbot.devtools.sample_run --guild GUILD --dm DM_USER_ID \
    --lore lore.txt --session session.txt [--keep-old]
# the first run prints the most paid AI calls it can use and stops; add --yes to run it
```

It needs `DATABASE_URL` and `ANTHROPIC_API_KEY` in its environment, the bot running with the lore and story switches on as the test plan says, a DM account with a paid plan or free access (the tool never grants one), and the DM's account must not have said no to being recorded. The older `--players ist=ID,jac=ID,eri=ID,owe=ID` (four real test accounts that agreed in Discord) still works. Backups go to `--backup-dir` (default `/var/lib/dmbot-sample-backups`, outside the repository). The report names which `DMBOT_` switches were on. The manual steps below are what the command does, for when one step must be run alone.

### How dev1 runs it each time (a test campaign, never a real one)

Run it after any deploy that changes what the story reader or the story graph does, on a **throw-away test campaign** made only for this. The documents are public, and the report prints their names, so never run it on any other campaign.

1. In a test server, make a **new** campaign named **test of the frostmaiden (sample)** for each run (delete the last run's, so old confirmations do not count). It must be a **paid campaign with a free slot**: Try It campaigns have no backup button. The check refuses any other campaign name. Switch story reading on as the current test plan says (`DMBOT_STORY_EXTRACTOR`, and readings if that is part of the run). Say in the log which switches were on. The override flag `--i-know-this-is-the-test-campaign` is never used on a real campaign.
2. Load the **small sample lore** (link above) as the campaign's lore (the "your notes" upload of the names panel, or the website's lore page). Wait for the read to finish, then **accept everything that waits** with `accept_all` (below), so no one has to press anything in Discord. (The old way, answering nothing and leaving the suggestions waiting, is still worth one optional second pass for comparison: the check then scores them as found-waiting.)
   `accept_all` (#3544) is dev1's command, run on the server as the normal `dmbot` user (the database refuses a superuser, as for `movers_check`): `cd core && python -m dmbot.devtools.accept_all --guild GUILD_ID --campaign CAMPAIGN_ID --dry-run` counts what it would accept, and the same line with `--yes` instead of `--dry-run` does it. It confirms every waiting name, other name, connection, "Which one?" and same-person question through the store's own DM calls (so each rule is checked again and each change can be undone), and prints counts only (accepted, left waiting, refused by a rule). A name that needs a person to say who plays it, and a question with no suggestion, stay waiting. It refuses any campaign not named **test of the frostmaiden (sample)**, with no override.
3. Make a backup (the backup button, then download the attached file; it holds the test campaign's DM secrets, so keep it off the repo and out of issues) and run the check with `--docs lore --json lore-run.json`. Keep the report and the json file for the comparison in step 5.
4. Import the **sample session** (link above) as a past session. Name the transcript's speakers `player_ist` (Cerric), `player_jac` (Belleros), `player_eri` (Okthandom) and `player_owe` (Claude): each is a speaker who is not on Discord. The **DM** is the DM's own account, written `DM`. Give the import-consent confirmation the uploader is asked for. Wait for the import to finish, run `accept_all --yes` again (the session's suggestions), make a backup, and run the check with `--docs both --json both-run.json`.
5. Put the **totals and the lists of missing tie ids** (entries have no ids: the count is enough) in `docs/testing-history.log` (counts and ids only: the sample's names are public, but the habit is the same), and the latest in `docs/testing-status.log`. Compare the json files with the previous run's: **a lower "found" count, a new must-not-find name or a new wrong-type tie is a regression**; report it on #3545 (the sample run's issue).
6. If a run shows a real entry or tie the key missed, or a key row that is wrong, GraphEngineer updates the keys (owner, 2026-11-07: no need to ask first) when **both** hold: the document's own words say it (the line is quoted in the row's note), and it is a standing fact of the kind the key scores (not an event, a guess or a duplicate of a row the key already has in other words). Anything less is reported on #3545 and left out of the key. A new row has `must_find` false (a good find, not a miss), the next id, and a line number. Each change is listed under "Changes to the keys" below with the run that showed it, so scores before and after a change are not compared as if the key were the same.

**The script check (#3385, once the resolver is deployed):** after step 4, make the session's script (the "Make a script of this session" button) and read it. Count, and record in the log (counts only): lines where a form of address or title is followed by a bracket (the sample's tavern line should read like "Hello, Friend [Belleros]"), brackets turned into `[unknown]`, and that **no entry or other name was added** by the run. A form of address must never show up in the names list.

A tie found only as a **waiting suggestion** counts as found-waiting: the reader proposed it and the DM has not said yes. A tie marked **inferred** in the key should only ever be offered; one that is already confirmed is counted so a person can look.

## Changes to the keys

- **2026-11-08, from the step 11 lore run (export commit 17044bf7):** added `l122` / `m136` (Brewner knows Belleros, wary, lore line 67: the heated debate goes both ways; the key only had Belleros toward Brewner). Totals: lore key 122 ties, master key 136. Not added, as at step 10: Glyph travels_with (no group yet, #3505), Torrga in Icewind Dale, Vaelrith as the child's uncle, the Brotherhood knows Danneth. Also not added: Cerric and Okthandom distrust Fizzlebomb (line 90 says they feared being accused of forbidden magic, not by whom); Okthandom last seen in Icewind Dale and Belleros last seen at the Three Flags Sailing (sheltering there is not where they were last seen); Sephek serves Torg's Caravan (the lore says bodyguard for Torrga; the key has works_for Torrga); Bryn Shander in the Eastway Road (the road passes it); Wit serves Syllar (the key has mentor_of, #3780).
- **2026-11-08, from the step 10 lore plus session run (export commit abc6b950), Supervisor's ruling on #3545:** the two barbarians who ambush the sleds are added to the session and master keys as people the story never names ("The barbarian with the maul", "The barbarian with the morningstar"; `unnamed`, `must_find` false), since the owner's #3736 and #3740 rule keeps everyone who interacts with the party. "morningstar barbarian" and "maul barbarian" stay on the must-not names: as a name they are still a miss. The check finds a key entry marked `unnamed` only by an unnamed entry's description, reads `must_find` false on entries as it does on ties, and checks must-not names against names only. Totals: master key 97 entries, session key 30.
- **2026-11-08, from the step 10 lore run (export commit abc6b950):** added `l121` / `m135` (Syllar Sparkwalker mentor_of Cerric, lore line 18: he served "under Master Syllar Sparkwalker" alongside "rival apprentices", so he was an apprentice; the key only had works_for). Totals: lore key 121 ties, master key 135. Not added: Glyph travels_with Cerric (line 21, "accompanied by his snowy owl familiar, Glyph", is a real find, but the check has no group for travels_with yet, #3505); the Underdark (named once in passing on line 84; entries have no optional flag, so it would become a must-find); Trex owns Penolopi (the key has it as carries); Torrga in Icewind Dale (she travels, #3363); Lee comes from the Spine of the World (line 42 can be read either way, and the key keeps it on Claude); Vaelrith as the child's uncle (worked out, not stated); the Brotherhood knows Danneth (a misreading: the note says the Brotherhood knows who Cerric is).
- **2026-11-07, from the step 9 lore plus session run (export commit fb847215):** added `l120` / `m134` (Cerric comes_from Temple of the Seldarine, lore line 18: the document says he was born in the temple, the key only had the city) and the alias "Minotaur captain" for the minotaur captain (the deployed build names the entry that way). Totals: lore key 120 ties, master key 134. Not added: ties for events (shoved into the water, burned the ship, sold the figurine), vague "is connected to" ties between towns, Garret and Keegan, the dog's "travels with" (the key has these in other words or scores them elsewhere); "Owlbear" is an entry the key says must not exist (#3373, #3634 fixes it); two person entries the step 9 build made appear in none of the three sample documents and are reported on #3545 as unsupported finds, not added.
