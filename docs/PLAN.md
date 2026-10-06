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
 │ consent allowlist    │   allowlist)       │  • campaign memory (Phase 2)   │
 └──────────────────────┘                    │  • rules, house rules (Phase 3)│
                                             │  • NPCs, PlotBot (Phase 5)     │
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

**Understanding pipeline (decided 2026-10-04; updated 2026-10-05).** After transcription,
each utterance passes through cheap steps first, so expensive AI is only spent on game
content:

```
voice → speech-to-text → ① Transcript Cleaner → ② off-topic filter → ③ speaker tagging
                               ▲    │ fixes made, unknown names       (who's talking)
          names, aliases,      │    ▼                                        │
          "don't change" rules └── EntityBot ──▶ campaign memory             ▼
                                       ▲         (Postgres)   ④ helpers: Rules advisor ·
                          DM answers, Undo, confirmed facts      House rules · TimeBot ·
                                                                 NPC tracker · PlotBot
```
1. **Transcript Cleaner (#127):** fixes misheard names in each line as it arrives, using
   the campaign memory. It fixes names only, and it **reads** the campaign memory and
   never changes it. See "Transcript Cleaner" below.
2. **Off-topic filter (#52):** a very light, fast AI pass labels each stretch as in-game,
   table talk, or non-game content. Only game content goes on to the helpers. Lines it
   labels non-game with high confidence show as the off-topic marker in the cleaned
   transcript (see "Transcript format").
3. **Speaker tagging:** labels who is speaking: the player's character, an NPC voiced by
   the DM, the DM narrating, table talk, or non-game content.
4. **Helpers** consume the cleaned, labeled stream.

**EntityBot (#126)** runs alongside the pipeline, not in it. It is the **only** part that
writes the campaign memory: entities, aliases, relationships and the ontology. It learns
from what the Cleaner reports and from the DM's answers, and pushes every update straight
back to the Cleaner. Later helpers (NPC tracker, PlotBot) send **proposals** to EntityBot;
their DM-confirmation step is what marks a fact confirmed. See "Campaign memory
(EntityBot)" below.

**Build order (decided 2026-10-05):** EntityBot and the campaign memory first (Phase 2a),
then the Transcript Cleaner on top of them (Phase 2b).

**Campaign memory storage (decided 2026-10-05).** The campaign's memory is a
**property graph** (things, plus typed relationships that carry details such as
confidence, source and game time) **stored in Postgres tables**, not in a separate graph
database.
- **Why Postgres:** it keeps the per-server row-level security and per-campaign scoping
  that already protect everything else; nothing extra to run, self-hosted or on
  Kubernetes; fast sound-alike and spelling indexes (`pg_trgm`, `fuzzystrmatch` with
  Double Metaphone; `pgvector` later for meaning); and a small, typed set of functions is
  far safer for an AI to drive than free-form graph queries.
- **Considered and not chosen:** Neo4j or another graph database (a second database
  outside our isolation model, solving a deep-traversal problem DMbot doesn't have: a
  campaign is a few thousand entities, and 1–3 step lookups take milliseconds in
  Postgres); RDF and RDF-star triple stores (the same isolation issue, harder for an AI
  to query correctly, and an "anything unstated may be true" model that clashes with
  "only DM-confirmed facts count"). From RDF we keep the discipline: every type and
  relationship is explicitly defined and every write is checked (SHACL-style).
- **Isolation:** every campaign-memory table has `guild_id` and `campaign_id`, forced
  row-level security, and an `ExportSection`. Links between tables (mention → line,
  relationship → entity, alias → entity) use composite foreign keys that include
  `guild_id` and `campaign_id`, so a row can never point into another campaign. Sound-codes
  are stored in an indexed column. (`pgvector` isn't in the stock Postgres image used for
  self-hosting; it's optional.)
- **Built (#126, first step):** tables `memory_entities`, `memory_aliases`,
  `memory_mentions`, `memory_relations`, `memory_corrections`, `memory_types`,
  `memory_predicates`, `memory_flags` and `memory_changes`, and `dmbot.memory`
  (`MemoryStore`, the only writer). Decided while building:
  - **Sound codes (#126, step 2):** `dmbot.memory.sounds`, modelled on Double Metaphone
    but simplified and tuned for invented names read the English way: vowels dropped
    except at the start, silent letters dropped ("Hrothgar" = "Rothgar"), words joined
    first ("Bell or us" = "Belleros"), and up to four codes where letters have two likely
    sounds. It matches all 59 "sounds like" spellings in the bake-off script, and no two
    of its names share a code. Codes find candidates only; the Transcript Cleaner
    decides.
  - **In-memory lookup (#126, step 2):** `dmbot.memory.lookup` keeps one read-only copy
    per (server, campaign), read in one snapshot. Writes notify with
    `campaign:version:names-changed`, so a mention or flag doesn't make copies reload;
    each time the listener (re)connects, every copy reloads. Secret aliases are known words but
    are never offered as fixes.
  - **Sound-codes are computed in Python**, not by `fuzzystrmatch`, because the in-memory
    copy must code each heard word without a database round trip, and both sides must
    use the same code. They're kept in an indexed text-array column. So no Postgres
    extension is needed yet: `CREATE EXTENSION` needs extra rights some self-hosters
    won't have. `pg_trgm` is added only if a spelling search ever has to run in the
    database.
  - **Undo** works per operation ("batch"): each row change is logged with its before and
    after values. Undo is refused if those rows changed again since, and a delete is
    refused while anything still links to the row, so undo never removes later facts.
  - **Backups** include everything except the change log (a restored campaign starts
    with a fresh undo history) and mentions, which are most of the size (about 15 MB
    for a long campaign) and are rebuilt as new sessions are transcribed.
  - **Only the DM's word confirms** (`source="dm"`): other sources can only propose, or
    drop a proposal. Saying something already known again only strengthens it (the
    DM's confirmation, or "keep secret"); something the DM rejected stays rejected
    unless the DM says it again.
  - **Merges** of two confirmed entries, or of different kinds (a place into an NPC),
    need the DM. Facts moved by a merge are checked again: duplicates are folded into
    one and new problems are flagged.
- **Speed:** during a session the active campaign's names, aliases, sound-codes,
  "don't change" rules and nearby relationships are held **in memory**, keyed by
  (server, campaign), so checking a line doesn't touch the database. Postgres stays the
  single source of truth: changes are written there first.
- **Keeping copies in step across processes:** each change bumps a per-campaign version
  number. EntityBot sends a Postgres `NOTIFY` carrying only the campaign ID and version
  (never names: notifications bypass row-level security), and every process holding a
  copy reloads changes newer than its version, and everything after a reconnect. A change
  reaches every process within about a second. Each live session has **one** Cleaner (a
  session lease), because the rolling window and look-back keep state per session.
- **Later, if ever needed:** Apache AGE adds graph queries on the same Postgres, after
  checking it works with row-level security.

**Hosting target: public bot, shard pods + workers (decided 2026-10-04, #57).** DMbot will
be a public bot others can install, on a scalable Kubernetes platform. *This replaces the
earlier "one pod per Discord server" idea:* Discord gives a bot one gateway connection per
shard (each shard covers up to ~2,500 servers), and isolation is stronger in the database
than in separate volumes.

| Piece | Job | Scales by |
|---|---|---|
| **Shard pods** (`core` + `ears`) | Commands, buttons, voice capture for the servers on their shards | `SHARD_COUNT` / `SHARD_IDS` settings (1 to start) |
| **Transcription & AI workers** (later) | Cloud (or Whisper) transcription, the Transcript Cleaner, EntityBot, rules advice, TimeBot | Live game sessions, via a job queue |
| **Scheduler worker** (later, single) | Reminders, retention cleanup, timed jobs | One runner, so nothing is sent twice |
| **Postgres** | All durable data: campaigns, consent, active sessions, transcripts, campaign memory, and later house rules, clock | Managed database |
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
- Hosted deployments use cloud speech-to-text (the default since 2026-10-05, #128) with the customer's own key
  (#50); self-hosting with local Whisper stays supported (docs/DEPLOY.md, Docker).
- A public bot needs a **privacy policy and terms of service**, and **Discord verification**
  once it's in 75+ servers.

## Phases

| Phase | Deliverable | Notes |
|---|---|---|
| 0 | Scaffolding, CI, ears ↔ core audio pipeline | ✅ Done. Live capture works (#40) |
| 1 | **Listener**: consent by DM buttons (#33–#35), cloud speech-to-text as the default (#128), live transcript in its own channel `#dmb-transcript-<short name>` (#124), stored session transcripts anyone in the server can download (as heard now; cleaned added in Phase 2b), with download buttons sent privately to the DM and recorded players when DMbot stops (#41, #125), transcript format with speaker labels, end-of-session summary (#109) | No AI yet; useful on its own |
| 1.5 | **Campaigns and setup**: `/dmbot start · stop · help`, first-time guide, campaign picker, one DM screen per campaign, voice-channel picker, target/fallback rulesets, optional rules, campaign export/import, bring-your-own API keys | Foundation for everything after |
| 2a | **Campaign memory (EntityBot)**: entities, aliases, relationships and the ontology in Postgres; entity resolution; names added by the DM, from characters, and from an after-session scan of the raw transcript (#126) | Built first: the Cleaner and every later helper read it |
| 2b | **Transcript Cleaner** (live name fixing, off-topic hiding, #127), off-topic filter (#52), speaker tagging | Every helper depends on clean, labeled input |
| 3 | **Rules advisor + house rules**: alerts with ✅ Agree / 🙈 Ignore / ⚖️ Override, house rules by voice with DM approval, `/houserules` | Uses the rules hierarchy below |
| 4 | **TimeBot**: game clock, effect durations, rests, dawn/noon/dusk, split-party clocks | |
| 5 | **NPC tracker** (remembers NPCs, relationships, factions between sessions), then **PlotBot** (DM-confirmed story events) | Read the campaign memory; use **confirmed** entities and relationships only |
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
| `/transcript` | Download a session transcript: **cleaned**, **as heard** (raw), or **both** (#125). Anyone in the server can use it. If DMbot is still recording, the DM is told "This transcript ends at 19:42. To get the whole session, stop with `/dmbot stop` first." and a player is told "This transcript ends at 19:42. You'll get a message with the full transcript when the DM ends the session." [Download anyway] [Cancel] |

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
optional-rule settings, rulesets, campaign memory (names, aliases, NPCs and relationships), game clock and
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

**Transcription (decided 2026-10-03; default changed 2026-10-05).** Per-speaker audio
means no diarization is needed. Every engine sits behind one `Transcriber` interface and
is chosen by `TRANSCRIBER=` in config.
- **Default: a paid cloud speech-to-text service** (chosen in #128). **Built first:
  Deepgram Nova-3, `TRANSCRIBER=deepgram`** (owner decision, 2026-10-06, #170), after a
  quick comparison where it and Speechmatics tied with names hinted (23/24 D&D terms,
  local Whisper 19/24; docs/testing-history.log). The full bake-off (#128) still runs to
  confirm. It doesn't yet pass per-word confidence through: the `Transcriber` interface
  returns text only, which the Cleaner work (#127) changes. Requirements:
  - custom words sent **with each request**, in effect immediately, with no training or
    pre-built vocabulary (about 50–300 per campaign, from the campaign memory);
  - "sounds like" hints if possible;
  - per-word confidence and timings (the Cleaner uses them to find doubtful words);
  - short clips (1–15 s, 16 kHz mono) back within about 1 s;
  - pay-as-you-go with the customer's own key, US data processing, and no training on
    customers' audio.
- **Local Whisper** (faster-whisper) stays supported **for self-hosting**, with relaxed
  Cleaner deadlines (see "Transcript Cleaner").
- The interface passes a **vocabulary** (terms, optional sounds-like) instead of a plain
  name list, and returns per-word confidence.

**Hosting, self-hosted (decided 2026-10-03).** A cloud server runs ears, core, and Postgres
(decided 2026-10-04: Postgres everywhere, so self-hosting and hosting debug the same database). With cloud
speech-to-text (the default) the server can be small. Self-hosters who choose local
Whisper need a bigger server: a CPU-only server suits the `base`/`small` models; larger
models need a GPU server. Both parts ship as Docker containers started with one
`docker compose` command; see docs/DEPLOY.md.

**Consent (decided 2026-10-04).** Consent is asked by **private message with buttons**,
the way other Discord bots handle opt-ins. No typing, and no slash command needed.
- When `/dmbot start` starts a session, DMbot DMs everyone in the table voice channel
  (the DM included), and anyone who joins later. The message says DMbot is for
  entertainment only, other uses are prohibited, their voice will be recorded and
  transcribed. With the 2026-10-05 decisions it must also say, plainly (final wording:
  #33):
  - **anyone in this Discord server can read the transcript,** live in the campaign's
    transcript channel and as downloads, and that stays true for what was recorded even
    if you stop recording later;
  - after the recording ends, **you can download the transcript as heard, cleaned, or
    both** (#125), and the "as heard" version keeps everything said, off-topic talk
    included;
  - your voice is sent to a speech-to-text service to be written down (unless the server
    runs local Whisper).

  This changes the terms, so the terms version goes up and everyone is asked again (#35).
  **Built for the switch to an outside company (#170):** each yes records whether the
  request said another company writes things down. While the server uses one
  (`TRANSCRIBER=deepgram` or `cloud`), only those yeses count; everyone else isn't
  recorded and gets the question again. An old "I consent" button from before the switch
  shows the new question instead of saving a yes. The general terms version (#35) still
  covers other wording changes.
  It has a **✅ I consent** button (#33).
- **Consent carries over** between sessions, per server. In **every session**, a consented
  person gets one short private reminder with the date they consented, plus a
  **🛑 Stop recording me** button (#33, #34). Rejoining in the same session doesn't send
  another (built 2026-10-05: once per person per session, not once per join).
- **No thanks** is not remembered: that person is asked again next session, and the
  message says so. Pressing **No thanks** on an old message also removes any consent
  given since, so an old message can never leave someone recorded after saying no.
- Revoking takes effect immediately. Queued and in-flight audio and text for that person
  are discarded.
- People with DMs off are nudged by the public notice in the voice channel's chat ("No
  message from DMbot? … type `/consent give`"). The DM screen names everyone who couldn't
  be reached, and they're asked again if they rejoin. Nobody is recorded without consent.
- `/consent give` and `/consent revoke` remain as fallbacks. `/consent give` shows the same
  request and buttons privately, so everyone agrees to the same terms.
- **More than one process (sharding):** buttons in private messages reach the process
  serving shard 0. Until consent changes reach every process (see "Consent changes reach
  every process" above), a button for a server another process serves changes nothing and
  points to `/consent give` / `/consent revoke`, which Discord routes to the right process.
- Consent records store the terms version, the UTC timestamp, and the method
  (`private_message`, or `consent_command` for the reply to `/consent give`). Changing the
  consent wording re-prompts everyone (#35, built 2026-10-06): a yes under older wording
  stops counting at once, so that person isn't recorded until they agree again. Their new
  request starts with a "What's new" line naming the change, and the DM screen says
  "🔁 Asked again: …" so the DM knows why. After a restart, people in voice whose yes no
  longer counts are asked (nobody else is). Version 2 is the "anyone in this server can
  read it" wording; every yes saved before versions were recorded is treated as version 1
  (we can't tell which wording each person saw). A test pins the request's wording to
  the version number.
- The public "DMbot is listening" notice in the voice channel's chat still posts once per
  `/dmbot start`, is not repeated after a voice-service reconnect, and the DM is warned if
  it can't be posted.

**Transcripts vs. the DM screen (decided 2026-10-04).**
| Content | Who sees it |
|---|---|
| **Transcripts** (what was said at the table) | **Anyone in the Discord server** (decided 2026-10-05): live in the transcript channel (#124), and as downloads, raw or cleaned (#41, #125). Only people who agreed are ever recorded, and the consent request tells them the whole server can read it. **View only:** "Did they mean…?" prompts, Undo buttons, DM sidebar messages and all other DM-screen content never appear in the transcript channel or a transcript. |
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
| Live transcript (cleaned lines, view only) | `dmb-transcript-rmfthfrstmdn` | Phase 1 (#124, built 2026-10-06: lines as heard until the Cleaner) |
| Rules archive (house rules, overrides, rulings) | `dmb-rules-rmfthfrstmdn` | Phase 3 |
| Game time (clock, effects, rests) | `dmb-time-rmfthfrstmdn` | Phase 4 |
| NPCs (roster, relationships, factions) | `dmb-npcs-rmfthfrstmdn` | Phase 5 |
| Plot (story beats, places, hooks) | `dmb-plot-rmfthfrstmdn` | Phase 5 |

- **The DM screen uses the full campaign name:** `rime-of-the-frostmaiden`.
- **Sub-channels use a short name** (`rmfthfrstmdn`), made like this:
  1. Lowercase the name and keep only `a–z` and `0–9` (spaces, dashes, punctuation,
     accents and emoji are dropped). `Rime of the Frostmaiden` → `rimeofthefrostmaiden`.
     If nothing is left (a name of only emoji, say), the short name is `campaign`.
  2. If **more than half of the letters are vowels** (a, e, i, o, u; **y is not a
     vowel**; digits don't count either way), keep the vowels and cut it to 15
     characters. `Eerie Aura` → `eerieaura` (7 of 9 letters are vowels), not `rr`.
  3. Otherwise remove the vowels and cut it to 15 characters.
     `rimeofthefrostmaiden` → `rmfthfrstmdn`.
- **Clashes get a number at the front, starting at 2.** If a new campaign's screen name
  or short name is already used by another campaign in the server, the new campaign gets
  the lowest free number from 2 up, on the screen name and every sub-channel name
  (transcript included). *Example:* "Frozen Sick" and
  "Frozens Cake" both shorten to `frznsck`. "Frozens Cake" was made second, so its
  channels are `dmb-dm-screen-2frozens-cake` and `dmb-time-2frznsck` (and so on).
- Clashes are checked on the **names Discord will actually show**, numbers included. So
  a name that starts with a digit can't land on another campaign's clash number:
  "2 Frozens Cake" next to "Frozens Cake" (already `2frznsck`) gets a number too.
- The number is chosen once, the first time the campaign's DM screen is made, and stored.
  It changes only if the campaign is renamed so that its names clash; other campaigns'
  names never shift. Campaigns made before this rule get their number the next time
  they start, in that order.
- Discord allows 100 characters per channel name; longer names are cut to fit, and two
  names that only differ after the cut count as a clash.
- Renaming an old-style screen (`dm-screen-…`), or after a campaign rename, is
  best-effort: Discord allows two renames per channel every 10 minutes, so DMbot tries
  again at the next start rather than holding anything up. A channel used by two
  campaigns is never renamed.

*Who can see what.* Channels are either **controlled** or **unrestricted**:

| Kind | Channels | Players see it |
|---|---|---|
| **Controlled** | DM screen, rules, NPCs, plot (and, by default, any channel added later) | As the campaign's **DM-screen visibility** says: private, opt-in peek (**default**), or open. The setting applies to all controlled channels at once. |
| **Unrestricted** | Game time, live transcript | Always, by everyone in the server (read-only). Withdrawing consent stops recording but doesn't remove access (decided 2026-10-05, #124) |

- Players can **read but never post** in any DMbot channel (no threads or reactions
  either), the same read-only access as a DM-screen peek. **Slash commands are never
  blocked** in DMbot's channels (the server's own setting applies): their replies are
  private, and a blocked command hangs on "Sending command..." with no explanation. This
  covers the live transcript channel (#188), peeks and an open DM screen (#190); old
  blocks are lifted the next time DMbot sets up the channel.
- Changing the setting (the help-card buttons) updates every controlled channel together.
  Peeking opens all controlled channels for that player; hiding closes them all. Once
  there's more than one controlled channel, the peek warning must name every channel a
  peek opens (rules, NPCs, plot), so players know exactly what they're agreeing to see.
- Server owners and admins always see every channel (see above).

*Modes.*
- **Compact mode:** everything goes in the DM screen **except the live transcript**, which
  always has its own channel next to it, in the DM screen's category (decided 2026-10-05: the DM
  screen was too noisy). This is the default while the DM screen and transcript are the
  only channels built (Phases 1–2).
- **Every DMbot channel has a topic and a pinned "What's this channel?" card in every
  mode.** For the transcript: "Live transcript for **Rime of the Frostmaiden**. View only.
  Anyone in this server can read it. Only people who agreed are recorded. Use
  `/transcript` to download it."
- **The DM screen holds only what the DM needs to see or act on:** "Did they mean…?"
  questions and Undo buttons, rules alerts, later NPC, plot and time notes, warnings
  (audio gaps, speech-to-text falling behind), the start and stop messages, and the
  end-of-session summary (#109). The 15-second capture checks leave it; audio health
  shows only as a warning when there's a problem, and in the summary.
- **Organized mode:** each campaign gets a category, `📋 Rime of the Frostmaiden`
  (categories keep capitals and emoji), holding its `dmb-` channels. Each channel has a
  pinned "What's this channel?" card saying what it's for and who can see it. This
  becomes the default once the first sub-channel other than the transcript ships. A DM can switch back to compact
  mode, which keeps players' notifications quiet too.
- Discord allows 500 channels per server and 50 per category, so with six channels and
  a category per campaign, a server can hold about 70 campaigns in organized mode.

*Why.* It mirrors a real table: the DM screen stays hidden, reference material sits on
the table. Actionable alerts stay separate from reference information. Players can follow
the clock and NPCs without seeing rulings, and each channel can be muted on its own.
Code changes: #87.

**Live transcript channel: built (2026-10-06, #124).** Decided while building:
- It's made at `/dmbot start`, next to the DM screen in the same category, and kept
  view-only for everyone (DMs included: only DMbot posts there; no threads or reactions).
  Slash commands still work there, with private replies (#188). It's only edited when
  something differs (Discord allows few channel edits per ten minutes), and its card is
  found among the pins. A problem setting it up is reported in the DM screen and in the
  start reply, and never stops the session; losing the channel mid-session stops the
  transcript and tells the DM once.
- Lines are posted every 2 seconds as new messages (no edits yet), at most 2,000
  characters each, in the order the speech *started*. A line leaves the queue only once
  posted (a failed post is retried), and consent is checked again for every line as
  each message is built. Speech from a stopped session never reaches the next one.
  Speech is escaped (no formatting, pings or links; link previews off). Every post (lines
  and the card) is sent silently, so nobody gets a pop-up or phone notification for it;
  the channel still shows as unread. Until the Cleaner
  (Phase 2b) lines are as heard and have no `{entity}` labels (#53).
- `/dmbot stop` posts the "Session ended" divider in the background, so it answers in
  time. Words still being written down when the session stops are lost (as before,
  #109); at shutdown, waiting lines are posted first.
- The 15-second capture checks now go only to the core log. The DM screen gets
  "⚠️ Mia's voice is cutting out for DMbot (82% got through)…" below 90%, once per
  person, again only if it gets 10 points worse or after 10 minutes (#134).
- The card mentions `/transcript` now that downloads exist (#125).

**Stored transcripts and downloads: built (2026-10-06, #41, #125).** Decided while
building:
- Each session has a row (kept after a restart: same campaign and start time) and each
  piece of speech from someone who agreed is a line, with `heard` (never changed) and
  `text` (cleaned; NULL while it's the same, which is always until the Cleaner). Lines
  are saved in batches every 5 seconds, and the session's row keeps its line count and
  speakers, so listing sessions never reads lines. Consent is checked when a batch is
  taken and again after it's saved (lines of someone who pressed Stop mid-save are
  taken back out); pressing Stop also drops that person's unsaved lines. If saving
  can't start, DMbot keeps the lines and keeps trying, and tells the DM once.
  Speaker names aren't stored: a download uses display names at that moment ("Someone"
  if DMbot can't find the person). Transcripts are deleted with their campaign and
  aren't part of campaign backups (yet).
- Sessions are named by number in their campaign ("Session 7 · 2 h 14 min · Oct 6
  (UTC)"): DMbot has no time zone setting, and a UTC date alone can look like the wrong
  day to an evening table. The menu's prompt shows the newest session's start in each
  person's own time (a Discord timestamp).
- `/transcript` (anyone in the server): pick the campaign, then a session (newest 25
  with something said), and get a private `.txt` file
  (`frostmaiden-session-7-as-heard.txt`): a short header, then `0:42:10 Mia: …` per
  line. The file says it's what DMbot wrote down, with no fixes, and that some words
  may be misheard. While DMbot is still recording that session it warns first
  ([Download anyway] [Cancel]), in different words for the DM and players. Replies
  are deferred first, since building a file can take more than Discord's 3 seconds.
- When a session ends, the DM(s) and everyone recorded get a private message with one
  **[🎙 Download transcript]** button, which works after a restart and only for people
  still in the server. People with private messages off use `/transcript`.
- Until the Cleaner, there's only the "as heard" version. If saving fails at the start,
  the DM screen says there'll be no download for this session.
- Still to come: "Delete my past transcripts", retention, cleaned and both downloads
  (Phase 2b), and the `{entity}` labels (#53).

**Transcripts and downloads (decided 2026-10-05, #124, #125).** Sessions and their
participants are stored. Each line is kept in two versions: **as heard** (exactly what
speech-to-text produced, never changed) and **cleaned** (after the Transcript Cleaner),
plus the list of fixes between them.
- **Live:** the cleaned lines stream into `#dmb-transcript-<short name>`, grouped into a
  message every few seconds (Discord allows a bot about 5 messages per 5 seconds per
  channel, and edits share that limit). Before the Cleaner exists (Phase 2b), lines
  appear as heard. Each session opens and closes with a divider ("── 🔴 Session started ·
  Oct 5, 7:30 pm ──", "── ⏹ Session ended ──"). Speaker names are bold, Discord's own
  message time replaces per-line times, and transcribed text can never ping anyone.
- **Late fixes:** messages from the last ~30 s are edited in place. Older fixes update the
  stored cleaned transcript and the downloads only.
- **When DMbot stops,** the DM(s) and every player recorded **in that session** get a
  private message: "The session for **<campaign>** has ended. Download the transcript:"
  **[📄 Cleaned]** **[🎙 As heard]** **[Both (2 files)]**, with one line explaining each
  ("Cleaned: names spelled right, off-topic chat left out." "As heard: exactly what
  DMbot heard, word for word."). It's a shortcut: **anyone in the server** can get the
  same choice with `/transcript`.
  Until the Cleaner exists, it's one **[🎙 Download transcript]** button. The message adds
  "If the DM fixes names later, download again for the updated version."
- **The cleaned file starts with a note:** "DMbot fixed the spelling of some names. The
  'As heard' file has the exact words."
- **Downloads use readable labels** (`0:42:10 Cerric (Mia): …`, `Narrator (Sam): …`,
  `Mia (table talk): …`) with time since the session started. The `{entity}` format
  below is the stored and backup format.
- Transcripts never contain DM-screen content. The Cleaner never **adds** a secret
  identity to a line; the "as heard" version contains only what was actually said.

**Transcript format (decided 2026-10-04).** One line per utterance:
`[timestamp] (Discord name) {entity}: text`, where `{entity}` is the in-game character,
an NPC or other in-game entity, `{narrating}` for the DM, `{table_talk}`, or
`{non-game_content}`. DM voice messages to the bot are DM-screen content: they appear
only in the DM screen, marked `[DM Sidebar Discussion]`, **never in a transcript**.
- **Off-topic talk (decided 2026-10-05, #52):** in the **cleaned** transcript, talk that
  is clearly unrelated (not the campaign, D&D, rules or table talk) is replaced by one
  line saying how long it was: `[timestamp] (Discord name) [1m 22s of off-topic chat
  skipped]` (`[8s of off-topic chat skipped]` for a short one), so players can see the
  transcript is working. A run of it from one person collapses into one line with the
  total time. When unsure, the line is kept. The **as heard** transcript keeps
  everything.
- **Players' lines:** players only speak in character, for a familiar or pet, as table
  talk, or off-topic, so the AI's best guess is used with no prompts.
- **DM's lines:** when DMbot isn't confident who the DM is voicing (narration vs which
  NPC), it asks in the DM screen, because a wrong label can throw off plot and NPC tracking.

**Campaign memory (EntityBot) (decided 2026-10-05, #126).** Each campaign remembers its
characters, NPCs, creatures, places, factions, items, spells, deities and events: their
names and nicknames, and how they relate. Users only ever see plain words ("DMbot
remembers Belleros is Cerric's mentor"), never "graph", "entity" or "ontology".
- **What's stored** (Postgres, scoped to server and campaign, all in backups):
  - **entities**, with a status (`proposed`, `confirmed`, `rejected`, `merged`);
  - **aliases**: every way a name is said or written, with sound-codes, a kind (`full`,
    `short`, `nickname`, `title`, `misheard`), who uses it, and a `secret` flag;
  - **mentions**: "this part of this line refers to this entity", with confidence and
    how it was matched;
  - **relationships**, with confidence, status, source mentions, and when they're true
    (session and game time), so history is kept, never overwritten. Game time stays
    empty until TimeBot exists (Phase 4); until then facts are ordered by session and
    game-time checks are skipped;
  - **corrections**, the **ontology** (below), and an **append-only change log**, so
    every change can be undone.
- **EntityBot is the only writer.** New names start as **proposed** and are used for
  matching only. The NPC tracker and PlotBot read **confirmed** data only, which keeps
  the rule that they record only DM-confirmed facts.
- **Where names come from:** names the DM adds, characters, the DM's answers to "Did they
  mean…?", Undo presses, the Transcript Cleaner's reports, and an after-session scan of
  the raw transcript (skipping lines labeled off-topic) that proposes new names for the DM
  to confirm.
- **Keeping it tidy (entity resolution):**
  - Nicknames grow: when "Bell" keeps standing for Belleros, EntityBot proposes the alias.
  - Merges: two proposed entities merge automatically only on strong evidence; proposed +
    confirmed asks the DM; **two confirmed entities are never merged automatically**.
    Merges and splits are logged and can be undone.
  - Ambiguous names ("the captain") are resolved by scene and time, asking only when it
    matters.
  - **Secret identities** ("the hooded stranger" is Belleros) are DM-only links: the DM
    marks one with a **[🤫 Keep secret from players]** button. They appear only in the DM
    screen, never in a transcript, and never in the NPC or plot channels unless the
    campaign's visibility is private.
  - An **after-session cleanup** clears stale proposals, flags likely duplicates, checks
    the rules below, and sends the DM a short plain report of anything to decide, for
    example: "📝 **After the session: 2 things to check** · **Hrothgar**: new name, heard
    4 times. Someone or something in your game? [Yes, add it] [Same as… ▾] [Not a name]
    · Are **Bell** and **Belleros** the same? [Yes, same] [No, different]". It never says
    proposed, confirmed, merged or entity.
- **Speed:** the active campaign's names and nearby relationships are kept in memory
  during a session, and every change reaches that copy at once, so a correction works on
  the very next line.

**Campaign memory step 3: built (2026-10-06, #126).**
- **`/dmbot names`** (the campaign's DMs and server managers only, private): the names
  DMbot knows, with **➕ Add a name** (a short form: name, other names or nicknames,
  disguises or secret identities; then "What is it?"), **🧑 Add a player's character**
  (pick the player, then the name; stored with who plays it, for speaker labels later;
  like the DM list, a backup keeps that player as a Discord user ID),
  and **📝 Check new names**. Everything the DM says here counts as confirmed.
- **After each session** DMbot reads what it heard (only from people who still agree)
  and suggests up to 10 names it doesn't know: capitalized mid-sentence, heard at least
  twice, never in lower case, not a common word or game term, and not anything DMbot
  already has an answer for (known names, "Not a name", "keep as heard", people at the
  table, each word of their display names included). The DM screen shows "📝 N new
  names to check from this session" (the check itself opens privately for the DM) with a
  button that works after a restart. Suggestions are stored as proposed, kind "other".
- **Checking a suggestion:** ✅ Yes, add it (then what it is), 🔗 Same as… (a known
  name, sound-alikes first: it becomes another way to say that name), 🚫 Not a name
  (never suggested again), Later (ends this round; it's still waiting next time). A
  suggestion can also be marked as a player's character.
- **Speech-to-text hints** now come from the campaign's names, most useful first:
  players' characters, confirmed names, players' display names, then suggested names
  (one hint per name, however it's capitalized).
  Secret names are never sent. The in-memory copy follows changes live.

**Campaign memory rules (the ontology) (decided 2026-10-05).** EntityBot alone builds and
maintains the ontology; there is no human graph engineer. So it is small, strict,
versioned and self-checking:
1. **A fixed core, defined in code:** types (character, split into player character and
   NPC; creature; place; faction; item; spell; deity; event; concept) and relationships
   (`located_in`, `member_of`, `ally_of`, `enemy_of`, kin, `owns`, `knows`, `serves`,
   `appears_in`). EntityBot can extend the core but never change it; core changes come
   only in code releases, after review.
2. **Extensions belong to one campaign** and never cross campaigns or servers. A useful
   pattern reaches the core only through a code release.
3. **Reuse before creating:** before adding a relationship, search the existing ones by
   name and meaning ("friends_with" maps to `ally_of`). Every new term needs a plain
   description, a parent, a domain and range, examples, and a reason.
4. **Only add, never silently rewrite:** terms are deprecated, not deleted, and every move
   of existing data to another term is logged.
5. **Checked on every write:** domain and range, how many are allowed ("one birthplace"),
   contradictions (alive and dead at the same game time), and inverse consistency. A
   failed check becomes a **flag** for review, never a silent fix.
6. **Every fact has a source and a confidence,** and a status. The DM can always
   override.
7. **Facts are tied to game time,** so a change (ally to enemy) adds history.
8. **AI proposes, rules decide:** AI may suggest aliases, merges and new terms;
   deterministic checks accept or reject them. Only what was said or what the DM
   confirmed is stored. DMbot never invents story.
9. **Limited growth:** a cap on new terms per session (about 5), and health numbers
   tracked (duplicates, orphans, unused terms, how often the DM is asked).
10. **Plain words for users,** always.

**Transcript Cleaner (decided 2026-10-05, #127).** Fixes misheard names in each line as
it arrives, so the live transcript, the downloads and every helper get clean text. It
reads the campaign memory and never changes it.
- **Two hard rules:** fix the **spelling of what was said, never the meaning**; and
  **never reveal a secret identity** in a transcript ("the hooded stranger" stays "the
  hooded stranger").
- **Suspicious first, match second.** A word is considered only if it isn't a real word
  or known name, speech-to-text was unsure of it, or the sentence reads badly with it.
  **A sentence that makes sense as heard is never changed.** In "Beleros has a serrated
  blade so he will saw through the rope", only the name's spelling is fixed ("Belleros");
  "serrated" is never compared with "Cerric".
- **Finding candidates:** exact alias, sound-code (Double Metaphone, including runs of
  1–3 words joined together, so "Bell or us" matches "Belleros"), and spelling similarity.
- **Scoring:** speech sounds (phonemes, via a letters-to-sounds model, since invented
  names have no dictionary pronunciation), spelling, context (who's in the scene, recently
  mentioned, related to the speaker's character), and how unusual the word is. Meaning
  (embeddings) is not used for sound; it helps EntityBot with descriptions and resolution
  later.
- **More evidence for riskier fixes:** a name's spelling (low risk); a broken phrase
  joined into a name (the original must read badly and the fix clearly better); a real
  word replaced by a name (strong context, or ask the DM). The fix must fit the grammar
  (names go where names go).
- **A wrong fix is worse than a missed one:** when unsure, leave the words as heard. An
  Undo or **Keep as heard** becomes a "don't change this" rule for the campaign. Target: wrong fixes at or
  below 1%.
- **Confidence:**
  | Confidence | What happens |
  |---|---|
  | High | Fixed silently before the line appears |
  | Medium | Fixed, plus a note with **Undo** in the DM screen |
  | Low | Left as heard; the **DM screen** asks "Did they mean…?"; the line is fixed if the DM picks one |

  - **Only confirmed names and aliases can make a silent (high) fix.** A proposed name
    reaches at most medium, which always shows Undo.
  - "Did they mean…?" and Undo appear **only in the DM screen**, never in the transcript
    channel. The question leads with what was heard: "❓ **Mia said "Bell or us"**: did
    they mean… [Belleros] [Bellamy] [Type it…] [Keep as heard]". At most 3 options.
    **Keep as heard** saves a "don't change this" rule, like Undo.
  - **Not flooding the DM screen:** medium fixes go into one "✏️ Name fixes this scene"
    message that is edited in place, one line and one Undo each. At most one question is
    open at a time, with a cooldown, and only for names that come up again or matter to
    the scene. Unanswered questions expire quietly (the line stays as heard) and move to
    the after-session report. At the **quiet** verbosity level there are no live
    questions at all.
  - **Undo says what it learned:** "↩️ Undone. DMbot won't change "Sara" to **Cerric**
    again in this campaign. [Allow again]"
- **Rolling window:** one utterance at a time (DMbot already splits speech after a ~2 s
  pause or 15 s of talk), with the last ~60–90 s of the table (all speakers, in spoken
  order) and the session state (scene, recent mentions) as context. A **look-back pass**
  checks the join with the same speaker's next piece ("…Bell or" | "us will…") and may
  revise a line from the last ~30 s. Helpers read lines after a ~2 s settle time; a
  revision after that sends a "line changed" event, and helpers that used the line
  re-check it.
- **Escalation:** rules, then **re-listen** (send that 1–2 s of audio back to
  speech-to-text with a tiny hint list), then AI, then ask the DM. **Timing:** the
  Cleaner and the off-topic filter add at most **0.7 s** after speech-to-text returns the
  line; re-listen and AI run in parallel within that, so with cloud speech-to-text a line
  shows about 1–2 s after the speaker stops. With local Whisper the extra time relaxes to
  about 2.5 s and re-listen is off. Whatever isn't back in time leaves the safe version
  showing, and the line is updated when the answer arrives (or the off-topic marker
  replaces it).
- **Re-listen audio:** kept in memory for about 60 s only, in the process that captured
  it (never queued, never in Redis, never logged, never stored). **Consent is re-checked
  right before every re-listen request**, and the audio is dropped at once on revoke.
  Re-listen is added after the first version, once close calls can be measured.
- **Learning is immediate:** a confirmed correction becomes a `misheard` alias and
  reaches the Cleaner within about a second. Earlier lines of the same session with the
  same mishearing are re-checked from "as heard" and fixed, with each change logged.
  Previous sessions change only if the DM asks.
- **Measured** on a test set of mishearings ("Sara" → Cerric, "Bell or us" → Belleros)
  and traps (real words, nicknames, secret aliases, the serrated-blade sentence), plus
  live timing (speech end → line shown).

**Off-topic filter (decided 2026-10-04; updated 2026-10-05).** A very light, fast AI pass
right after the Cleaner. Scheduling, life updates, and other non-game talk are labeled
`{non-game_content}` and not analyzed further, which saves cost. In the cleaned
transcript, clearly unrelated talk shows as `[1m 22s of off-topic chat skipped]` (see
"Transcript format"); table talk and anything unsure stay. A live line waits for the
filter within the Cleaner's time budget; if the filter is late, the line is posted and
then edited to the marker.

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
DMbot transcribes it, answers in the DM screen, and logs it there as
`[DM Sidebar Discussion]`. The table never hears it, and it never goes into a transcript. An optional hotkey helper app for the DM's PC may come later.

**AI and speech API keys (decided 2026-10-04).** Start with **bring your own key**: each
server's DM or admin enters their own Anthropic API key (and a cloud speech-to-text key if
used) through a private pop-up form, never typed in a channel. Keys are stored encrypted
and per server; usage and spending limits live in their own provider account. A paid
service (the owner's key, metered and billed per server) may follow later; the code
keeps a single "who pays for this call" seam so that switch stays small.

**Retention.** Configurable auto-delete of transcripts per server (audio is never
stored), and a "Delete my past transcripts" action for each player. Deleting a person's
lines covers both versions, the fixes list, the mentions that point at those lines, and
that person in any alias's "who uses it" field. When someone withdraws, recording stops
at once and they're told: "🛑 Stopped. DMbot won't record you anymore in <server>. What
DMbot already wrote down stays, and anyone in this server can still read it." (When
"Delete my past transcripts" ships, that message must also say how to use it.)

**Bots are never transcribed** (music bots etc.) — enforced in ears by an allowlist.

**License (decided 2026-10-03).** Public repository under PolyForm Strict 1.0.0.

## Open decisions

- Final wording of the consent DM and join reminder (#33)
- Whether revoking consent also removes a person's past lines from stored transcripts (#34)
- Which cloud speech-to-text service (#128): Deepgram built first (#170); the bake-off
  confirms or changes it
- The Cleaner's confidence thresholds and target accuracy, set from the test set (#127)
- Whether to use an embeddings provider for meaning-based matching, and which (#126)
- Whether players may suggest corrections to their own transcript lines, with the DM
  approving (#127)
- Whether the downloadable cleaned transcript marks changed words (default: no marks;
  the DM sees every fix)
- Hosting provider and Kubernetes setup (Helm) for the public bot (transcription is cloud by default; GPU workers only for self-hosters who want them)
- Privacy policy and terms of service text for the public bot
