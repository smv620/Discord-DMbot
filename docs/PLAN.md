# DMbot — Project Plan

DMbot is a Discord bot that helps a D&D **Dungeon Master** run sessions. It listens to the
table's voice channel, transcribes each speaker, and privately advises the DM on rules
(published and homebrew). It keeps track of characters, NPCs, game time, and story events
between sessions. It never makes rulings and never invents story: **the bot advises,
the DM decides.**

## Guiding principles

0. **Simple enough for a child (decided 2026-10-04).** Everything a user sees uses plain
   words and buttons. No technical terms: say "DMbot remembers your NPCs between
   sessions," never "knowledge graph." Commands are entry points; every choice after that
   is a button. The bot must make it obvious that it **does not make up the story and
   does not make decisions**. It only suggests, cites, and remembers.
1. **The DM is the authority.** The bot proposes; the DM confirms, overrides, or ignores.
   Overrides can become house rules, and house rules beat book rules.
2. **Consent first.** Nobody is recorded or transcribed unless they have opted in.
3. **One table channel per session.** The bot listens to exactly one voice channel at a
   time per server. Anything said in another channel is never heard. Players step out
   of the table channel for private asides.
7. **Every campaign is separate.** Each campaign has its own memory (house rules,
   characters, NPCs, game clock, story events, transcripts, DM screen). One Discord server
   can run several campaigns, and **no Discord server can ever see another server's data
   or settings.**
4. **Cite, don't assert.** Every alert names its source (SRD section, homebrew doc,
   or house rule number) and a confidence level.
5. **Quiet by default.** Verbosity levels and cooldowns keep the DM from being flooded.
6. **Least access.** Narrow OAuth scopes, encrypted tokens, and commands to review,
   test, and revoke every access the bot holds.

## Architecture

```
 Discord voice (DAVE E2EE)                         Discord text / slash commands
          │                                                    ▲
          ▼                                                    │
 ┌──────────────────────┐  local WebSocket   ┌─────────────────┴──────────────┐
 │ ears/ (Node + TS)    │ ─── PCM frames ──▶ │ core/ (Python)                 │
 │ @discordjs/voice     │ ◀── commands ───── │ discord.py bot + services      │
 │ per-speaker capture  │  (join, leave,     │  • transcription               │
 │ consent allowlist    │   allowlist)       │  • rules advisor (Phase 2)     │
 └──────────────────────┘                    │  • house rules, Drive (Phase 3)│
                                             │  • PlotBot / NPCBot (Phase 5–6)│
                                             └────────────────────────────────┘
```

- **ears** (Node/TypeScript): the only part that touches voice. Discord made DAVE
  end-to-end encryption mandatory on 2026-03-01, and `@discordjs/voice` +
  `@snazzah/davey` is the best-supported receive path. ears joins the table channel,
  subscribes only to consenting, non-bot users, decodes Opus to 16 kHz mono PCM, and
  streams frames to core. It holds no game logic.
- **Audio health** (the "audio NN%" in capture checks) compares Opus packets received
  with packets expected per utterance. Speaker pauses, which clients mark with five
  silence frames, are excluded. Known limit: packets lost right after a pause, such as
  DAVE decrypt failures on resumed speech, look like part of the pause and aren't
  counted (up to ~800 ms per pause). Exact detection is tracked in #43. RTP sequence
  numbers would be exact, but `@discordjs/voice` strips the RTP header before the
  receive stream, so reading them needs its internal UDP socket. Packet arrival plus
  silence frames was chosen instead, as the public-API option.
- **core** (Python): everything else — slash commands, consent records, transcription,
  AI analysis, storage, integrations. Developed in PyCharm.
- Both use the same Discord bot token. ears requests only the voice-state intent.

**Understanding pipeline (decided 2026-10-04).** After transcription, each utterance
passes through cheap steps first, so expensive AI is only spent on game content:

