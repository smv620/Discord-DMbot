# Story graph answer keys (public sample data)

These files say what DMbot's story graph **should** hold after it reads a sample lore document and a sample session.
The player characters and many other names are invented for testing; some towns, gods and non-player characters come from a published adventure (names only, none of its text). The players agreed that their lines may be used for testing, development and public examples.
The documents themselves are linked, not copied.

## The sample documents

- Sample lore (**the one the lore key follows**, the small test bed, about 2,200 words): <https://docs.google.com/document/d/1zbt7g3r6uFfiSYr3fl_gyaxARhwJPizauI0jmiEa2aw/edit?usp=sharing>
- A longer lore document, also public (about 13,500 words, with session recaps, DM prep notes and an entities tab). **No key follows it yet**: <https://docs.google.com/document/d/1lHeFJAheOyz-suXZS644R86dLMt_d5VX7ib6Ig4Ox6Y/edit?usp=sharing>
- Sample session: <https://docs.google.com/document/d/1BtHZ4O75886WHOoTq0FCPst2hdg7YLnsaCiI-kNsv-E/edit?usp=sharing>

## The test players

| Player character | Username |
|---|---|
| Cerric Vanya'elen | `user_ist` |
| Belleros Stormgrace | `user_jac` |
| Okthandom Korran | `user_eri` |
| Claude Ambere | `user_owe` |

These usernames are made up. Use them when loading the sample session, so no real account is needed.

## The keys

| File | What it is | Status |
|---|---|---|
| [lore_key.md](lore_key.md) | Everything the small sample lore should produce, as tables to read, with the document line for each tie | built |
| [lore_key.json](lore_key.json) | The same key, for the check script | built |
| session_key.md / .json | What the sample session should produce | next |
| master_key.md / .json | Lore and session together, plus the ties that cross them | after the session key |

## How the keys are used

Each time the story reader changes, dev1 loads the sample lore and session into a test campaign, takes a backup and compares it to the master key.
The comparison prints counts only: found, missing, extra and wrongly typed ties, split by people, groups, places, items, family and events.
When a test finds something real that the key missed, the key is updated in a small pull request, with the reason.

The comparison script and dev1's run instructions are added with the master key.
