# Reading a DMbot backup

This is for someone who wants to open a DMbot campaign backup and pull the pieces out. The
machine-readable description is [`backup_schema.json`](backup_schema.json). The code is the
authority: `core/src/dmbot/campaigns/store.py` (main file) and `core/src/dmbot/campaigns/parts.py`
(transcripts files). If this page and the code disagree, the code is right; please fix the page.

> **A backup is private to the table.** It holds the whole campaign unabridged: secret names, DM
> notes, and the raw words people said. Do not post it publicly or open files from someone else's.

## What you get from `/dmbot backup`

A backup is a **set of files**:

| File | What it is |
|---|---|
| `campaign.json.gz` (the main file) | Settings and every kind of campaign data except transcripts. |
| one or more transcripts files | Whole sessions with their lines. Only present if the campaign has transcripts. At most 24. |

Older backups are a single plain `.dmbot.json` (not compressed) and have no transcripts files.
Both forms are still readable.

## Step 1: unpack

Every file is **gzip-compressed JSON**, UTF-8. Tell a compressed file from a plain one by its first
two bytes (`1f 8b`), not by its name.

```python
import gzip, json

def read(path):
    raw = open(path, "rb").read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)
```

DMbot refuses anything that unpacks to more than 25 MB, so a single file is never bigger than that.
Do the same limit check if you read files you did not make.

## Step 2: the main file

```json
{
  "format": "dmbot-campaign",
  "version": 4,
  "exported_at": 1730500000,
  "campaign": { ...settings... },
  "sections": { "dms": [...], "memory": [...], ... },
  "parts": { "id": "9f2c...", "count": 2 }
}
```

- **`format`** is always `"dmbot-campaign"`. Anything else is not a DMbot backup.
- **`version`** is the file layout version (currently 4). If you meet a number higher than you
  know, stop: new sections may carry meaning you would drop. What changed:
  - 1: the first layout.
  - 2: compressed on disk; the contents are the same as 1.
  - 3: adds who-knows lists to `memory`. Older files lack them.
  - 4: adds the `session_notes` section.
- **`exported_at`** is Unix seconds (UTC).
- **`parts`** exists only when there are transcripts files. See step 4.

Fields can be missing in files from older versions. The schema file says which and gives the
default each time (for example `table_chat` defaults to `true`). Treat a missing field as its
default, not as an error.

### `campaign`: the settings

Name, ruleset choices, DM-screen visibility, recap switches, how long transcripts are kept, and so
on. **Not included on purpose:** channel, role and message ids (they mean nothing in another
server), the DM's invite link, and who owns the campaign (that is a person's plan, not the
campaign's). Whoever restores the file becomes its DM and owner.

### `sections`: the campaign's data

A map from section name to an **array**. Every section is an array, even if empty. The list of
names is fixed by the DMbot that made the file; a reader that sees a name it does not know should
treat the file as newer than itself.

The three simplest sections are fully specified in the schema:

| Section | Rows |
|---|---|
| `dms` | Discord user ids (strings) of the game masters. |
| `optional_rules` | `{ "rule": "...", "enabled": true }`. |
| `confirmations` | Who confirmed the right to use shared material: `id`, `user`, `purpose`, `fingerprint` (a SHA-256 of the material, never the material), `at`, `restored`. |

**`memory`** is the big one: a flat array of objects, each with a `"table"` key saying what it is
(`entity`, `claim`, `relation`, `belief`, `evidence`, …). Rows refer to each other by id, and the
`table` values in the schema are listed in load order, so anything a row links to appears before
it. Some links are deliberately blanked: a claim's `lines` and an evidence row's `target_id` are
empty, because they would name the speaker of a transcript line and a backup can be read by the
whole server.

The remaining sections (`house_rules`, `homebrew`, `recaps`, `rule_sets`, `game_clock`,
`plot_issues`, and so on) are arrays of objects whose fields come straight from that module's
`dump()`. The schema lists each one with the source file that defines its rows. They are not
spelled out field by field yet; read that file for the exact keys.

**All ids that are Discord user ids are strings**, not numbers, because they are bigger than
JavaScript can hold exactly. Timestamps are integer Unix seconds.

## Step 3: a transcripts file

Same gzip + JSON wrapping, a different format:

```json
{
  "format": "dmbot-transcripts",
  "version": 1,
  "backup": "9f2c...",
  "part": 1,
  "guild": 1234567890,
  "sessions": [
    {
      "session":     { ...one session row... },
      "lines":       [ ...in time order... ],
      "suggestions": [ ... ],
      "respells":    [ { "line": 12, ... } ],
      "import":      null
    }
  ]
}
```

- Each file holds **whole sessions**, oldest first, filled to about 16 MB of text.
- Rows are database rows with their `id`, `guild_id`, `campaign_id` and `session_id` columns
  **removed**. Ids are made again on restore, so a restore next to the original cannot clash.
- **`respells[].line` is a position, not an id.** It is the 0-based index into the same
  session's `lines` array. That is how a respelling finds its line without ids.
- Lines are unabridged: the raw `heard` words, and the DM sidebar lines with their lineage, as the
  shared-transcript rule says.
- `guild` is the server the copy came from. It lets a restore leave out lines of people who erased
  their transcripts after the copy was made. Older files lack it.

## Step 4: putting the set together

Open `campaign.json.gz`. If it has `parts`, then:

1. Collect every transcripts file.
2. Check each file's `backup` equals `parts.id`. A mismatch means it is from another copy.
3. Check each `part` is between 1 and `parts.count`, with no repeats, and that you have exactly
   `parts.count` of them. A missing file is not the same as a campaign with no transcripts; the
   `parts` entry is how you can tell.
4. Read the parts in `part` order. DMbot reads one at a time so memory stays bounded.

If the main file has no `parts`, the campaign has no transcripts files (or the backup is from
before they existed).

## Things to know

- **Not a database dump.** It is an export of DMbot's data in its own shape. Column names inside
  transcripts rows are the database's, and they can change between versions; the `version`
  numbers above are the contract for the main file.
- **Not included in any backup:** audio files of recaps, mentions, ids of channels and messages,
  other people's sign-in or plan data. (Pictures kept with a campaign, #3274, are not yet in
  backups; they will be a later section with their own numbered files.)
- **Validation.** DMbot checks a file fully before it writes anything, and restores in one
  transaction. If you build a restore tool, do the same.
- **Schema is descriptive.** [`backup_schema.json`](backup_schema.json) was written by hand from
  the code. DMbot does not validate against it, and the per-section row shapes beyond the three
  spelled out are not in it. Treat it as a map, and the code as the territory.
