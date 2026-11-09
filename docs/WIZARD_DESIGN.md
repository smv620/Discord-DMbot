# The campaign start-up wizard: core design (#3955, part of #3953)

Author: CloudDev-lead. The rule and the words are in `docs/PLAN.md`, "The campaign start-up wizard".
This note is the table, the doors and the finish run. Supervisor rules on it before code; WebDev-lead
builds the pages against the door names in section 3 (#3956).

## 1. Principles (each follows from a rule that already exists)

- **The website's database role touches no campaign table.** As for every other web feature, the pages
  call `SECURITY DEFINER` doors (`dmbot_web_*`); the doors check the signed-in account
  (`dmbot_current_user()`) and that the server is in the session's own list (`dmbot_web_guilds()`).
- **The website cannot call the AI and does not hold the AI key.** Anything that needs the AI or the
  bot's stores is a **queued row the bot applies** (the pattern of `lore_uploads` 0288, `script_makes`
  0401 and the settings queue). The wizard's finish run is one such row.
- **A draft is not a campaign.** Nothing a campaign owns is created until Finish, so a draft never
  counts against the plan's campaign cap, never has a channel, and leaves nothing behind when it
  expires. Everything it holds is the DM's own answers and documents.
- **"Check only" calls no AI** and reads no names. It opens the file as text and counts pages and words.
- **Counts and stages only** in progress and logs (CLAUDE.md, "Every wait shows..."); no word of a
  document or a name in a log.
