# Go-live map for the site's `*_LIVE` switches

Why this file exists: the website hides every feature that is not live yet, so Help, What's new, the
home cards and the privacy page never claim something that isn't working. Each hidden feature has a
switch (`*_LIVE = false`). This file says which switch turns on with which deploy, and which ones also
need a server setting, so the site and Discord go live the same day (#1852).

Owner of this file: WebDev. Source of the "live today?" column: `docs/testing-status.log` ("LIVE NOW")
and `docs/testing-history.log`, read the way #1820 did (a feature is live only when it is deployed and
a live check is recorded). Update a row in the same PR that flips its switch.

**Last checked:** 2026-10-18, against `development` at 86976110. dev1 deployed `86976110` at 01:40 UTC
(migrations 0128–0130 and every PR merged after `174c35ec`); its record in `docs/testing-history.log` was
not on `development` yet when this was written. The deploy before it was `174c35ec` (2026-10-17 17:00 UTC,
last migration 0127).

## How to read "goes live with"

- **next deploy**: the bot code is merged but not on the server yet. Flip the switch in the PR that
  waits for dev1's line in `docs/testing-history.log`.
- **owner check**: already deployed. The held What's new entry says the owner (or dev1) has not seen it
  working at a table or on the test server, so the switch stays off until that check is recorded.
- **server setting**: a `DMBOT_…` value in `.env`, set by the owner through `scripts/set-key` or
  `scripts/set-variables`. dev1 cannot edit `.env`. After #1833 merges, `core/src/dmbot/features.py`
  lists what is on; until then the names below come from `core/src/dmbot/config.py`.
- **owner step**: something only the owner can do (an account, a key, a legal go-ahead).

The owner's checks for the rows marked **owner check** are in `docs/OWNER_CHECKS.md`, one sitting, in plain words.

## The table

