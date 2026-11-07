# Story graph answer keys (public sample data)

These files say what DMbot's story graph **should** hold after it reads a sample lore document and a sample session.
The player characters and many other names are invented for testing; some towns, gods and non-player characters come from a published adventure (names only, none of its text). The players agreed that their lines may be used for testing, development and public examples.
The documents themselves are linked, not copied.

## The sample documents

- Sample lore (**the one the lore key follows**, the small test bed, about 2,200 words): <https://docs.google.com/document/d/1zbt7g3r6uFfiSYr3fl_gyaxARhwJPizauI0jmiEa2aw/edit?usp=sharing>
- A longer lore document, also public (about 13,500 words, with session recaps, DM prep notes and an entities tab). **No key follows it yet**: <https://docs.google.com/document/d/1lHeFJAheOyz-suXZS644R86dLMt_d5VX7ib6Ig4Ox6Y/edit?usp=sharing>
- Sample session (**the one the session key follows**, read together with the small lore): <https://docs.google.com/document/d/1BtHZ4O75886WHOoTq0FCPst2hdg7YLnsaCiI-kNsv-E/edit?usp=sharing>

## The test players

| Player character | Username |
|---|---|
| Cerric Vanya'elen | `user_ist` |
| Belleros Stormgrace | `user_jac` |
| Okthandom Korran | `user_eri` |
| Claude Ambere | `user_owe` |

These usernames are made up. Use them when loading the sample session, so no real account is needed.

## The keys

| File | What it is |
|---|---|
| [lore_key.md](lore_key.md) / [.json](lore_key.json) | What the small sample lore should produce, with the document line for each tie |
| [session_key.md](session_key.md) / [.json](session_key.json) | What the sample session should produce, with the transcript line for each tie |
| [master_key.md](master_key.md) / [.json](master_key.json) | The two together, each tie once: lore first, then session (where they disagree about what is current, the session wins). **This is the one the check scores.** |

A key entry marked `"by_design": true` is a **kind** of creature the rules already hold (a yeti, an ice troll, a mind flayer): DMbot leaves it out of story memory on purpose (`in_the_rules`), so the check does not count it as a miss when it is absent; it lists it on its own "left out by design" line so a change in that rule shows up (Supervisor, 2026-11-06, #3545). A particular creature the lore names or sets apart (a named owlbear) is an entry and stays must-find.

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

`sample_run` does steps 1 to 4 below by itself, with no one in Discord: it deletes the last run's sample campaign and makes a new one (only that exact name, and only when it is plainly the tool's), queues the lore the way the website's upload does and waits for the running bot to read it, accepts what was found, backs up and scores the lore **exactly as the reader left it** (the report says how many of the four player characters the read found by itself), only then sets the four itself (Cerric to `ist`, Belleros to `jac`, Okthandom to `eri`, Claude to `owe`, through the store's own "who plays" calls, so `--players` must give those four labels), imports the session file as a past session mapped to the four test accounts, waits for the bot, accepts again, backs up and scores both, and writes the recap with no audio. It prints one block of counts and times to paste on #3545, where regressions are reported. Download the two documents above as plain text files first (the session in a timestamped form the import reads, such as `[0:00:05] ist: ...`, with the speakers named by the labels you give).

```bash
cd core
python -m dmbot.devtools.sample_run --guild GUILD --dm DM_USER_ID \
    --players ist=ID,jac=ID,eri=ID,owe=ID --lore lore.txt --session session.txt [--keep-old]
# the first run prints the most paid AI calls it can use and stops; add --yes to run it
```

It needs `DATABASE_URL` and `ANTHROPIC_API_KEY` in its environment, the bot running with the lore and story switches on as the test plan says, a DM account with a paid plan or free access (the tool never grants one), and the four test accounts must already have agreed to being recorded in Discord (the tool checks this before it spends anything and stops if one has not, or has said no). Backups go to `--backup-dir` (default `/var/lib/dmbot-sample-backups`, outside the repository). The report names which `DMBOT_` switches were on. The manual steps below are what the command does, for when one step must be run alone.

### How dev1 runs it each time (a test campaign, never a real one)

Run it after any deploy that changes what the story reader or the story graph does, on a **throw-away test campaign** made only for this. The documents are public, and the report prints their names, so never run it on any other campaign.

1. In a test server, make a **new** campaign named **test of the frostmaiden (sample)** for each run (delete the last run's, so old confirmations do not count). It must be a **paid campaign with a free slot**: Try It campaigns have no backup button. The check refuses any other campaign name. Switch story reading on as the current test plan says (`DMBOT_STORY_EXTRACTOR`, and readings if that is part of the run). Say in the log which switches were on. The override flag `--i-know-this-is-the-test-campaign` is never used on a real campaign.
2. Load the **small sample lore** (link above) as the campaign's lore (the "your notes" upload of the names panel, or the website's lore page). Wait for the read to finish, then **accept everything that waits** with `accept_all` (below), so no one has to press anything in Discord. (The old way, answering nothing and leaving the suggestions waiting, is still worth one optional second pass for comparison: the check then scores them as found-waiting.)
   `accept_all` (#3544) is dev1's command, run on the server as the normal `dmbot` user (the database refuses a superuser, as for `movers_check`): `cd core && python -m dmbot.devtools.accept_all --guild GUILD_ID --campaign CAMPAIGN_ID --dry-run` counts what it would accept, and the same line with `--yes` instead of `--dry-run` does it. It confirms every waiting name, other name, connection, "Which one?" and same-person question through the store's own DM calls (so each rule is checked again and each change can be undone), and prints counts only (accepted, left waiting, refused by a rule). A name that needs a person to say who plays it, and a question with no suggestion, stay waiting. It refuses any campaign not named **test of the frostmaiden (sample)**, with no override.
3. Make a backup (the backup button, then download the attached file; it holds the test campaign's DM secrets, so keep it off the repo and out of issues) and run the check with `--docs lore --json lore-run.json`. Keep the report and the json file for the comparison in step 5.
4. Import the **sample session** (link above) as a past session. Map the transcript's speakers to four test accounts: Cerric to `user_ist`, Belleros to `user_jac`, Okthandom to `user_eri`, Claude to `user_owe` (the made-up names stand for whichever four test accounts are used; say which). The **DM** is a fifth account. Give the import-consent confirmation the uploader is asked for. Wait for the import to finish, run `accept_all --yes` again (the session's suggestions), make a backup, and run the check with `--docs both --json both-run.json`.
5. Put the **totals and the lists of missing tie ids** (entries have no ids: the count is enough) in `docs/testing-history.log` (counts and ids only: the sample's names are public, but the habit is the same), and the latest in `docs/testing-status.log`. Compare the json files with the previous run's: **a lower "found" count, a new must-not-find name or a new wrong-type tie is a regression**; report it on #3545 (the sample run's issue).
6. If a run shows a real entry or tie the key missed, or a key row that is wrong, comment on #3244: GraphEngineer updates the keys in a small PR with the reason.

**The script check (#3385, once the resolver is deployed):** after step 4, make the session's script (the "Make a script of this session" button) and read it. Count, and record in the log (counts only): lines where a form of address or title is followed by a bracket (the sample's tavern line should read like "Hello, Friend [Belleros]"), brackets turned into `[unknown]`, and that **no entry or other name was added** by the run. A form of address must never show up in the names list.

A tie found only as a **waiting suggestion** counts as found-waiting: the reader proposed it and the DM has not said yes. A tie marked **inferred** in the key should only ever be offered; one that is already confirmed is counted so a person can look.
