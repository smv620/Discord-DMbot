# Discord ↔ website parity (#3663, Part B)

Goal: every DM-facing thing DMbot does in Discord is on the website, on the way, or Discord-only on
purpose, each with its reason. The rule it will become: *a new DM-facing feature ships in both
places, or files the other half before it merges.*

This file is built by two sessions so neither edits the other's lines:

- **Section 1, the website column** (WebDev-lead, drafted 2026-11-07 from `web/src/account/api.ts`'s
  `AccountApi` and the `*_LIVE` switches): what the website can do today, its page, and whether it is
  live or built and switched off. Read from the code; the switch names are exact, the capability
  names are my reading of them, so CloudDev-lead checks each row against the Discord function.
- **Section 2, the Discord functions** (CloudDev-lead): one row per command, button or card, from
  `ui/`, `bot.py` and the slash commands, each pointing at a Section 1 row, an issue, "missing", or
  "Discord-only: why".
- **Section 3, the table** joins the two. Whoever finishes second writes it, then Supervisor files one
  issue per "missing" row.

"Switched off" means the page and its pretend-API tests exist, and the switch in `web/src/content/`
stays false until the core half is deployed and dev1 has checked it (`docs/GO_LIVE.md` has each flip).

## Section 1: the website column

| Capability (website) | Page | State | Switch / held on |
|---|---|---|---|
| Sign in with Discord or email, sign out, sign out everywhere | `/account` | live | none |
| Try It, buy or renew a plan, the billing portal, extra hours | `/account`, `/pricing` | live | payments on |
| Export my data; my email (add, change, confirm); support messages and replies | `/account` | live | none |
| Add the bot to a server; link a server | `/install`, `/account` | built, switched off | `INVITE_LIVE` |
| Campaigns list and a campaign's home page | `/account/campaigns` | live | none |
| Campaign settings; optional rules; game masters (co-DMs); character sheets; pause; improvement sharing | campaign home | built, switched off | `OPTIONAL_RULES_LIVE`, `CO_DMS_LIVE`, `SHEETS_LIVE`, `PAUSE_LIVE`, `SHARE_STORY_LIVE` (improvement: built and switched off, #3741, #3784) |
| Hand a campaign over (offer, accept, decline, take back, by email too) | `/handover`, campaign home | built, switched off | `HANDOVER_LIVE` |
| Backup download; load a backup; delete a campaign | campaign home, `/account/restore` | backup and delete live; load a backup switched off | `WEB_RESTORE_LIVE` |
| Recaps: list, read, edit, make again, approve, approve unchecked, audio (play, make again) | `/account/recaps` | live | `RECAPS_LIVE`, `RECAP_AUDIO_LIVE` |
| Recap must-haves and defaults | recap page | live | `RECAP_MUSTS_LIVE` |
| Recap style (how recaps are written) | `/account/recap-style` | built, switched off | `RECAP_STYLE_LIVE` |
| Share a recap; remake a recap | recap page | built, switched off | `RECAP_SHARE_LIVE`, `RECAP_REMAKE_LIVE` |
| Fix a name's words in a recap; the narrator's way to say a name (type it, suggest it, say it) | recap page | built, switched off | `RECAP_NAMES_LIVE`, `RECAP_FIX_LIVE`, `RECAP_VOICE_LIVE` (🎤 waits for the bot, #3828; server `SAID_AS_VOICE`) |
| Hear a name in the recap narrator's voice (🔊 Hear it beside the "said as" box; the bot makes the clip, the browser's voice reads it when narration is off) | Names page, names to check | built, switched off | `HEAR_NARRATOR_LIVE` (#3807; core #3817) |
| "Players know:" on a suggested name before the DM confirms it (the line a shared lore upload filled in), with a way to change it | recap page, names to check | built, switched off | `PLAYERS_LINE_LIVE` (#3833; Discord's card is #3838) |
| Search the website: the best-matching page at once (a search of the site itself, so website-only on purpose; Discord already answers questions about DMbot through table questions) | site header | built, switched off | `SEARCH_LIVE` (#3855) |
| Ask the website a question: a short answer under the search box, written from the site's own public words (website-only on purpose, as the search) | site header | built, switched off | `SEARCH_ASK_LIVE` (#3855; core #3856) |
| After-session report, session notes, who was in each scene | recap page, campaign | built, switched off | `AFTER_SESSION_REPORT_LIVE`, `SESSION_NOTES_LIVE`, `PRESENT_PER_SCENE_LIVE` |
| Names: list, search, edit, merge, split, notes on an NPC (NPC page) | `/account/names`, `/account/npc` | live (split off) | `NAME_SPLIT_LIVE` |
| Search names by meaning | `/account/names` | built, switched off | `NAMES_SMART_LIVE` |
| Names fixed this session, with Undo | recap page | built, switched off | `NAMES_FIXED_LIVE` |
| Names DMbot has met but not named yet (name them, remove, same as someone) | `/account/names` | live | `NOT_NAMED_LIVE` |
| Graph clean-up: the people and groups not linked to a player character, with their scenes, Add a connection and the Names page's panel | `/account/web` (the Clean up the map button) | built, switched off | `GRAPH_CLEANUP_LIVE` (#3740); Discord shows a count with a link (CloudDev-lead, after this page): desk work, so the page is the place to work |
| People the story never named, drawn on the map for the DM (dashed node, the same three actions, a Graph settings kind, a line for the waiting ones) | `/account/web` | built, switched off | `UNNAMED_MAP_LIVE` (#3732, #3734) |
| Who is this? for one name (the clarify page) | `/account/clarify` | in an open PR (not yet on development), behind its own switch | `name-clarify-switch` |
| The names waiting for a check; the recap's name checks | `/account/questions`, recap page | built, switched off | `QUESTIONS_LIVE`, `RECAP_NAME_CHECKS_LIVE` |
| Confirm kinds of names; places written two ways | Names, Story Graph | built, switched off | `CONFIRM_KINDS_LIVE`, `NAME_PLACES_LIVE` |
| Story Graph: view, details panel, settings, centre on a name | `/account/web` | live | none |
| Story Graph: waiting connections (answer, All true / All secret / Undo all) | `/account/web` | live | none |
| Story Graph: what people believe (readings); who knows a connection; secret names told; peek and reveal | `/account/web` | readings live; others switched off | `WHO_KNOWS_LIVE`, `TOLD_LIVE` |
| Story Graph: change a connection, its feeling | `/account/web` | change live; feeling picker is in an open PR, switched off | `knows-feeling-switch` |
| Story Graph: PC icons | `/account/web` | in an open PR (not yet on development), switched off | `pc-icons-switch` |
| Node summary on the graph | `/account/web` | built, switched off | `NODE_SUMMARY_LIVE` |
| Transcripts: read, download (text, theatre), put back or leave out a stretch | `/account/transcripts` | live | `THEATRE_DOWNLOAD_LIVE` |
| Fix one transcript line; delete my own lines; remove a speaker | `/account/transcripts`, `/account` | built, switched off | `FIX_LINE_LIVE`, `DELETE_MY_LINES_LIVE`, `SPEAKER_REMOVAL_LIVE` |
| Stop recording me | `/account` | live | none |
| Lore upload and reading | `/account/lore` | built, switched off | `LORE_LIVE` |
| Import older sessions (preview, members, consent, start, progress, clock) | `/account/import` | built, switched off | `IMPORT_LIVE`, `IMPORT_CONFIRM_LIVE` (consent), `IMPORT_CLOCK_LIVE` |
| House rules (read), the house-rules file, rules sharing and rulesets | `/account/house-rules`, `/account/sharing` | read live; file and sharing switched off | `HOUSE_FILE_LIVE`, `RULE_SHARING_LIVE`, `RULESETS_LIVE` |
| Homebrew (read and file) | `/account/homebrew` | live | none |
| Pictures gallery | `/account/pictures` | built, switched off | `PICTURES_LIVE` |
| Story timeline | `/account/timeline` | live | none |
| Plot check (ask, answer an issue, chat, decide a proposal) | campaign | built, switched off | `PLOT_CHECK_LIVE` |
| Story mode | campaign | built, switched off | `STORY_MODE_LIVE` |
| Support helper answers | `/account` | built, switched off | `SUPPORT_AI_LIVE` |
| The owner's admin page: free access, support tickets, costs | `/admin` | live (owner only) | none |

## Section 2: the Discord functions

Written by CloudDev-lead on 2026-11-07 from the slash commands (`ui/`, `questions/`, `bot.py`), the
DM-screen cards and buttons (`dm_screen/`) and the private-message cards (`ui/`). One row for each
function a DM uses. **Answer** is a Section 1 row (named by its capability), an open issue, `missing`,
or `Discord-only: why`. A row marked ⚠ is my reading and WebDev-lead should confirm the Section 1
match. "In Discord" says where the DM finds it today.

### Slash commands

| Function | In Discord | Answer |
|---|---|---|
| Start listening (pick the campaign and the voice channel) | `/dmbot start` | Discord-only: choosing the voice channel and joining it is live at the table |
| Stop listening | `/dmbot stop`, 🛑 on the DM screen | Discord-only: ending the live recording |
| Open the campaign's settings | `/dmbot settings`, ⚙️ | Campaign settings (campaign home) |
| Help | `/dmbot help` | Discord-only: a pointer to the commands |
| Questions waiting for me | `/dmbot questions`, 📬 on the Manage card | The names waiting for a check (`/account/questions`, #3438) |
| Make or remake a recap | `/dmbot recap`, 📜 Recaps | Recaps: make again |
| Add a recap someone wrote | `/dmbot recap-upload` | Recaps: add a recap someone wrote (`addRecap`, on the make-a-recap page; live) (WebDev confirmed) |
| Fix a line heard wrong | `/dmbot fix` | Fix one transcript line (`FIX_LINE_LIVE`) |
| Delete my own lines | `/dmbot delete-my-lines` | Delete my own lines (`DELETE_MY_LINES_LIVE`) |
| Back up a campaign | `/dmbot backup` | Backup download |
| Restore from a backup | `/dmbot restore` | Load a backup (`WEB_RESTORE_LIVE`, switched off) |
| Bring in older sessions | `/dmbot import` | Import older sessions (it already says "on the website") |
| Teach DMbot the campaign's names | `/dmbot names` | Names: list, search, edit |
| House rules (see, add, edit, remove) | `/dmbot houserules` | House rules (read live; the file is switched off) |
| Optional rules (Xanathar's, Tasha's) | `/dmbot optionalrules` | Campaign settings: optional rules (`OPTIONAL_RULES_LIVE`) |
| Homebrew (own spells, monsters, items) | `/dmbot homebrew` | Homebrew (read and file) |
| Look up a rule, spell, creature | `/dmbot rule` | missing ⚠ (a rules lookup page); it is also live help at the table, so the owner may rule it Discord-only |
| Move my plan and campaigns to a new Discord account | `/dmbot migrate` | Account: email the move code (`getMoveCode`, live); the move itself is typed in Discord with that code, so that last step is Discord-only (WebDev confirmed) |
| Give or take back consent to be recorded | `/consent give`, `/consent revoke` | Discord-only: a player's own consent; the website has its own consent screens |

### The DM screen (the buttons on the card in the DM-screen channel)

| Function | In Discord | Answer |
|---|---|---|
| Who can see the DM screen (private, peek, open); Peek behind the DM screen | 👁️ / 🔓 buttons | Campaign settings: who can see the DM screen (`visibility`, in `CampaignSettings`); peek and reveal are Story Graph: peek and reveal (WebDev confirmed) |
| Hide or show the DM screen | Hide / Show | Discord-only: it is the Discord channel's own card |
| Pause and resume the table | Pause, Resume now | Pause (`PAUSE_LIVE`) |
| Quiet stop (stop the recording when the table is quiet) | Campaign management | Discord-only: live recording |
| Transcript retention (how long transcripts are kept) | Campaign management | Campaign settings: how long transcripts are kept (`retention`, in `CampaignSettings`) (WebDev confirmed) |
| Table chat on or off, and which chat | Table chat | Discord-only: the table's chat is a Discord channel |
| Rules level (how much DMbot says) and Features on or off | Campaign management | Rules level: Campaign settings (`level`). Features: a read-only list (`FEATURES_LIST_LIVE`, #3693, #3718); turning features on or off stays in Discord until filed |
| Game masters: add, remove | Game masters | Campaign settings: game masters (`CO_DMS_LIVE`) |
| Hand over the campaign: offer, accept, decline, withdraw | Handover buttons | Hand a campaign over (`HANDOVER_LIVE`) |
| Delete this campaign | Delete this campaign | Backup and delete (delete is live) |
| Campaign invite link; D&D Beyond invite | Campaign management | Campaign settings: the invite panel (`InvitePanel`, `INVITE_LIVE`, switched off) (WebDev confirmed) |
| Give your adventure (Shared story); read it; match it to files; refresh; remove | Shared story, adventure card | Lore upload and reading, with the right-to-use box (`LORE_LIVE`); matching to files, refresh and remove not checked ⚠ |
| Story mode (the story guide choice) | 📖 Story | Story mode (`STORY_MODE_LIVE`) |
| Import button and the import's clock offer | Import, the clock card | Import older sessions: clock (`IMPORT_CLOCK_LIVE`, #3199); consent (#3306) |
| Transcripts: download, keep, channel | Transcripts card | Transcripts: read, download |
| Recap style (how recaps are written) | Recap style | Recap style (`RECAP_STYLE_LIVE`) |
| Shared rules: follow or publish a set; update notes; pack suggestions | Shared rules, update cards | House rules: rules sharing and rulesets (`RULE_SHARING_LIVE`, `RULESETS_LIVE`) |
| House-rules file: sync, accept, review | House-rules file card | House rules: the file (`HOUSE_FILE_LIVE`) |
| Backstory (a character's backstory file) | Backstory button | Story Graph: waiting connections (backstory links are answered there, `backstory-keep`) (WebDev confirmed) |
| Clock, timers and effects (the table's game time) | Clock and effects cards | Discord-only: running game time at the table |
| Initiative and fights (fightbot) | live commands | Discord-only: the initiative tracker during play |
| Character sheets (link a sheet, open a card, re-read) | My sheet, Open card | Campaign settings: character sheets (`SHEETS_LIVE`) |
| Guide me (🧭 what to do next) | 🧭 button | missing ⚠ |
| Theatre (the transcript as a script) | Theatre button | Transcripts: download theatre (`THEATRE_DOWNLOAD_LIVE`) |

### Private-message cards (the questions DMbot asks the DM)

| Function | In Discord | Answer |
|---|---|---|
| Check new names (one by one; Add all N; Undo) | 📝 Check new names, read summary (after a lore upload the summary with Add all is a counts-only message in the DM Bot channel, and the private question's buttons open it: Discord-only on purpose, which Discord message opens, #3873) | The names waiting for a check; Add all (`QUESTIONS_LIVE`, #3207); Confirm kinds (`CONFIRM_KINDS_LIVE`) |
| Who is this? (same as a known name) | name card, look-alikes | Who is this? (the clarify page, #3604) |
| Names heard but not named yet | heard review, unnamed | Names DMbot has met but not named yet |
| Which one? (Which Mara?) | connections review | Story Graph: waiting connections (#3463) |
| Check the connections; Keep, Keep as secret, No, Keep all; Undo | Check the connections | Story Graph: waiting connections (live) |
| Said at the table (claims): true, secret, All true, All secret | claims review | Story Graph: waiting connections (All true / All secret, #3640, #3645) |
| Family ties | family ties card | Story Graph: waiting connections (family ties are one of its kinds) (WebDev confirmed) |
| Change a connection; its feeling | connection edit | Story Graph: change a connection; feeling picker (#3607) |
| Who knows this; tell one character a secret name; take it back | who-knows, told name | Story Graph: who knows, secret names told (`WHO_KNOWS_LIVE`, `TOLD_LIVE`, #3542) |
| Reveal a secret; secret said aloud; revealed report; Recant | reveal cards | Story Graph: peek and reveal (`webPeek`, `webReveal`) (WebDev confirmed) |
| Places written two ways | name question | Names: places written two ways (`NAME_PLACES_LIVE`, #3533) |
| Make these words a kind of connection | kind offer | #3577 |
| The party: player characters; Who plays them | party note, name card | Story Graph: PC icons (#3622); assigning a player to a player character: Names, change the kind to player character with their Discord ID (WebDev confirmed) |
| Deeds the party did | deeds card | #3591 |
| What the characters learned (Keep / Not this) | reveal card | #3863 |
| Unnamed people (a scene's summary) | unnamed card | #3595, #3597, the scene (#3644) |
| The recap's names to check; the DM's fix to a name in a recap | recap names card | The recap's name checks (`RECAP_NAME_CHECKS_LIVE`); Fix a name's words (`RECAP_FIX_LIVE`) |
| Recap ready, failed, unchecked, hold; Write now; share; feedback | recap cards | Recaps: approve, approve unchecked, share, edit |
| Recap audio: ask, remake | audio cards | Recaps: audio |
| Session notes; after-session report | notes and report cards | `SESSION_NOTES_LIVE` (#3027), `AFTER_SESSION_REPORT_LIVE` |
| Rule clashes (house rule vs rule); rules alerts | clash card | on the way (#3697): the core half is built (the decided pairs, change one, clear one, own rules only; a pair with a followed set's rule is read-only there), the page is WebDev's; "Which wins?" after a save waits for the website rule editor (#3775); the clash is found by one AI call when a rule is saved, nothing is stored but the DM's answer; live alerts are Discord-only |
| Plot check (ask, answer an issue, decide a proposal) | plot cards | Plot check (`PLOT_CHECK_LIVE`) |
| Continuity and contradictions | continuity card | missing ⚠ |
| Threads (open plot threads) | threads card | missing ⚠ |
| Live heads-up (a secret about to be said) | live card | Discord-only: live at the table |
| Questions for you (notice, reminders, 🔕 Not now) | private notices | The Questions page; notices are Discord-only |
| Pictures: kept pictures, looked-at pictures, image fix | picture cards | Pictures gallery (`PICTURES_LIVE`) |
| Speaker questions (who said this?) | speaker question card | Discord-only on purpose (Supervisor, #3698): the question is live at the table; "Fix one transcript line" covers fixing a speaker afterwards; remove a speaker is `SPEAKER_REMOVAL_LIVE` |
| Account move notices; plan and trial messages | private messages | The account page shows the plan and ends; the notices are Discord-only (WebDev confirmed) |

### Gaps this list found

CloudDev-lead's candidates were: recap upload, rules lookup, account move, DM-screen visibility and
peek, transcript retention, rules level and features, the campaign invite link, Guide me, continuity,
open plot threads, assigning a player to a player character, plus the rows marked ⚠. WebDev-lead's
check against Section 1 is in Section 3.

## Section 3: the table

Written by WebDev-lead on 2026-11-07, after checking each ⚠ row of Section 2 against the website's code
(`web/src/account/`). Five candidates turned out to be on the website already, so they are not gaps.

### On the website already (the candidates that are not gaps)

| Function | Where on the website |
|---|---|
| Add a recap someone wrote | Make-a-recap page (`addRecap`, live) |
| Account move | The move code is emailed from the account page (`getMoveCode`); the final step is typed in Discord |
| Who can see the DM screen | Campaign settings (`visibility`) |
| How long transcripts are kept | Campaign settings (`retention`) |
| Assign a player to a player character | Names: change the kind to player character, with the player's Discord ID |
| Campaign invite link | The invite panel in campaign settings (`INVITE_LIVE`, switched off, so on the way) |

### Missing: the gaps Supervisor files, one issue each

| Gap | Discord | Note |
|---|---|---|
| Features on or off (turning them) | Campaign management | the website lists them read-only (#3693); the switches stay in Discord |
| Guide me (what to do next) | 🧭 button | no website page |
| Continuity and contradictions | continuity card | the website has no page for them |
| Open plot threads | threads card | the website has no page for them |
| Rule clash (a house rule against a rule being checked) | clash card | on the way: #3697 (decided pairs now; "Which wins?" at website save, after the editor #3775) |
| Rules lookup | `/dmbot rule` | also live help at the table: the owner may rule it Discord-only |

### Switched off and waiting (counted as "on the way", already filed)

Every row in Section 1 marked "built, switched off" or "in an open PR" is on the way: its switch and
`docs/GO_LIVE.md` row say what it waits for. Nothing in that group needs a new issue.

## Discord-only on purpose (starting list, from #3663)

- Which voice channel the bot listens to, the table's text channel and the DM-screen channel.
- Joining, leaving and recording in voice, and anything live at the table: "Hey DMbot" answers, live
  rules alerts, the initiative tracker during play.
- Discord server roles and permissions, and inviting the bot.
- A player's own consent button in their private messages. The website has its own consent screens,
  and the consent rules stay as they are.

## Website-only for now

- "Share this campaign's story to improve DMbot" (#3741): the DM's switch, off by default, in the
  website's campaign settings (Supervisor's ruling on #3741). Discord shows it read-only in what is
  switched on (#3693).