```
voice → transcription → ① name fixing → ② off-topic filter → ③ speaker tagging
                          (EntityBot)      (fast, cheap AI)     (who's talking)
                                                   │ game content only
                                                   ▼
                     ④ helpers: Rules advisor · House rules · TimeBot · NPC tracker · PlotBot
```
1. **Name fixing (EntityBot):** corrects misheard fantasy names using the campaign's name
   list (see below). Cheap sound-alike matching first, AI only for unclear cases.
2. **Off-topic filter:** a very light, fast AI pass labels each stretch as in-game, table
   talk, or non-game content. Only game content goes on to the helpers. Everything stays in
   the transcript, labeled.
3. **Speaker tagging:** labels who is speaking: the player's character, an NPC voiced by
   the DM, the DM narrating, table talk, or non-game content.
4. **Helpers** consume the cleaned, labeled stream.

**Hosting target: public bot, shard pods + workers (decided 2026-10-04, #57).** DMbot will
be a public bot others can install, on a scalable Kubernetes platform. *This replaces the
earlier "one pod per Discord server" idea:* Discord gives a bot one gateway connection per
shard (each shard covers up to ~2,500 servers), and isolation is stronger in the database
than in separate volumes.

| Piece | Job | Scales by |
|---|---|---|
| **Shard pods** (`core` + `ears`) | Commands, buttons, voice capture for the servers on their shards | `SHARD_COUNT` / `SHARD_IDS` settings (1 to start) |
| **Transcription & AI workers** (later) | Whisper or cloud transcription, name fixing, rules advice, TimeBot | Live game sessions, via a job queue |
| **Scheduler worker** (later, single) | Reminders, retention cleanup, timed jobs | One runner, so nothing is sent twice |
| **Postgres** | All durable data: campaigns, consent, active sessions, and later house rules, NPCs, clock | Managed database |
| **Redis** (later) | Short-lived coordination only: session leases, locks, queues, cooldowns | |
| **Object storage** (later) | Transcript and backup files | |

- **Isolation:** every table has the server ID, every query filters on it, and Postgres
  **row-level security** (forced, so even the table owner is subject to it) only returns
  rows for the server set in the current transaction (#68). DMbot refuses to connect as a
  superuser. Limit: today the app's database user also owns the tables, so row-level
  security guards against a forgotten filter, not against a compromised process. Before
  hosting for the public, split it into an owner user for migrations and an app user
  that can only read and write rows.
- **Consent changes reach every process:** each process caches consent for the instant
  audio check. Once more than one process can change a server's consent (for example a
  DM button handled by a different shard), a change must notify the others (Postgres
  `LISTEN/NOTIFY`, or routing all consent writes through the shard that owns the
  server). A revoke must stop recording everywhere at once (#69).
- **Shard manager from day one:** `AutoShardedBot` in core, the same shards in ears, so
  scaling is a settings change (#69).
- **Pods are disposable:** active sessions live in Postgres; a restarted or moved pod
  resumes them, rejoins voice, and tells the DM screen. A few seconds of audio during the
  gap can't be saved (#70).
- **Structured JSON logs** with shard, server, and campaign IDs; never names or speech (#69).
- Hosted deployments will mostly use cloud transcription with the customer's own key
  (#50); self-hosting with local Whisper stays supported (docs/DEPLOY.md, Docker).
- A public bot needs a **privacy policy and terms of service**, and **Discord verification**
  once it's in 75+ servers.

## Phases

| Phase | Deliverable | Notes |
|---|---|---|
| 0 | Scaffolding, CI, ears ↔ core audio pipeline | ✅ Done. Live capture works (#40) |
| 1 | **Listener**: consent by DM buttons (#33–#35), live per-speaker transcript in the DM screen, stored session transcripts participants can download (#41), transcript format with speaker labels | No AI yet; useful on its own |
| 1.5 | **Campaigns and setup**: `/dmbot start · stop · help`, first-time guide, campaign picker, one DM screen per campaign, voice-channel picker, target/fallback rulesets, optional rules, campaign export/import, bring-your-own API keys | Foundation for everything after |
| 2 | **Understanding the table**: name fixing (EntityBot), off-topic filter, speaker tagging | Every helper depends on clean, labeled input |
| 3 | **Rules advisor + house rules**: alerts with ✅ Agree / 🙈 Ignore / ⚖️ Override, house rules by voice with DM approval, `/houserules` | Uses the rules hierarchy below |
| 4 | **TimeBot**: game clock, effect durations, rests, dawn/noon/dusk, split-party clocks | |
| 5 | **NPC tracker** (remembers NPCs, relationships, factions between sessions), then **PlotBot** (DM-confirmed story events) | Shares the EntityBot name list |
| 6 | **DM sidebar**: voice messages to DMbot, marked `[DM Sidebar Discussion]` | No install needed |
| 7 | **Google Drive** (house-rules doc mirror) and **character data** from D&D Beyond links | |
| later | Paid service billing (owner's key, per-server metering); D&D Beyond companion extension; optional DM hotkey helper | |

## Feature notes

**Delivery to the DM.** Discord has no pop-ups. Alerts go to the campaign's private DM
screen channel (only the DM can see it) and optionally to DMs.

**Commands (decided 2026-10-04).** Five entry points; everything else is buttons.

| Command | What it does |
|---|---|
| `/dmbot start` | Start listening: pick the campaign and the voice channel (both default to last time). Replaces `/table join` |
| `/dmbot stop` | Stop listening and close the session. Replaces `/table leave` |
| `/dmbot backup` · `/dmbot restore` | Download a copy of a campaign; bring one back from a copy (restore needs a file, which only a command can take) |
| `/dmbot help` | A short, friendly guide with buttons |
| `/houserules` | List, add, edit, and remove house rules for the current campaign |
| `/optionalrules` | Turn optional rules (e.g. Xanathar's, Tasha's) on or off for the current campaign |
| `/transcript` | Download a session transcript. If DMbot is still recording: "This transcript may be incomplete. Use `/dmbot stop` first for the full session." [Download anyway] [Cancel] |

`/consent give · revoke` stay as hidden fallbacks for people with DMs off.

**First-time setup and starting a session (decided 2026-10-04).**
- The first `/dmbot start` in a server runs a short guide: explains in three lines what
  DMbot does and doesn't do, sets up the campaign, sets up its DM screen, and pins a
  "How DMbot helps" card with buttons (Add a house rule · Download a transcript · Help).
- **Every start asks:**
  - **Which campaign?** [▶ *Name*, last played *date/time*] (default: last used) ·
    [Another campaign ▾] · [＋ New campaign]
  - **Which voice channel?** Default: the one used last time for this campaign.
- **New campaign:** asks for the name, the **target ruleset**, and the **fallback ruleset**
  (defaults: 2024 rules, then 2014 legacy), and which **optional rules** to include
  (default: included where they don't conflict with the target ruleset).
- **One DM screen per campaign:** a channel such as `#dmb-dm-screen-rime-of-the-frostmaiden`,
  which the bot creates (see "Channel structure"). Who else can see it depends on the campaign's **DM-screen visibility**
  (see "DM-screen visibility" below; default **opt-in peek**). If a suitable channel
  already exists, setup offers to use it, after checking the bot can post there and that
  its visibility matches the campaign's setting. The DM may be someone other than the
  server owner; `/dmbot` setup and a "change DM" option keep DM-screen access in sync (#30).

**Campaigns (decided 2026-10-04).** Each campaign is a separate memory: house rules,
optional-rule settings, rulesets, name list, NPCs and relationships, game clock and
effects, story events, sessions and transcripts, and its DM screen. It persists between
Discord sessions. A server can have several campaigns. **Export** (backup to a file) and
**import** (restore) are available per campaign to its DM.

*Implementation (#47):* `core/src/dmbot/campaigns/` holds the campaign store. Every method
takes the server ID and every query filters on it, so isolation can't be forgotten by a
caller. A feature with per-campaign data adds its own tables (with `campaign_id` and
`guild_id`) and registers an `ExportSection`, so its data is included in backups and
removed with the campaign. Schema changes go through `dmbot.db` migrations. The campaign
also stores its DM-screen visibility (`private` / `peek` / `open`, default `peek`), which
`dmbot.dm_screen` applies to the channel's permissions (#30).

**Rules sources.** Baseline is the SRD 5.2 (CC-BY-4.0, attribution required). Owned
sourcebook text is never bulk-copied to the server; only short, relevant excerpts are
sent per query.

**Rules edition (decided 2026-10-03).** The newest official ruleset is always the
default — currently the 2024 Player's Handbook / 2025 Monster Manual — including when
running a legacy adventure such as *Rime of the Frostmaiden*. This covers spells, rules,
**and monster stat blocks**.
1. Look up the newest version of a spell, rule, or monster first.
2. Only if no newer version exists, fall back to the legacy version (for a monster in a
   legacy adventure, the adventure's printed stat block).
3. Any legacy content used is tagged **`[Legacy 2014]`** wherever it appears (alerts,
   citations, house-rule records, transcript rule notes).

Precedence, highest first: **house rules → homebrew → target ruleset → fallback ruleset**.
The DM only picks the target and fallback rulesets (defaults: 2024, then 2014 legacy);
the order itself never changes. Content from the fallback ruleset is tagged
**`[Legacy 2014]`** (or the matching edition tag).

**Optional rules (decided 2026-10-04).** Where the target ruleset neither includes nor
contradicts an optional rule from a supplement (e.g. Xanathar's Guide, Tasha's Cauldron),
the fallback applies. Example: going 24 hours without a long rest risks exhaustion — an
optional Xanathar's rule that the 2024 books don't include or change. New campaigns ask
which optional rules to use, **default: on**. `/optionalrules` toggles them any time. House
rules can still override them.

Lookups must match renamed content (e.g. 2024 dropped many creator names from spell
titles), so the rules index keys each entry by a normalized name plus known aliases,
and a legacy entry is used only when no newer entry matches any alias. If a future
edition supersedes 2024, it becomes "newest" and 2024 content gets its own legacy tag.

**House rules (decided 2026-10-04).** Each campaign has its own. The database is the
source of truth; the Google Doc (Phase 7) is a readable mirror. Each rule records the
rule, the book rule it supersedes, and the scenario that created it (session, date, what
happened). Ways in:
1. **From an alert:** ⚖️ Override → "Save as a house rule?"
2. **By voice:** DMbot listens for the DM declaring a house rule, and compares it against
   the existing hierarchy. **Every voice-proposed rule needs the DM's approval** in the DM
   screen: new rules show [Save] [Edit] [Cancel]; conflicts with an existing house rule
   show both and ask which wins.
3. **By typing** in the DM screen ("House rule: …"), with the same approval.
4. **`/houserules`:** list with Edit and Remove.

Only the DM can declare or change a house rule. A player may suggest one; it becomes a
proposal when the DM clearly agrees out loud, then goes through the same approval.

**Transcription (decided 2026-10-03).** Per-speaker audio means no diarization is
needed. Name hints (players now; characters, NPCs, places later) are fed to the
transcriber. Default engine is **local Whisper** (faster-whisper). Every engine sits behind
one `Transcriber` interface and is chosen by `TRANSCRIBER=` in config, so switching to a
**cloud pay-as-you-go** API (any OpenAI-compatible endpoint) is a settings change, not a
code change — for DMs without a GPU.

**Hosting, self-hosted (decided 2026-10-03).** A cloud server runs ears, core, and Postgres
(decided 2026-10-04: Postgres everywhere, so self-hosting and hosting debug the same database). Because local
Whisper runs on that server, its size decides transcription quality and speed: a CPU-only
server suits the `base`/`small` models; larger models need a GPU server, or switch to
`TRANSCRIBER=cloud`. Both parts ship as Docker containers started with one
`docker compose` command; see docs/DEPLOY.md.

**Consent (decided 2026-10-04).** Consent is asked by **private message with buttons**,
the way other Discord bots handle opt-ins. No typing, and no slash command needed.
- When `/dmbot start` starts a session, DMbot DMs everyone in the table voice channel
  (the DM included), and anyone who joins later. The message says DMbot is for
  entertainment only, other uses are prohibited, their voice will be recorded and
  transcribed, and consenting participants can view and download transcripts. It has a
  **✅ I consent** button (#33).
- **Consent carries over** between sessions, per server. **Every time** a consented
  person joins a channel where DMbot is listening, they get a private reminder with the
  date and time they consented and how to revoke, plus a **🛑 Stop recording me**
  button (#33, #34).
- Revoking takes effect immediately. Queued and in-flight audio and text for that person
  are discarded.
- People with DMs off are nudged in the voice channel's chat. The DM sees who couldn't be
  reached. Nobody is recorded without consent.
- `/consent give` and `/consent revoke` remain as fallbacks.
- Consent records store the terms version, the UTC timestamp, and the method. Changing the
  consent wording re-prompts everyone (#35).
- The public "DMbot is listening" notice in the voice channel's chat still posts once per
  `/dmbot start`, is not repeated after a voice-service reconnect, and the DM is warned if
  it can't be posted.

**Transcripts vs. the DM screen (decided 2026-10-04).**
| Content | Who sees it |
|---|---|
| **Transcripts** (what was said at the table) | The DM **and every consenting participant** can view and download them (#41) |
| **DM screen** (rules alerts, house-rule prompts, NPC and plot notes) | The DM, plus players only as the campaign's **DM-screen visibility** allows (below). The bot never *sends* DM-screen content to players. |

**DM-screen visibility (decided 2026-10-04).** Each campaign's DM picks one; if none is
picked, **opt-in peek** applies. Discord has no warning screen in front of a normal
channel, so a warning can only come *before* access is granted, which is why the default
is opt-in rather than open.
| Setting | Players | How |
|---|---|---|
| **Private** | Can't see it | Only the campaign's DM(s) and the bot have access |
| **Opt-in peek** (default) | Hidden until they choose | A **"Peek behind the DM screen"** button on the players' "DMbot is listening" notice in the voice channel's chat warns that they'll see the DM's notes (old ones too), that the DM will see they peeked, and that they can hide it again. [Yes, show me] [Cancel]. Yes gives **read-only** access (no posting, threads, reactions or commands); the DM screen notes who peeked. Pressing Peek again, or Hide on the screen's help card, hides it. Helpful for new DMs and players learning or testing the bot. |
| **Open** | Can see it | **Everyone in the server**, read-only; the channel topic and the help card warn about spoilers |

The DM picks the setting when creating a campaign, and changes it any time with the
buttons on the DM screen's help card (🔒 Only the DM · 👀 Players can peek · 📖 Everyone
in the server). Changing to "peek" during a session posts a Peek button in the voice
channel's chat.

- **Server owners and admins always see every channel**, whatever the setting; Discord
  doesn't let a bot hide channels from them. The bot can't prevent an admin from opening
  the screen to players, but it checks and tells the DM when a player can see the DM
  screen under the **private** setting.
- Granting peek access needs the bot to manage that channel's permissions (Manage
  Channels and Manage Roles); per least privilege it only does this in the DM screen it
  created or was given.

**Channel structure (decided 2026-10-04, #85).** Every channel DMbot creates starts with
`dmb-`, so its channels group together in the sidebar and are clearly bot-managed.

*Names.* Discord turns every text channel name into lowercase with dashes for spaces,
so the docs always show names the way Discord does. For the campaign
**Rime of the Frostmaiden**:

| Channel | Name in Discord | Built in |
|---|---|---|
| DM screen | `dmb-dm-screen-rime-of-the-frostmaiden` | Phase 1.5 (now) |
| Rules archive (house rules, overrides, rulings) | `dmb-rules-rmfthfrstmdn` | Phase 3 |
| Game time (clock, effects, rests) | `dmb-time-rmfthfrstmdn` | Phase 4 |
| NPCs (roster, relationships, factions) | `dmb-npcs-rmfthfrstmdn` | Phase 5 |
| Plot (story beats, places, hooks) | `dmb-plot-rmfthfrstmdn` | Phase 5 |

- **The DM screen uses the full campaign name:** `rime-of-the-frostmaiden`.
- **Sub-channels use a short name** (`rmfthfrstmdn`), made like this:
  1. Lowercase the name, then drop spaces, dashes and punctuation.
     `Rime of the Frostmaiden` → `rimeofthefrostmaiden`.
  2. If **more than half of the letters are vowels** (a, e, i, o, u; **y is not a
     vowel**), keep the vowels and cut it to 15 characters.
  3. Otherwise remove the vowels and cut it to 15 characters.
     `rimeofthefrostmaiden` → `rmfthfrstmdn`.
- **Clashes get a number at the front, starting at 2.** If a new campaign's screen name
  or short name is already used by another campaign in the server, the new campaign gets
  the lowest free number from 2 up, on both names. *Example:* "Frozen Sick" and
  "Frozens Cake" both shorten to `frznsck`. "Frozens Cake" was made second, so its
  channels are `dmb-dm-screen-2frozens-cake` and `dmb-time-2frznsck` (and so on).
- Discord allows 100 characters per channel name; longer names are cut to fit.

*Who can see what.* Channels are either **controlled** or **unrestricted**:

| Kind | Channels | Players see it |
|---|---|---|
| **Controlled** | DM screen, rules, NPCs, plot (and, by default, any channel added later) | As the campaign's **DM-screen visibility** says: private, opt-in peek (**default**), or open. The setting applies to all controlled channels at once. |
| **Unrestricted** | Game time | Always, by everyone in the server |

- Players can **read but never post** in any DMbot channel (no threads, reactions or
  commands either), the same read-only access as a DM-screen peek.
- Changing the setting (the help-card buttons) updates every controlled channel together.
  Peeking opens all controlled channels for that player; hiding closes them all.
- Server owners and admins always see every channel (see above).

*Modes.*
- **Compact mode:** everything goes in the DM screen. This is the default while the DM
  screen is the only channel built (Phases 1.5–2).
- **Organized mode:** each campaign gets a category, `📋 Rime of the Frostmaiden`
  (categories keep capitals and emoji), holding its `dmb-` channels. Each channel has a
  pinned "What's this channel?" card saying what it's for and who can see it. This
  becomes the default once the first sub-channel ships. A DM can switch back to compact
  mode, which keeps players' notifications quiet too.
- Discord allows 500 channels per server and 50 per category, so with five channels and
  a category per campaign, a server can hold about 80 campaigns in organized mode.

*Why.* It mirrors a real table: the DM screen stays hidden, reference material sits on
the table. Actionable alerts stay separate from reference information. Players can follow
the clock and NPCs without seeing rulings, and each channel can be muted on its own.
Code changes: #87.

Sessions and their participants are stored, and each transcript can be downloaded as a
file by its participants and the DM: from a 📄 button in the consent DMs, at session end,
or with `/transcript`. Transcripts never contain DM-screen content.

**Transcript format (decided 2026-10-04).** One line per utterance:
`[timestamp] (Discord name) {entity}: text`, where `{entity}` is the in-game character,
an NPC or other in-game entity, `{narrating}` for the DM, `{table_talk}`, or
`{non-game_content}`. Off-topic talk stays in the transcript, labeled. DM voice messages
to the bot appear as `[DM Sidebar Discussion]`.
- **Players' lines:** players only speak in character, for a familiar or pet, as table
  talk, or off-topic, so the AI's best guess is used with no prompts.
- **DM's lines:** when DMbot isn't confident who the DM is voicing (narration vs which
  NPC), it asks in the DM screen, because a wrong label can throw off plot and NPC tracking.

**Name fixing (EntityBot) (decided 2026-10-04).** Speech engines miss fantasy names, so
each campaign keeps a **name list** (characters, NPCs, places, monsters, spells, items)
with nicknames ("Cerric the Brightshadow" = "Cerric"). It is the same list the NPC tracker
uses.
- The most relevant names are given to the speech engine as hints.
- After transcription, names that sound close and fit the context are fixed ("Sarah" →
  Cerric when Cerric is in the scene).
- **Unsure?** The DM screen asks "Did they mean…?" with **at most 3 options** plus
  **Type it**. Answers are remembered as nicknames for that campaign.
- Retraining the speech engine per campaign is a later option, not the first step.

**Off-topic filter (decided 2026-10-04).** A very light, fast AI pass right after
transcription. Scheduling, life updates, and other non-game talk are labeled
`{non-game_content}` and not analyzed further, which saves cost.

**TimeBot (decided 2026-10-04).** Tracks **game time** (not real time) quietly in the
background.
- Reads time cues from narration ("you walk for a few hours"), rests (short ≈ 1 hour,
  long ≈ 8 hours), and combat rounds.
- Tracks timed effects (e.g. Mage Armor 8 hours, Bless 1 minute) from the rules data and
  suggests in the DM screen when one has likely ended.
- **Speaks up only for:** conflicts ("earlier it was night; the clock says noon"), effects
  ending, transitions, **dawn, noon and dusk**, a periodic "where are we" note (e.g.
  "Afternoon of Day 4"), and **24 hours without a long rest** (optional exhaustion rule,
  on by default, adjustable by house rules).
- **Split party:** keeps a separate clock for each group, and warns when one group gets too
  far ahead of the other.
- The DM can correct the clock with buttons ([+1 hour] [It's dawn] [Set time…]).

**DM sidebar (decided 2026-10-04).** A Discord bot can't watch for key presses, and
muting in Discord stops audio to everyone including the bot. So the DM sends a **voice
message in their DM conversation with DMbot** (hold the mic button, speak, release).
DMbot transcribes it, answers in the DM screen, and logs it as `[DM Sidebar Discussion]`.
The table never hears it. An optional hotkey helper app for the DM's PC may come later.

**AI and speech API keys (decided 2026-10-04).** Start with **bring your own key**: each
server's DM or admin enters their own Anthropic API key (and a cloud speech-to-text key if
used) through a private pop-up form, never typed in a channel. Keys are stored encrypted
and per server; usage and spending limits live in their own provider account. A paid
service (the owner's key, metered and billed per server) may follow later; the code
keeps a single "who pays for this call" seam so that switch stays small.

**Retention.** Configurable auto-delete of audio and transcripts per server, and a
"Delete my past transcripts" action for each player.

**Bots are never transcribed** (music bots etc.) — enforced in ears by an allowlist.

**License (decided 2026-10-03).** Public repository under PolyForm Strict 1.0.0.

## Open decisions

- Final wording of the consent DM and join reminder (#33)
- Whether revoking consent also removes a person's past lines from stored transcripts (#34)
- Whether consenting members who missed a session can download its transcript (#41)
- Hosting provider and Kubernetes setup (Helm) for the public bot; GPU vs cloud transcription workers
- Privacy policy and terms of service text for the public bot