- **Never a D&D Beyond sign-in, password or cookie.** The link is kept as the invite; sheets are read
  through the public sheet link as today (#1119 part A).

## 2. The tables (one migration, claimed on #2475 when we start)

All keyed by `(guild_id, user_id)`: **one draft per DM and server**. Not isolated per campaign because
there is no campaign yet; isolated per server and per DM by the door (a DM cannot read another DM's
draft, nor the same DM's draft in another server). They are not part of a campaign backup (a draft
has no campaign), and they are removed with the account (`accounts.delete_person` lists them).

**`wizard_drafts`**: the answers so far.

| column | meaning |
|---|---|
| `guild_id`, `user_id` | primary key. `user_id > 0`. |
| `step` | the step the DM is on (1 to 9), so a reload returns there. |
| `answers` | JSONB, one object, shape checked by the door (see below). Ids, names the DM typed, choices. |
| `created_at`, `updated_at` | seconds. |
| `expires_at` | `updated_at + 7 days`; a press of any door pushes it. Rows past it are deleted by the hourly sweep and refused by every door. |

`answers` keys (the door rejects any other key and any value over its size):
`name`, `invite` (the D&D Beyond join link, normalised by `parse_invite`), `source` (`dndbeyond` |
`file` | `drive`), `page_text_used` (bool: the optional pasted campaign page was read), `characters`
(list, at most 12: `{ "full": str, "party_name": str, "sheet_url": str, "player": "pending" | user id }`),
`target`, `fallback`, `supplements` (list of book codes from the seven), `data_use` (bool, the #933
switch), `skipped` (list of step numbers). Typed text is capped (name 100, a character name 100, a link
2000). No document text lives here.

**`wizard_documents`**: the checked documents of steps 4 (lore everyone knows), 5 (secrets), 6 (the
DM's own ruleset), 7 (house rules) and 8 (homebrew).

| column | meaning |
|---|---|
| `guild_id`, `user_id`, `id` | `id` is 32 hex; key `(guild_id, user_id, id)`; foreign key to `wizard_drafts` `ON DELETE CASCADE`. |
| `slot` | `lore` \| `secrets` \| `ruleset` \| `house` \| `homebrew`. A slot may hold several documents (at most 20 a draft). |
| `name` | file name or link title, as the DM sees it (at most 200). |
| `source` | `upload` \| `link` \| `drive`. |
| `drive_id` | for `drive`, the file id from Google's picker (`drive.file` scope); the bot reads it with the DM's existing Drive link at Finish. Empty otherwise. |
| `text` | the extracted text for an upload (bounded: the lore text limit of 0288, 262,144 characters a document; a bigger file is **refused at the door with the way out**, "split it or use Google Drive"). Blank for `drive` and for a link kept as a link. |
| `pages`, `words` | what check-only counted (`pages` 0 for plain text). |
| `status` | `ok` \| `unreadable`; with `reason` (a short code from a fixed list the page words: `empty`, `scanned`, `locked`, `too_big`, `not_text`). |
| `rights_by`, `rights_at` | the right-to-use confirmation (CLAUDE.md, IP): required before a `ruleset` document is accepted and before **any** of `lore`, `secrets`, `house`, `homebrew` is read by the AI at Finish (the same one confirmation the Discord upload asks for; who and when are recorded on the campaign at Finish, via the existing `campaigns` store call). |

The text of a document is **the DM's own and never echoed back** (as `lore_uploads`): the door returns
name, pages, words, status. It is deleted when the row is consumed by Finish, when the draft is
deleted, or at expiry, whichever first.

**`wizard_finishes`**: the finish queue, one running per `(guild_id, user_id)` (partial unique index, as
`script_makes_one_running`).

| column | meaning |
|---|---|
| `guild_id`, `user_id`, `id` | `id` 32 hex. |
| `state` | `waiting` \| `working` \| `done` \| `failed`. |
| `stage`, `part`, `of_parts` | what it is at (a code from the list in section 4), and how far (for example lore document 2 of 3). |
| `campaign_id` | set once the campaign exists (so a retry never makes a second one). |
| `error` | one of a fixed set of words (section 4). |
| `created_at`, `claimed_at`, `moved_at`, `tries` | for the lost-run rule and "last moved" on the page. |

## 3. The doors (names for WebDev-lead, #3956)

All `SECURITY DEFINER`, all return `NULL` / an error word for anyone but the draft's own DM, all take
the server `g` and read the account from `dmbot_current_user()`. Python wrappers live in
`core/src/dmbot/web/wizard.py` (CloudDev builds them), with `web/app.py` routes under
`/api/wizard/...`. WebDev builds the pages against these and `mock.ts` entries.

| door | does |
|---|---|
| `dmbot_web_wizard_get(g)` | the draft (`step`, `answers`, the documents' name/slot/pages/words/status, `expires_at`) or `NULL`. Never document text. |
| `dmbot_web_wizard_start(g, name)` | makes the draft if none (or returns the existing one); refuses (`no_room`) at once when the plan's campaign cap is reached, so the DM is told before 9 steps (the cap is checked again at Finish). |
| `dmbot_web_wizard_set(g, step, patch)` | merges `patch` into `answers`, moves `step`. The door whitelists keys and checks every value (shape, length, `target != fallback`, supplements among the seven, `parse_invite` for the link). Rejection says which key. |
| `dmbot_web_wizard_skip(g, step)` | records the step as skipped (`skipped` list); the defaults apply at Finish. |
| `dmbot_web_wizard_sheet(g, url)` | queues a read of one public sheet link (see 4.1) and returns its id; `dmbot_web_wizard_sheet_state(g, id)` answers (`waiting` \| `done` with the character's name \| `failed` with a word). |
| `dmbot_web_wizard_page_text(g, text)` | the optional pasted campaign page: checked at the door to the size limit; returns the name and the characters found by the plain reader (4.2) for the DM to confirm in step 2. The text is not kept. |
| `dmbot_web_wizard_doc_add(g, slot, name, source, drive_id, text, pages, words, status, reason)` | adds a checked document. The wrapper does the check-only read (4.3) before calling, so the door only stores what it was given and validates sizes, slot and count. |
| `dmbot_web_wizard_doc_remove(g, id)` | removes one. |
| `dmbot_web_wizard_rights(g, kind)` | the DM's right-to-use confirmation, recorded with the time (the account is the "who"). `kind` is `shared` (for lore, secrets, house, homebrew) or `ruleset`. |
| `dmbot_web_wizard_finish(g)` | checks the draft is complete enough (a name, or Skip), the rights are confirmed for any document that needs them, and the plan has room; inserts a `wizard_finishes` row and returns its id. A second press returns the running one's id. |
| `dmbot_web_wizard_finish_state(g)` | the finish row: `state`, `stage`, `part`, `of_parts`, `error`, `campaign_id`, `moved_at`, `created_at` for the page's progress card; `NULL` if none. |
| `dmbot_web_wizard_discard(g)` | deletes the draft and its documents. |

Skip at any step (`dmbot_web_wizard_skip` then `dmbot_web_wizard_finish`) makes the campaign with the
name the DM typed (or asks for it, as PLAN says) and the defaults, and says so in the notice the page
shows: the words are in PLAN. The page asks for the name before it calls Finish.

**Rate limits**, as the neighbours: `wizard_set` and `doc_add` 60 a minute a DM; `sheet` 10 a minute;
`finish` 3 a minute; the doors return `too_fast` past that.

## 4. Behaviour

### 4.1 Public sheet links (step 1/2)
The door queues the link in the existing character-sheet queue's style; the bot fetches it with the
current reader (`memory/sheets.py`, #1119 part A) and writes back the character's name or a failure
word (`private`, `not_found`, `slow`). The web container never fetches a D&D Beyond page. The link is
kept in the draft's `characters[].sheet_url` and attached to the character at Finish (the existing
`sheet_url` call), visible only as sheet links are today.

### 4.2 Pasted campaign page (step 1, optional)
A plain reader (no AI) that finds the campaign's name and the list of character names in D&D Beyond's
campaign page text. If its shape is not recognised it returns nothing and the page says "I couldn't
find the characters in that text. You can add them by hand." It is a helper for step 2, never a
source of truth: the DM confirms every name.

### 4.3 Check only (steps 4, 5; also the uploads of 6 to 8)
`web/wizard_check.py`, pure and testable: bytes in, `(text, pages, words, status, reason)` out.
Plain text and Markdown are read directly; PDF and Word with the extractors the lore upload already
uses; the same size and page limits. **It imports no AI module** (a test asserts that importing it
loads none of `dmbot.ai`, `dmbot.memory`, `dmbot.extractor`). Words are counted by whitespace split;
pages by the format (PDF page count; Word estimated at 500 words, marked "about"). Unreadable files
say which of the fixed reasons applies and what to do ("This PDF looks scanned: paste its text or
upload a text version").

### 4.4 The finish run (the bot applies the row)
A new loop in `bot.py` (`_wizard_finish_loop`, one task per server, wired like `_script_make_loop`),
module `core/src/dmbot/wizard_finish.py`. It claims one `waiting` row, re-checks that the sender is
the draft's DM and the server's member and that the plan still has room, then runs these stages **in
order, each recording `stage` and `moved_at`** so the page shows where it is and a restart resumes at
the first stage not done:

1. `campaign`: `CampaignStore.create` with the draft's name, target, fallback, supplements and invite
   (one transaction as `create(dndbeyond_invite=...)` does); records `campaign_id` on the row first
   so a resume never makes a second campaign. The right-to-use confirmation is recorded (who, when).
   Channels are made the way `/dmbot` setup makes them, in the DM Bot channel's order (#3874); if the
   bot cannot make them it says so and the campaign is still made (as today).
2. `party`: the "party" group and the player characters as members (GraphEngineer's #3954 call), the
   characters' sheet links attached, each player "pending" where not given.
3. `rules`: the supplements as the campaign's optional-rule books (the choice only, no text); a
   `ruleset` document goes through the existing shared-rulebook flow.
4. `house_homebrew`: the existing house-rule and homebrew imports for each document or link.
5. `data_use`: the #933 switch (`set_share_improve`).
6. `lore`: each `lore` document through the **same read Discord's upload runs**
   (`lore_upload`, "everyone knows" = the world choice), with its usual progress. Then `secrets`
   (the spoilers choice). One document at a time, in the DM's order; `part`/`of_parts` count documents.
7. `sure`: #3878's sure step and then the names to check, with their usual notices and Undo (nothing
   is re-implemented: the same functions the upload path calls at the end).
8. `clean`: the draft and its documents are deleted. The row becomes `done`.

**Failure.** A stage that fails stops the run with one word and leaves what is already made: `plan`
(the cap was reached), `not-allowed`, `gone` (the draft vanished), `no-key` (the DM's AI key or the plan
cannot read lore: the campaign and the earlier stages stand and the page says "Your campaign is
ready. The lore is waiting for an AI key. Add one in Settings; nothing is lost."), `failed` (any other,
logged by class only). A retry from the page runs the same row again from the first stage not done;
`MAX_TRIES` 3. A run waiting or working for more than 30 minutes without moving is taken as lost, as
for scripts. Lore reads that need the AI do not hold up making the campaign: the campaign exists after
stage 1 and the page links to it from then on.

**Plan rules**, as for any new campaign: the cap is checked at `start`, at `finish` and in stage 1
(`campaign_cap.room`); a Try It account gets what Try It gets (no backups, no uploads beyond the
plan's), and a stage the plan does not allow is skipped with the page told ("Your plan doesn't include
reading lore yet").

### 4.5 Discord
`＋ New campaign` offers, first, a link button **Set up step by step on the website** (the site's
`/new?server=<id>`), then the existing short form, unchanged. The button is shown only when the site
address is set. Discord-only on purpose: none other; the parity row says "website-first (uploads and
many choices); Discord keeps its short form".

## 5. Consent, isolation and privacy checks (for review)

- A draft is readable and changeable only by its DM, in its server (door check on every call; tests:
  another DM, the same DM in another server, an expired draft, a non-member).
- Documents are the DM's own and are not shared: they never go in a transcript, a log, an alert or the
  DM screen; a failure shows the file name the DM gave and a reason code, never a line of the text.
- At Finish the lore is read by the existing readers, which already honour campaign isolation and the
  consent rules (no audio or other people's words are involved: this is the DM's own material).
- Nothing the draft holds is a D&D Beyond credential; a link that is not a join link or a public sheet
  link is refused at the door.

## 6. Tests (named)

`test_wizard_doors.py`: each door's happy path and every refusal; draft isolation; the 7-day expiry;
whitelist and size checks; the rate limits. `test_wizard_check.py`: counts for text, Markdown, PDF and
Word; each unreadable reason; **import test that no AI module loads**. `test_wizard_finish.py`: the
stages run in the order above (stub readers record call order); a restart resumes without a second
campaign; `plan` stops at the cap; `no-key` leaves the campaign standing; Skip takes the defaults; the
draft is gone after `done`; progress rows hold counts and stage codes only. `test_wizard_discord.py`:
＋ New campaign offers the link first when the site is set and omits it when not.

## 7. Split of the work (each its own PR, in this order)

1. **Migration and doors** (CloudDev-lead; SQL, RLS, doors; one migration `NNNN_wizard`, claimed first).
2. **Check-only reader and the wrappers/routes** (CloudDev-lead; the reader could go to
   CloudDev-assistant once the shape above is fixed: bytes in, counts out, a named test list).
3. **The finish run** (CloudDev-lead; consent, plan rules, async design).
4. **Discord button and parity row** (CloudDev-assistant: a button like the existing link buttons).
5. **The pages** (WebDev-lead, after step 1 merges, against the door names above).

## 8. Questions for Supervisor

1. **Document storage:** this design keeps an upload's extracted text in `wizard_documents` until Finish
   (so the bot can read it), capped at 262,144 characters a document and 20 a draft. Alternative: keep
   the file in the web container's temporary store and hand the bot a handle: more moving parts, and
   the bot would need to reach the web container. Recommend the table.
2. **Right-to-use wording:** one confirmation per draft covering lore, secrets, house rules and
   homebrew, and a separate one for an uploaded ruleset (as PLAN says). Confirm that is what the owner
   wants, or whether each slot asks.
3. **Who may use it:** any signed-in account that is a DM of a server DMbot is in, or only owners with
   plan room. The design allows any such DM to start and refuses at `start` when there is no room.
