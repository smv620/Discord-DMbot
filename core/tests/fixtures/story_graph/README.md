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

The `.md` files are for reading, the `.json` files for the check; each pair comes from one list, so they cannot disagree.

## The check script

`core/src/dmbot/devtools/graph_key_check.py` scores a campaign **backup** against a key and prints counts and ids: for the entries and for each group of ties (family, work, feelings, things, places, associations) how many were **found**, **missing**, of the **wrong type** (for example family without its role, or "in" where a word was expected), found **only as a waiting suggestion**, plus the **extra** entries and ties the key does not name, the **must-not-find** names that appeared, and the inferred ties that were stored as facts. It reads the backup file only: no database, no Discord, no AI.

```bash
cd core
python -m dmbot.devtools.graph_key_check BACKUP.dmbot.json.gz --docs lore      # after the lore is read
python -m dmbot.devtools.graph_key_check BACKUP.dmbot.json.gz --docs both      # after the session is read
# options: --counts-only (ids and counts, no names)  --json OUT.json  --key OTHER_KEY.json
```

### How dev1 runs it each time (a test campaign, never a real one)

Run it after any deploy that changes what the story reader or the story graph does, on a **throw-away test campaign** made only for this. The documents are public, and the report prints their names, so never run it on any other campaign.

1. In a test server, make a campaign named **test of the frostmaiden (sample)**. Switch story reading on as the current test plan says (`DMBOT_STORY_EXTRACTOR`, and readings if that is part of the run). Say in the log which switches were on.
2. Load the **small sample lore** (link above) as the campaign's lore (the "your notes" upload of the names panel, or the website's lore page). Wait for the read to finish and answer nothing: leave the waiting suggestions as they are.
3. Make a backup (the backup button) and run the check with `--docs lore`. Keep the report.
4. Import the **sample session** (link above) as a past session. Map the transcript's speakers to four test accounts: Cerric to `user_ist`, Belleros to `user_jac`, Okthandom to `user_eri`, Claude to `user_owe` (the made-up names stand for whichever four test accounts are used; say which). The **DM** is a fifth account. Wait for the import to finish, make a backup, and run the check with `--docs both`.
5. Put the **totals and the lists of missing ids** in `docs/testing-history.log` (counts and ids only: the sample's names are public, but the habit is the same), and the latest in `docs/testing-status.log`. Compare with the previous run: **a lower "found" count, a new must-not-find name or a new wrong-type tie is a regression**; say so on #3244.
6. If a run shows a real entry or tie the key missed, or a key row that is wrong, comment on #3244: GraphEngineer updates the keys in a small PR with the reason.

A tie found only as a **waiting suggestion** counts as found-waiting: the reader proposed it and the DM has not said yes. A tie marked **inferred** in the key should only ever be offered; one that is already confirmed is counted so a person can look.
