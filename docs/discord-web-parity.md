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
| Campaign settings; optional rules; game masters (co-DMs); character sheets; pause; improvement sharing | campaign home | built, switched off | `OPTIONAL_RULES_LIVE`, `CO_DMS_LIVE`, `SHEETS_LIVE`, `PAUSE_LIVE` (improvement: none) |
| Hand a campaign over (offer, accept, decline, take back, by email too) | `/handover`, campaign home | built, switched off | `HANDOVER_LIVE` |
| Backup download; load a backup; delete a campaign | campaign home, `/account/restore` | backup and delete live; load a backup switched off | `WEB_RESTORE_LIVE` |
| Recaps: list, read, edit, make again, approve, approve unchecked, audio (play, make again) | `/account/recaps` | live | `RECAPS_LIVE`, `RECAP_AUDIO_LIVE` |
| Recap must-haves and defaults | recap page | live | `RECAP_MUSTS_LIVE` |
| Recap style (how recaps are written) | `/account/recap-style` | built, switched off | `RECAP_STYLE_LIVE` |
| Share a recap; remake a recap | recap page | built, switched off | `RECAP_SHARE_LIVE`, `RECAP_REMAKE_LIVE` |
| Fix a name's words in a recap; the narrator's way to say a name (type it, suggest it, say it) | recap page | built, switched off | `RECAP_NAMES_LIVE`, `RECAP_FIX_LIVE`, `RECAP_VOICE_LIVE` |
| After-session report, session notes, who was in each scene | recap page, campaign | built, switched off | `AFTER_SESSION_REPORT_LIVE`, `SESSION_NOTES_LIVE`, `PRESENT_PER_SCENE_LIVE` |
| Names: list, search, edit, merge, split, notes on an NPC (NPC page) | `/account/names`, `/account/npc` | live (split off) | `NAME_SPLIT_LIVE` |
| Search names by meaning | `/account/names` | built, switched off | `NAMES_SMART_LIVE` |
| Names fixed this session, with Undo | recap page | built, switched off | `NAMES_FIXED_LIVE` |
| Names DMbot has met but not named yet (name them, remove, same as someone) | `/account/names` | live | `NOT_NAMED_LIVE` |
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

(CloudDev-lead fills this in from `ui/`, `bot.py` and the slash commands: one row per function,
each with where it is in Discord and which Section 1 row answers it, or an issue, or "missing", or
"Discord-only: why".)

## Section 3: the table

(Written by whoever finishes second; Supervisor then files one issue per "missing" row.)

## Discord-only on purpose (starting list, from #3663)

- Which voice channel the bot listens to, the table's text channel and the DM-screen channel.
- Joining, leaving and recording in voice, and anything live at the table: "Hey DMbot" answers, live
  rules alerts, the initiative tracker during play.
- Discord server roles and permissions, and inviting the bot.
- A player's own consent button in their private messages. The website has its own consent screens,
  and the consent rules stay as they are.