| Switch | Shows | Bot PRs, merged? | Server setting | Live today? | Goes live with |
|---|---|---|---|---|---|
| `NPCS_CHANNEL_LIVE` (help.ts) | Help steps for the people-and-groups channel; home card; privacy line "people list"; What's new "A channel listing the people and groups" | #1761, #1779, #1793 merged after `174c35ec`; migration 0128 | none | Deployed 2026-10-18 01:40 UTC (`86976110`); the live check is not recorded yet | **the draft flip (#1864)**, once dev1 records the check in `docs/testing-history.log` |
| `INITIATIVE_STYLE_LIVE` (help.ts) | Help steps (player and game master); home card sentence; privacy line; `/players` "What you might see"; What's new "FightBot follows your turn order" | #1771/#1783, #1784/#1790, #1791/#1795 merged after `174c35ec`; migrations 0129, 0130 | none | Deployed 2026-10-18 01:40 UTC (`86976110`); the live check is not recorded yet | **the draft flip (#1864)**, once dev1 records the check in `docs/testing-history.log` |
| `TIME_SUGGESTIONS_LIVE` (help.ts) | Help steps; What's new "A time suggestion from your narration" | #1758, #1760 deployed in `174c35ec` | **`DMBOT_TIME_SUGGESTIONS=1`** (default off) | No: deployed but the setting is off | **server setting**, set by the owner, then dev1's live check |
| `RULES_CHANNEL_LIVE` (help.ts) | Help steps; the sentence that a peek opens this channel too; What's new "A channel for your house rules" | #1752, #1759 deployed (migration 0127, `3b75a693`) | none | Deployed, not seen: the entry says the owner has not checked it | **owner check** |
| `WHERE_ARE_WE_LIVE` (help.ts) | Help line about the quiet "where we are" note; What's new "A quiet reminder of the game time" | #1738 deployed | none found | Deployed, not seen | **owner check** |
| `DEEDS_LIVE` (help.ts) | Help steps for Record a deed; What's new "Record what your players did" | #1732, #1754 deployed | none found | Deployed, not seen | **owner check** |
| `STANDING_LIVE` (help.ts) | Help steps for Standing; What's new "How NPCs feel about the party" | #1655, #1661 deployed (0117, `68a8949f`) | none | Deployed, not seen (listed under NEW LIVE CHECKS) | **owner check** |
| `HOMEBREW_KINDS_LIVE` (help.ts) | Help steps for the kinds menu and the new kinds; What's new "Homebrew menu by kind" and "Species, subclasses and backgrounds in homebrew" | #1663, #1665, #1672 deployed (0119, `68a8949f`, `e25d74ea`) | none | Deployed, not seen | **owner check** |
| `STORY_THREADS_LIVE` (help.ts) | Help steps for story threads, quiet threads, promises and Led to; What's new "Story threads you keep" and "Keep track of promises made at the table" | #1662, #1674, #1675, #1679 deployed (0118, `68a8949f`, `e25d74ea`) | none (`DMBOT_THREAD_STALLED_SESSIONS` only tunes how many sessions count as quiet) | Deployed, not seen | **owner check** |
| `DM_BOT_CHANNEL_LIVE` (help.ts) | Every Help line that sent people to Settings "on the DM screen" now says the DMbot channel; What's new "A DMbot channel for buttons" | #1666, #1676, #1693 deployed (0120, `e25d74ea`) | none | Deployed, not seen (the owner's check is listed: the channel appears, each role sees its own buttons) | **owner check** |
| `SHARED_STORY_LIVE` (help.ts) | Help steps for the Shared story switch; What's new "A Shared story switch" and the entries behind it | #1692, #1698 deployed; Watch the story (#1714, #1729) and its buttons (#1809, #1815) deployed: the story watch now reads the adventure, which is what "useful" meant (Supervisor, #1852) | the per-campaign switch is the DM's own (0121 `story_mode`, default off); no `.env` value | Deployed, not seen at a table | **owner check**: give DMbot an adventure on the test server, turn Settings > Shared story > Watch the story on, play a little, and see one heads-up on the DM screen |
| `SECRET_CLUES_LIVE` (help.ts) | Help step for "only some people know" clues; What's new "Secrets only some people know" | #1649, #1651 deployed (0115) | **`DMBOT_STORY_EXTRACTOR=1`** (default off), plus its tier and call limits | No: needs the extractor, which stays off until its prompt passes the measurement (#1282) | **server setting** and the measurement, then an owner check |
| `RECAPS_LIVE` (recap-words.ts) | Recap words in Help, My Account's "Read your session recaps", the recap list lead and email promise, privacy lines, pricing, home card; What's new "Recaps for your table" | #1382 family deployed (0079, 0084), built and OFF | **`DMBOT_RECAPS=1`** (default off), `DMBOT_RECAPS_PER_MONTH` | No: off | **server setting**, then dev1 checks a real recap (#1382) |
| `RECAP_AUDIO_LIVE` (recap-words.ts) | Narrated recaps in pricing, My Account, privacy; What's new "Narrated recaps with music" | #1383 deployed (0080–0082), `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` are set on the server | `DMBOT_RECAPS=1` first; `DMBOT_RECAP_AUDIO_MAX_CREDITS`, `DMBOT_RECAP_AUDIO_PER_MONTH` | No | **owner step** (#1385): the go-ahead to name ElevenLabs, ElevenLabs in "Companies that help us" with its country, the add-on's price, a What's new entry. Can only be true with `RECAPS_LIVE` |
| `QABOT_LIVE` (qabot.ts) | Public Q&A section, two privacy lines, What's new "Ask a question in public" | the workflow and key are the owner's steps on #1341; GitHub refused the workflow PR (the server's login has no workflow scope, #516) | the key is a GitHub Actions secret, not `.env` | No | **owner step**, then QAbot answers a real post |
| `SUPPORT_AI_LIVE` (support-words.ts) | Privacy page and Support form describe the AI helper; What's new "DMbot's helper answers questions" | #1431 deployed (0086 `5c59f459`) | no `DMBOT_…` switch: the helper runs whenever the web API has the AI key (`ANTHROPIC_API_KEY`), see `core/src/dmbot/web/__main__.py` (`if settings.ai_key:`). dev1 confirms the key is set on the web API | Deployed; no real question recorded | **owner check**: send one test question from the website's Support form, see the helper's answer, and dev1 records it |
| `WHATS_ON_LIVE` (help.ts) | Help step for the ℹ️ What's switched on button, and its snag line; What's new "See what's switched on" | #1831/#1833 (the button) and #1866 (the Help step) merged; not in deploy 86976110 | none | Not deployed yet: merged after 86976110 | **next deploy**, then **owner check 19** in `docs/OWNER_CHECKS.md` (the button needs a deploy before anyone can press it) |
| `SIDEBAR_SOURCES_LIVE` (help.ts) | Help step "Where did that come from?"; What's new "See what DMbot looked at" | #1957 (not merged yet) | none | No: not built yet | **the next deploy after #1957 merges**, then dev1's live check |

`HOMEBREW_LINKS_LIVE` is already `true` (went live 2026-10-17, #1664) and is not in the table.

## What the 86976110 deploy flips

Two switches, the ones that deploy brought: `NPCS_CHANNEL_LIVE` and `INITIATIVE_STYLE_LIVE`.
Neither needs a server setting. The draft PR that flips them also moves their two held What's new
entries into `entries` and changes no words. It waits for dev1's line in `docs/testing-history.log`
that records the deploy and the live check.

Everything marked **owner check** can flip as soon as the owner's check is recorded in
`docs/testing-history.log`; nothing about the deploy blocks it. Everything marked **server setting** or
**owner step** needs the owner first.

## When dev1 deploys

Before the site switches flip, dev1 reads the "Server setting" column for the switches in the deploy:
a feature whose setting is off on the server must keep its switch off, or the site will promise
something Discord does not do. The next deploy's two switches need no setting. The one deployed
feature that does is `TIME_SUGGESTIONS_LIVE`, which needs `DMBOT_TIME_SUGGESTIONS=1`.
