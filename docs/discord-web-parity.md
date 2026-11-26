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
| Campaign settings; optional rules; game masters (co-DMs); character sheets; pause; improvement sharing | campaign home | built, switched off | `OPTIONAL_RULES_LIVE`, `CO_DMS_LIVE`, `SHEETS_LIVE`, `PAUSE_LIVE`, `SHARE_STORY_LIVE` (improvement: built and switched off, #3741, #3784), `MAP_CHECKS_LIVE` (map checks: built and switched off, #4687) |
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
| After-session report, who was in each scene | recap page, campaign | built, switched off | `AFTER_SESSION_REPORT_LIVE`, `PRESENT_PER_SCENE_LIVE` |
| Session notes and the script, read on a session's Transcripts page (Script and Session notes tabs, game masters only) and saved | `/account/transcripts` | live (#3898) | `SESSION_NOTES_LIVE`, `THEATRE_DOWNLOAD_LIVE` |
| Make a session's script from the website (Discord offers it after a live session; imported sessions get the offer too, CloudDev's half of #3899) | `/account/transcripts` Script tab | built, switched off: the page, its door and the bot's applier are merged (#3932, #3952) | `SCRIPT_MAKE_LIVE` (#3899) |
| Biggest file a DM can add: a PDF up to 100 MB in Discord, 50 MB on the website (its request goes through the tunnel as base64); every other type 10 MB. **Level but for the PDF number, on purpose** | Discord uploads, `/new` wizard | core built (#4001); the wizard page's limit is the web half | |
| Set up a campaign step by step (uploads, many choices and a Back button need a page; Discord's `＋ New campaign` offers it first and keeps its short form). **Website-first on purpose.** | `/new?server=<id>` | pages built, switched off; the doors and the finish run are CloudDev-lead's (#3955) | `WIZARD_LIVE` (#3956) |
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
| A quest's sheet: what it's about, who gave it, the reward, its state and steps (#4540) | the 🧵 card | Story Graph detail pane (DM only): built, switched off, `QUEST_SHEET_LIVE` (#4547) | `QUEST_SHEET_LIVE` |
| A character's languages: the sheet's (read only) or the story's, the ones DMbot found marked "not checked yet", each added, taken away or marked right by the DM; never shown to players | 🗣️ Languages on the character's name card (DM only, #4229 slice 1b) | The read door and the changes (the queue door's `lang-*` actions) are built; the page is built, switched off, `LANGUAGES_LIVE` (#4271, migration 0519). Language questions on ❓ Questions for you follow |  |
| A kept story thread's steps, rewards and more: add, change, state, show to the table, take off, and its branches (#4476) | the 🧵 card, "📋 Steps, rewards and more" | the read door and the changes (the queue door's `part-*` and `branch-*` actions) are built; the page is next, switched off. A loan's terms are added with a change afterwards, not in the add form. A time limit's date is set (N game days from the game clock, counted by the bot) or cleared by `part-date` (#4503); the card reads a dated limit's countdown words (`dateWords`, from the clock door, #4503); the page's 📅 buttons follow |  |
| Story Graph: waiting connections (answer, All true / All secret / Undo all) | `/account/web` | live | none |
| Story Graph: what people believe (readings); who knows a connection; secret names told; peek and reveal | `/account/web` | readings live; others switched off | `WHO_KNOWS_LIVE`, `TOLD_LIVE` |
| Story Graph: change a connection, its feeling | `/account/web` | change live; feeling picker is in an open PR, switched off | `knows-feeling-switch` |
| DMbot adds what it is sure of after a lore upload or an import: the counts notice (and the list of what a 🌍 upload revealed, #4661), Undo this upload (7 days), the "From <upload>" Names filter, and "Ask me before adding" (Discord's twin is #3887) | lore and import pages, `/account/names`, campaign settings | page built, switched off; the doors are next | `AUTO_ADD_LIVE` (#3879) |
| A lore PDF: its pages and pictures are counted and the cost shown before the DM's yes, then it is read pictures included (Discord's twin is the 🤖 upload of a PDF, #4003) | `/account/lore`, "A PDF" | page built, switched off, with the picture-reading deploy | `LORE_PDF_LIVE` (#4762) |
| The connection panel on the Story Graph: a compact list with Add a connection on top, edits kept until Save, the direction toggle, Ctrl/⌘+click or Connect to another name to join two names, a jump from a name's panel, and ↩ Undo this save (#4155; Discord's own menus are #4154 and #4156) | the connection edit menu in Discord | `/account/web` | built, switched off | `CONNECTION_PANEL_LIVE` (dev1 checks before the flip) |
| How two people know each other on a `knows` connection: "has met", "has heard of", or plain "knows", and the DM's change (#3882; Discord's card and edit menu are #3889) | `/account/web` | page built, switched off; the website's door is next | `KNOWS_HOW_LIVE` |
| Story Graph: PC icons | `/account/web` | in an open PR (not yet on development), switched off | `pc-icons-switch` |
| Node summary on the graph | `/account/web` | built, switched off | `NODE_SUMMARY_LIVE` |
| Transcripts: read, download (text, theatre), put back or leave out a stretch | `/account/transcripts` | live | `THEATRE_DOWNLOAD_LIVE` |
| Fix one transcript line; delete my own lines; remove a speaker | `/account/transcripts`, `/account` | built, switched off | `FIX_LINE_LIVE`, `DELETE_MY_LINES_LIVE`, `SPEAKER_REMOVAL_LIVE` |
| Stop recording me | `/account` | live | none |
| Lore upload and reading | `/account/lore` | built, switched off | `LORE_LIVE` |
| A lore PDF's pictures (maps, tables, handouts) taken out and their cost shown before the yes, then read and kept for the DMs alone, and each map asked about once to become a DM-only map (#4003, #4259; Discord's lore file upload, behind `DMBOT_PICTURE_READING`, the maps also behind `DMBOT_MAP_LAYERS`) | `/account/lore` | missing: the website's lore upload takes text only; #4762 (a PDF upload) and #4761 (the gallery's "DM only" mark) | none yet |
| Import older sessions (preview, members, consent, start, progress, clock) | `/account/import` | built, switched off | `IMPORT_LIVE`, `IMPORT_CONFIRM_LIVE` (consent), `IMPORT_CLOCK_LIVE` |
| House rules (read), the house-rules file, rules sharing and rulesets | `/account/house-rules`, `/account/sharing` | read live; file and sharing switched off | `HOUSE_FILE_LIVE`, `RULE_SHARING_LIVE`, `RULESETS_LIVE` |
| Homebrew (read and file) | `/account/homebrew` | live | none |
| Pictures gallery | `/account/pictures` | built, switched off; DM-only pictures from documents are marked (#4761) | `PICTURES_LIVE` |
| Maps page: the list, a DM's yes or no, show or hide for the table, remove, a poster's take-back, Undo | `/account/maps` | built, switched off | `MAPS_LIVE` (#4347); Discord: `/dmbot maps` shows a map to the table or hides it, with the same warning and an Undo (#4414); the rest of the page (the DM's yes or no, a poster's take-back) is in Questions and the map's own question |
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
| ＋ New campaign | `＋ New campaign` offers a link to the start-up wizard first (when `DMBOT_WIZARD_LINK` is on and a website address is set); the short form stays one press away | Set up a campaign step by step (Section 1, `/new?server=<id>`). Website-first on purpose: uploads and many choices need a page |
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
| Who knows what was said: 👥 Some know on the "What was said" page's cards and on a backstory connection's card, with DMbot's guess ticked and a "Known to:" line (#4534, #4681, #4688) | After-session "What was said" page; the backstory connection card, and the claim cards (#4680), behind `SOME_KNOW_LIVE`. DMbot ticks nobody on the website (Discord ticks who was there) | "Waiting for you": 👥 Some know on a waiting claim, nobody ticked (doors #4709, migration 0535; page #4718, built, switched off, `SOME_KNOW_LIVE`); on a waiting backstory connection, door #4723 (migration 0537), page next (#4680) |
| The party record on a player character's card: "In the party since…" or "Left the party on…", and 🚪 Has left the party with its reason and ↩️ Undo (#4537 slice 2a), DM only | the character's card | built, switched off: the Names row (`PARTY_RECORD_LIVE`, #4798) (the website half, with the join question of slice 2b) |
| "Catch up" on a name's row: who is behind the rest of the party on an entry, and 📖 Catch up with ↩️ Undo (#4793) | the Catch up button on a card (Discord, #4793) | built, switched off: the Names row (`CATCH_UP_LIVE`, #4824) |
| "Did they mean…?": the word as heard, who said it, and a name or Keep it as heard, or ⏳ Later (#4841 B1); "Type it…" (a name typed for the question, #4865) is built, switched off on the Questions page | the "Did they mean…?" question (Discord, #3435) | built, switched off: the Questions page (`HEARD_LIVE`, #4857) |
| "See changes" under a followed house-rules set: what a newer version would change, and that DMbot takes it at the next session start (#4841 C1); "Pick changes" and its Undo for a day (#4841 C2, #4855); the Undo of an update DMbot already took (#4841 C3, #4867); packs from other tables on the Shared rules page: a newer version or a taken update (#4841 packs P1, #4872); "Pick changes" on a pack and its Undo for a day (#4841 packs P3, #4873) | the house-rules update notice (Discord, #4841 C1) | built, switched off: the House rules page (`RULE_UPDATES_LIVE`, #4845, #4855) |
| A character joining the party: "Since when has Mira been in the party?" (From now, From the first session, Or pick a session…) and, after the session, "What does Mira know about what came before?" (Only what everyone knows, What the party knows, each with ↩️ Undo) and 🧭 New in the party in ❓ Questions for you (#4537 slice 2b), behind `DMBOT_PARTY_JOIN` (off), DM only | private messages, ❓ Questions for you | built, switched off: the Questions page answers it (`PARTY_JOIN_LIVE`, #4778 part b) |
| Who knows it, in one line by the party now: 🌍 Everyone knows, ✅ The whole party knows, 👥/👤 Known to: …, 🤫 No one in the party knows (then who else does); Who knows this? lists the party first, then characters who left and NPCs (#4537 slice 3a), DM only | connection card, other names, backstory card, claims card | missing: #4778 (with the doors' party list) |
| A character coming back to the party: 🤝 Back in the party on the card of a character who left (with ↩️ Undo), then, after the session, "What does Okthandom know about what happened while away?" (Only what was known before, What the party knows, each with ↩️ Undo) (#4537 slice 4a), the question behind `DMBOT_PARTY_JOIN` (off), DM only | the character's card, private messages, ❓ Questions for you | built, switched off: 🤝 Back in the party on the Names row (`PARTY_RECORD_LIVE`, #4801; #4791) |
| Catching one party member up from a card: "Mira is missing 6 things on this card that the rest of the party knows." and 📖 Catch up: Mira (or 📖 Catch up… with a choice), one change with ↩️ Undo, nothing sent to the player (#4537 slice 4b, "Tell Mira" in the design), DM only | name cards (people, places, quests) | built, switched off: the Names row (`CATCH_UP_LIVE`, #4840) |
| The join question's "Let me choose": a private page with "Catch up by session…" and "Catch up by story thread…", each pick one change with ↩️ Undo, the first answering the question (#4537 slice 4c), behind `DMBOT_PARTY_JOIN` (off), DM only | private messages | built, switched off: the join question and the coming-back question on the Questions page, each pick with its own Undo and the first pick's Undo in the question's own words (`PARTY_JOIN_LIVE`, #4799, #4891); "Type it…" is not here |
| A player character in the party marked dead: after the session, "Okthandom is marked dead. Mark Okthandom as no longer in the party?" (Left the party, with ↩️ Undo; Still in the party; Later) and 🧭 Still in the party? in ❓ Questions for you; nothing closes a stretch by itself (#4537 slice 6a; a goodbye heard at the table is slice 6b), DM only | private messages, ❓ Questions for you | built, switched off: "Still in the party?" on the Questions page (`PARTY_LEAVES_LIVE`, #4895, page half); the goodbye at the table is slice 6b |
| A player character heard saying goodbye to the party in a session (the DM's lines, or that character's own player's, with a quote checked against the line): after the session, the same "Still in the party?" question with the goodbye's words, the same answers and Undo; nothing leaves the party by itself; behind `DMBOT_PARTY_LEAVES` (#4537 slice 6b), DM only | private messages, ❓ Questions for you | built, switched off: the same Questions page card as a death (`PARTY_LEAVES_LIVE`, #4895, page half) |
| The campaign's setting (the world the story is in): see it, keep DMbot's guess (#4291) | ⚙️ Manage DMbot: the Campaign setting line, ✅ Keep it, Pick another world (a list, or Somewhere of my own) | WebDev's half of #4291 (the website's Campaign setting field) |
| Hand over the campaign: offer, accept, decline, withdraw | Handover buttons | Hand a campaign over (`HANDOVER_LIVE`) |
| A hand-over offer closed because the Discord accounts on an account changed, and why (#2533 slice 3, #4467) | A notice to both people, and the offer message loses its buttons | Account page: "Offers that were closed", for a week, with the reason (no names, no accounts) |
| Delete this campaign | Delete this campaign | Backup and delete (delete is live) |
| Campaign invite link; D&D Beyond invite | Campaign management | Campaign settings: the invite panel (`InvitePanel`, `INVITE_LIVE`, switched off) (WebDev confirmed) |
| Give your adventure (Shared story); read it; match it to files; refresh; remove | Shared story, adventure card | Lore upload and reading, with the right-to-use box (`LORE_LIVE`); seeing the files, how far each read is and what was read: the Upload lore page, `ADVENTURE_LIVE` (#3694 slices A and B, switched off); giving a file (#3694 slice D), reading it after the cost is shown (slice E) and removing one with one confirmation (slice C): the same page; the match-names list (#3694 slice G1): the same page; picking one name's link from its card: not built yet (slice G2); Guide me (where the table is, I'm in another chapter, back to DMbot's guess): the same page (#3694 slice F) ⚠ |
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
| Check everything after a session or an import (one walk through names, who is who, what was said, the connections and kinds of connection, then deeds, reveals and the script as they join, with progress, Stop for now and Carry on; #4192) | ✅ Check everything on the private after-session message (the old buttons stay when only one kind waits) | The Questions page lists the kinds in the same fixed order, and a ✅ Check everything (N) button per campaign walks through the parts that open on the page, with "Names, part 1 of 3 · 11 waiting", Next, Stop for now and Carry on (N left) (#4193). A part moves on by itself when its list empties, and the count drops as answers go (#4740) |
| Continuity heads-ups waiting for the DM: the "Heads-ups" kind on the Questions list, a count only, while the story extractor is on and the plan has story memory (#5147, #5148) | ❓ Questions for you; the heads-ups message's own button opens the list | the same kind on the Questions page (`QUESTIONS_LIVE`), marked "answered in Discord" until the heads-ups panel on Names (#5136, switched off) is live and the kind links to it |
| Check new names (one by one; Add all N; Undo) | 📝 Check new names, read summary (after a lore upload the summary with Add all is a counts-only message in the DM Bot channel, and the private question's buttons open it: Discord-only on purpose, which Discord message opens, #3873) | The names waiting for a check; Add all (`QUESTIONS_LIVE`, #3207); Confirm kinds (`CONFIRM_KINDS_LIVE`) |
| A new name heard several ways as one question: "Heard N times, sometimes as …", the line that made it a name first, the surest first; ✅ Yes, spelled X / ✏️ Spelled another way… / 🚫 Not a name; other spellings become "change to" rules on a yes (#5133 slice 3, behind `DMBOT_SESSION_NAMES`) | 📝 Check new names | On the way: the website's names to check, #5144 |
| Uploads and imports add what DMbot is sure of (an import also adds the connections the DM's own lines state); Undo this upload (7 days); Ask me before adding | the notice in the DM Bot channel (an import's too, once its last session is scanned), ⚙️ Manage DMbot → Ask me before adding | The same notice and Undo on the upload page, a "From <upload>" filter on Names (WebDev, #3879); the setting on the campaign page (to do) |
| After-session counts in the DM Bot channel: quiet story threads, private scenes and continuity heads-ups (counts only, each with a DM-only button that opens its list privately; the DM screen when the campaign has no DM Bot channel; #3874 E1 part a, #4637) | the counts lines in the DM Bot channel | Discord-only on purpose: which Discord channel a notice goes to is Discord itself. The lists they open are on the website: Story threads (#4476), Private scenes (`PRIVATE_SCENES_LIVE`), continuity: the graded clashes are on the recap report, the continuity checks are on the way (#3695 half 1) |
| Who can see the campaign's channels (the people, or everyone) and the list added by hand | ⚙️ Manage DMbot → Who can see the channels, 👥 Who can see | The same setting and list on the campaign page (WebDev, #3894) |
| Who is this? (same as a known name) | name card, look-alikes | Who is this? (the clarify page, #3604) |
| Names heard but not named yet | heard review, unnamed | Names DMbot has met but not named yet |
| Which one? (Which Mara?) | connections review | Story Graph: waiting connections (#3463) |
| Check the connections; Keep, Keep as secret, No, Keep all; Undo | Check the connections | Story Graph: waiting connections (live) |
| A one-of-a-kind item two characters have: "Does A still have it?" (No, not any more; Yes, both have it; Not now; Undo) and the item's "How it changed hands" line | 🔁 card in 📜 Check the connections; the line on the item's name card (DM only, #4115) | #4122 (the Questions page and the Story Graph's history; the bot already applies the answers) |
| Said at the table (claims): true, secret, All true, All secret | claims review | Story Graph: waiting connections (All true / All secret, #3640, #3645); both pages say whether the bot showed a yes to the players or kept it hidden (#3974, #3986), or kept it to the group that was there while the party was split (#2996; the website's half is #4035) |
| Family ties | family ties card | Story Graph: waiting connections (family ties are one of its kinds) (WebDev confirmed) |
| Change a connection; its feeling; why (the lore quote, DM only) | connection edit | Story Graph: change a connection; feeling picker and "Why?" (#3607) |
| Whether two people have met (has met / has heard of / knows) | connection edit ("Have they met?") | Story Graph: on the way, #3882 (the web half of #3881) |
| Which way a connection reads (A → B, B → A, A ↔ B) and "Cerric and Wit are rivals" for a two-way one; Undo | connection edit ("Which way does it read?", DM only, #4154) | Story Graph: the connection panel's direction toggle and `pairWords`, #4155 (the API and door are in #4154) |
| Who knows this (with 🌍 Common knowledge: Discord #4563, website #4559); tell one character a secret name; take it back | who-knows, told name | Story Graph: who knows, secret names told (`WHO_KNOWS_LIVE`, `TOLD_LIVE`, #3542); 🌍 Common knowledge #4559 |
| A 🌍 Common knowledge (World) lore read reveals the hidden names and connections it states plainly, with the upload's ↩️ Undo (#4524 slice 1b) | the upload notice's count line (#4591) | the website upload notice's line (#4589) |
| 🤫 Known or secret?: what DMbot held back instead of hiding something the players know (Keep it known / Make it secret; Keep both / End the old one; Keep it secret / Let them know), with ↩ Undo (#4524) | the 🤫 card, ❓ Questions kind, count lines in the DM Bot channel (#4599) | the Questions page kind (#4598): the 🤫 card on the Questions page (page built, switched off, `SECRET_QUESTIONS_LIVE`; the 🌍 World read's question stays Discord-only, answered one upload at a time) |
| Reveal a secret; secret said aloud; revealed report; Recant | reveal cards | Story Graph: peek and reveal (`webPeek`, `webReveal`) (WebDev confirmed) |
| Places written two ways | name question | Names: places written two ways (`NAME_PLACES_LIVE`, #3533) |
| How big a place is (a plane down to a room; read from the lore's words, the DM sets it, Not sure clears it) | 📏 Size on a place's name card (DM only, #4173 slice 1) | Names: the API and door are built (`placeLevel` on each name, the `place-size` action, #4173); the page: Names, a place's row → "📏 Size" choice, built, switched off, `PLACE_FACTS_LIVE` (#4187) |
| A place's parent DMbot assumed from where the story was in a lore upload (never a question; "(assumed)" on the DM's card; ✅ That's right; players see nothing until then) | the place's name card: the line ends "(assumed)", ✅ That's right (DM only, #4173 slice 3) | Names and Story Graph (the DM's Full View connection panel): the line ends "(assumed)" and a ✅ That's right button clears it, built, switched off, `ASSUMED_PARENT_LIVE` (#4304, migration 0471). The waiting list and its Keep/Skip already leave assumed links out (0462) |
| Two places side by side, or a way between them ("Targos is on the shore of Bryn Shander", "Targos and Bryn Shander are connected by road"), made by the DM | the place's 📏 Size and kind screen: ↔️ Next to…, 🛣️ Travel to… (DM only, #4173 slice 6d) | Names: a place's row → ↔️ Next to… / 🛣️ Travel to… (DM only): search for a place, pick the words, Save; built, switched off, `PLACE_ROUTES_LIVE` (#4341). Without "On a road or river" until the add-link action takes a kind and a detail |
| How far a road or route is, in the DM's words ("two days by cart"); DMbot's read from the lore is the DM's to check and never shown to players until kept | 🛣️ Length and ✅ Length is right on a route connection's edit page and a route place's card (DM only, #4255, migration 0486) | core built (migration 0589: the door `dmbot_web_route_lengths`, `route-length`, `route-length-keep`, `route-undo`, #4392); the page is built, switched off: the Story Graph panels (`ROUTE_LENGTH_LIVE`, #4986) |
| The campaign's setting (the one place the whole story is in; a smaller place with no stated parent counts as inside it) | 🌍 Make it the setting / Not the setting on a big place's name card (DM only, #4173 slice 2) | Names: the door sends `setting` and takes `place-setting` (#4173); the page: "🌍 Make it the setting" on the same row, built, switched off, `PLACE_FACTS_LIVE` (#4187) |
| Land, water or a road (a place's kind; read from the name, the DM sets it, land shows no line) | The second menu on a place's name card, "Land, water or a road?" (DM only, #4173 slice 6a) | Names: a place's row → "Land, water or a road?", built, switched off, `PLACE_FACTS_LIVE` (#4187) |
| A place's land, climate and who rules it (read from the lore's set phrases as "DMbot read this", set by the DM, read up the place tree; players see only what the DM kept and showed) | 🗺️ Land, climate, ruler on a place's name card (DM only, #4256) | Names: a place's row → "🗺️ Land, climate, ruler" (DM only): built, switched off, `PLACE_WORLD_LIVE` (#4287, migration 0463). Players: a "🗺️ Land, climate, ruler" button on a place's row shows what the DM confirmed and showed, cut by the gate and the players' rule (#4311, migration 0466). Without "Same as {bigger place}" for now |
| A place's culture and who lives there, by lineage or a named culture ("strongly dwarven", "strongly Illuskan", "mostly halflings"; read from the lore's set phrases as DMbot's read, set by the DM, never read up the place tree; players see only what the DM kept and showed) | 🏛️ Culture, who lives here on a place's name card and its 🗺️ screen (DM only, #4257 slices 3a and 3b, migrations 0492 and 0496; a culture typed under "Something else…" is kept as one of the campaign's named cultures) | core built (migration 0590: `map-culture`, `map-population`, a named culture's name in both place doors, #4412); the 🗺️ panel is built, inside `PLACE_WORLD_LIVE` (#4990); the players' view of these rows is not built yet |
| A road's, pass's or bridge's condition: open, hard going, closed, gone or the DM's words, with what it was before ("Closed since session 5 (was open)"); read from the lore's set phrases as DMbot's read; players see only what the DM kept and showed | 🚧 Condition on a route place's name card and on a known road's connection page (DM only, #4258 slice 4a, migration 0500); World notes lists DMbot's reads as "Road condition" | built, switched off: the connection panel's condition menu, read note, Keep, Show and Undo (`ROAD_CONDITION_LIVE`, #4451); the Names route-place row is not built yet |
| DMbot's guesses about places (land, climate, ruler) and languages, for the DM to keep or turn down | 🌍 World notes, a line on ❓ Questions for you (DM only, #4285) | Names: "🌍 World notes" at the top (DM only): ✅ Keep, Keep all, 🚫 Not right, ↩ Undo; built, switched off, `WORLD_NOTES_LIVE` (#4285, migration 0481). A Roads group below Places and Languages (✅ Keep and Keep all, no 🚫, #4784): page built, switched off, `ROADS_LIVE`. Without ✏️ Change (Names already has each place's and character's own panel) |
| A place's, group's or people's feeling toward a lineage or a named culture that the lore states ("Mithral Keep distrusts elves"), as DMbot's read for the DM to keep, change or turn down | 🌍 World notes "Feelings" (✅ Keep, Keep all, ✏️ Change to one of the five steps, 🚫 Not right, ↩ Undo; DM only, #4229 slice 4, migration 0579) | built, switched off: the Feelings group in World notes (`WORLD_FEELINGS_LIVE`, #4932; core's door and the step pick are migration 0585) |
| The first meeting's reminder: when DMbot asks whether two characters know each other and they have never been connected, how one's home, group or people feels about the other's people, from feelings the DM kept | After-session "What was said" questions: one 🤔 line under the question, worked out when shown, never stored ("You decide how they feel"; DM only, #4229 slice 4b) | The Waiting for you page: the same line under the claim, from `startingFeeling` on each claim (core: migration 0592, PR #4999; page half #5000) |
| Story threads DMbot found in an upload (a quest someone asked for or a character's own pursuit, or a lead nobody has taken up), with who gave it, who it's aimed at and who takes it on; kept or turned down as a whole, never added by itself | 📜 Story threads in ❓ Questions for you (DM only, #4453, quests slice 1, migration 0502) | The read door, its keep, turn down and Undo, and what is in each suggestion (its steps, rewards, help, time limits and branches in the card's words, with a keep that names the lines left on, #4501, migration 0517) are built, and a suggestion read from a session is listed only while every speaker it quotes is recorded now, with the lines it was heard in (#4526, migration 0518); the page is next, switched off (#4457) |
| Progress on a story thread the campaign already keeps, heard in a session (a new step, something in the way, a reward given, help or a time limit's words; a step done, overcome or returned; the thread happening now, finished or dropped; a clue found, and by which player characters, #4645), kept or turned down by the DM, never added by itself | 🧵 Progress to check, the second stage of 📜 Story threads in ❓ Questions for you (DM only, #4522 quests slice 5b, migration 0520), behind `DMBOT_STORY_THREADS` (off) | Built, switched off: the 🧵 Progress to check card on the Names page beside 📜 Story threads (DM only, `THREADS_LIVE`, #4616) |
| "How far is A from B?" and "which way is A from B?" answered from the kept maps (a straight line with the map's scale, or one of eight directions; a player only from the table's maps) | Table questions and the DM sidebar (#4670 slice 4b), behind `DMBOT_MAP_MEASURE` (off) | Discord-only on purpose: asked live at the table, as table questions are. The map page's pins are #4672 |
| "How long will it take to get from A to B?": a travel-time suggestion on foot for the DM (the time at each pace that differs, capped by the land at either end, from a stated road length in miles or a kept map's straight line, citing SRD 5.2.1); a player's question gets "That's one for your DM." | The DM sidebar, and a DM's table question answered privately (#4260 slice 10a), behind `DMBOT_MAP_MEASURE` (off) | Discord-only on purpose: asked live at the table, as "how far" is |
| "How far" and "how long" along a chain of kept roads when no road joins two places directly: each leg with its stated length and condition, and a total only when the lengths add up (one unit, no range, one way of travelling); a player's only when the party knows every leg, otherwise the usual answer | The DM sidebar and table questions (#4260 slice 10d), behind `DMBOT_MAP_MEASURE` (off) | Discord-only on purpose: asked live at the table, as "how far" is |
| A road's length DMbot read that a kept map's straight line makes far longer (more than 20%), and the map's figure after the DM sets or keeps a road's length | 🌍 World notes "Roads" (✅ Keep makes the length the DM's; ✏️ Change opens the road) and a line on the road's page after 🛣️ Length (DM only, #4670 slice 4c-1), behind `DMBOT_MAP_MEASURE` (off) | Not yet: Discord only for now (#4677; the road page's line with #4392) |
| A private note to the DM during play when a distance or direction they say is far off a kept map's (once per pair of places a session), and the campaign's "Map checks" switch | The note in the DM's private chat with DMbot, with 🔕 Stop map checks; "🗺️ Map checks: on/off" on ⚙️ Manage DMbot (DM only, #4670 slice 4c-2, migration 0532), behind `DMBOT_MAP_MEASURE` (off) | The note is Discord-only on purpose: it happens live at the table. The switch: not yet (#4687) |
| A private estimate for the DM during play when they say a travel time between two places DMbot can measure ("Targos is two days from Bryn Shander"), once per pair of places a session; never a check of the DM's time | The DM's private chat with DMbot, with 🔕 Stop map checks (DM only, #4260), behind `DMBOT_MAP_MEASURE` (off) and the campaign's "Map checks" | Discord-only on purpose: it happens live at the table |
| A private note to the DMs after a session when weather the DM described (snow, frost or heat) plainly can't fit a place's kept climate; nothing stored, never during play | The DMs' private chat with DMbot, one message per session (#4260 slice 10c-2), behind `DMBOT_WEATHER_NOTES` (off) | Discord-only on purpose: a private note after the session, as the live map notes are; the place's climate is on the website already |
| What a kept map shows outright (a legend, border, colour key or terrain symbol: land, climate, ruler, culture, who lives there), as World notes rows to check, and a private note to each DM with ↩ Undo | The note in each DM's private chat with DMbot (at once, or after the session when one is running), with ↩ Undo; the rows in 🌍 World notes (#4259 slice 8a), behind `DMBOT_MAP_LAYERS` (off) | The rows are on the website's World notes already (Names page). The note is Discord's private message, as for a lore upload's; the Undo on the website: not yet (#4698) |
| The table's guesses at a mystery: the theories the players voiced in a session about who or what is behind it, each placed by the DM, shown with who holds it and how sure they said (never right or wrong) | 💭 "Guess: Cerric is sure …" on the mystery's 🧵 card and its 📋 screen (DM only, #4655, quests slice 7b, migration 0529), behind `DMBOT_STORY_THREADS` (off); a guess is placed on the website's readings (Story Graph) | Not yet: the Story threads page's thread card (WebDev, #4665) |
| A story thread's steps (with smaller steps), what is in the way, rewards promised and given (with what is still owed), help given or lent, and time limits, each added, ticked, changed, shown to players or taken off by the DM; which bigger thread it is a branch of | 📋 Steps, rewards and more and 🌿 Branch of… on the 🧵 thread's card (DM only, #4471, quests slice 2, migration 0504) | Not yet: Discord only for now (WebDev, the Story threads page, #4476) |
| Who may read a campaign's transcripts, and the maps shown to its table (#3891: the campaign's people, or everyone when the channels are open to everyone) | the campaign's DM Bot and transcript channels (Who can see this campaign's channels) | Transcripts pages and the Maps page: the same rule, one shared database test (`dmbot_web_reads_campaign`, migration 0489, #4401); a member outside the campaign's people is refused like a stranger and does not see the campaign listed |
| Make these words a kind of connection | kind offer | #3577 |
| A character's lineage and class (from lore) | name card (DM only, #2959) | built, switched off: the Story Graph node panel line (`NODE_TRAITS_LIVE`, #3925); core's door is `GET …/web/nodes/{id}/traits` (migration 0587) ⚠ |
| A map's pins, scale, grid and north | (nothing yet: DMbot reads them quietly; 4b's answers are behind `DMBOT_MAP_MEASURE`) | Maps page, "Pins and scale" under a map (DM only; page built, switched off, `MAP_PINS_LIVE`; #4672) |
| A lineage or class the lore gives a character you have (asked, never changed silently) | 🧬 Character details card (lineage, class and languages), private, from ❓ Questions for you (#3926) | Questions for you: the same card, Yes or No, and the website also asks language questions ("Does {who} speak {name}?", Yes, add it / No, leave it) (page built, switched off, `TRAIT_QUESTIONS_LIVE`; the bot's applier is CloudDev-lead's; #3946, #4632). Deliberate differences from Discord: the page has no Undo, so its done line says "Undo is in Discord."; a language's "left" line has no name-card sentence (the ux-critic's wording). |
| Connections DMbot inferred from lore (marked, kept only one by one) | connection card, Why (#2959) | built (#4967): Why says it is DMbot's guess from your lore with its reason; the waiting guess line and the Keep all count (#3928); the page reads `link.inferred` from #4966 |
| The party: player characters; Who plays them | party note, name card | Story Graph: PC icons (#3622); assigning a player to a player character: Names, change the kind to player character with their Discord ID (WebDev confirmed) |
| Deeds the party did | deeds card | on the way: answered on the Questions page (Record, Not this, Undo), behind `DEED_QUESTIONS_LIVE` (#3591, design approved 2026-11-23) |
| What the characters learned (Keep / Not this) | reveal card | built, switched off: the Questions page (`REVEALS_LIVE`, #3863 step 2); needs core's door and routes |
| Unnamed people (a scene's summary) | unnamed card | #3595, #3597, the scene (#3644) |
| Met but not named (who the party met in play that nobody named): name, Same as…, remove | Names home → 📋 More lists → 👣 Met but not named (DM only, #4045, #4103) | Names page → 👣 Met but not named (DM only): built, switched off, `MET_LIST_LIVE` (#4084; core 0438 and the bot's actions are merged or in review) |
| Met but not named (who the party met in play that nobody named): name, Same as…, remove | Names home → 📋 More lists → 👣 Met but not named (DM only, #4045, #4103) | #4084 (the website's page; the bot already applies its actions) |
| A DM's remembered players, with Forget this player, and a player's "DMs who remember you" with Clear | the website only on purpose: Discord has no list page to hold it (the data is kept by DMbot either way, #4226) | `/account/players`: built, switched off, `REMEMBERED_PLAYERS_LIVE` (#4226; core 0450) |
| A DM's remembered players listed first and marked "played with you before" when picking a player | the Names, People and import player pickers (#4724, built, switched off, `DMBOT_REMEMBERED_PLAYERS`) | the website's person pickers (#4252, `REMEMBERED_PLAYERS_LIVE`); both switches go on together |
| Private scenes (two player characters alone together while split): Not private | Names home → 📋 More lists → 🤫 Private scenes, or 🤫 Open the list on the after-session note (DM only, #2996 rule 5, #4103) | Names page → 🤫 Private scenes (DM only): built, switched off, `PRIVATE_SCENES_LIVE` (#4102; core 0439) |
| The recap's names to check; the DM's fix to a name in a recap | recap names card | The recap's name checks (`RECAP_NAME_CHECKS_LIVE`); Fix a name's words (`RECAP_FIX_LIVE`) |
| Recap ready, failed, unchecked, hold; Write now; share; feedback | recap cards | Recaps: approve, approve unchecked, share, edit |
| Recap audio: ask, remake | audio cards | Recaps: audio |
| Session notes; after-session report | notes and report cards | `SESSION_NOTES_LIVE` (#3027), `AFTER_SESSION_REPORT_LIVE` |
| Rule clashes (house rule vs rule); rules alerts | clash card | on the way (#3697): the core half is built (the decided pairs, change one, clear one, own rules only; a pair with a followed set's rule is read-only there), the page is WebDev's; "Which wins?" after a save waits for the website rule editor (#3775); the clash is found by one AI call when a rule is saved, nothing is stored but the DM's answer; live alerts are Discord-only |
| Plot check (ask, answer an issue, decide a proposal) | plot cards | Plot check (`PLOT_CHECK_LIVE`) |
| Continuity and contradictions | continuity card | The graded clashes between what people believe: on the recap report as "Things that may not fit" (door 0249). The continuity checks against confirmed facts: built, switched off: the heads-ups panel on Names (#5136; its read door is 0606, with consent checked when it is read). The checks' list is the heads-ups panel on Names (#5136, switched off), and its count is the "Heads-ups" kind on the Questions list (#5148): counted on the website's Questions page, answered in Discord until the panel links from it (Supervisor, 2026-11-23) |
| Threads (open plot threads) | threads card | built, switched off: the read-only Plot threads panel on the Names page (`PLOT_THREADS_LIVE`, #3696 slice 1); the writes are slice 2: the state change and "Checked, still going" (slice 2a) have their core half (`POST …/plot-threads/state`, migration 0580), the page half is next; 2b (who is involved, migration 0581) and 2c (a new thread or promise, a promise's due note and parties, migration 0582) have their core half; 2d (an event moved or ended a thread, and what led to what, migration 0583) too; their page halves follow ⚠ |
| Live heads-up (a secret about to be said) | live card | Discord-only: live at the table |
| Questions for you (notice, reminders, 🔕 Not now) | private notices | The Questions page; notices are Discord-only |
| A live session's after-session reads while they run: one card with the stage, part n of m, how long and when it last moved, then what could not be read (#5124, #5126; from the worker with #5142) | the card in the DM screen | Discord-only on purpose: the live session's own wrap-up at the table's end; an import's stages are on the website's import card (#5091, #5129) |
| Pictures: kept pictures, looked-at pictures, image fix | picture cards | Pictures gallery (`PICTURES_LIVE`) |
| Maps: the right-to-use question to a map's poster (#4344, 3b-1) | private question | Website: the same question in the Maps page's upload step (#4347); the Discord question follows a map posted in the table's chat, which only Discord has (live at the table) |
| Maps: sending a map yourself, `/dmbot map` or a picture in a private chat (#4372) | the command, and the private chat | Website: the Maps page's upload step (#4347) is the same thing. The command is level with it; **the private-chat path is Discord-only on purpose** (Supervisor, #4372): a person's quick way to hand DMbot a picture, with no page to open |
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
| Continuity and contradictions | continuity card | half on the website: the graded clashes are on the recap report ("Things that may not fit"); the continuity checks' count is on the Questions page ("Heads-ups", #5148) and their list is #5136 (switched off); the answers stay in Discord until #5136 is live (Supervisor, 2026-11-23) |
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
- A quest's time-limit messages ("⏰ … 1 day left", "time's up", #4448 slice 4b): private messages
  to the DMs when the game clock moves, so Discord's alone. The website shows the same countdown on
  the thread's card (#4503).
- Discord server roles and permissions, and inviting the bot.
- A player's own consent button in their private messages. The website has its own consent screens,
  and the consent rules stay as they are.

## Website-only for now

- "Share this campaign's story to improve DMbot" (#3741): the DM's switch, off by default, in the
  website's campaign settings (Supervisor's ruling on #3741). Discord shows it read-only in what is
  switched on (#3693).
| The clues of a story thread (add, false lead, take off, Undo) | the clues card in 🧵 Story threads (Discord, #4625) | Yes, on the quest sheet (#4625), behind `THREAD_CLUES_LIVE` (page built, switched off) |
| The table's guesses about a story thread (read only) | the guesses in 🧵 Story threads (Discord, #4665) | Yes, on the quest sheet (#4665), behind `THREAD_GUESSES_LIVE` (page built, switched off) |
| The card summary at the top of a character's card: who they are, "First met", "Last mentioned" (DMbot is still writing the sentence while it is pending) | the top of a character's card in the Story Graph and the name card (Discord, #5050) | Built, switched off on the Names and NPC pages (`CARD_SUMMARY_LIVE`; page #5051, core #5050) |
