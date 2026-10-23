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
5. **Quiet by default.** "How much DMbot says" (quiet / normal / chatty) and cooldowns
   keep the DM from being flooded.
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
  with packets expected per utterance, and means "core got the audio". Speaker pauses,
  which clients mark with five silence frames, are excluded. Three kinds of loss are
  counted besides the gaps (decided 2026-10-09, #43, #45): packets the voice library
  **fails to decrypt** (DAVE; it says so in a debug event, counted per speaker, including
  right after a pause, where the gap would look like the pause), packets the **Opus
  decoder refuses**, and frames the **link to core drops** when it is busy. They are left
  out of "received", and the health message carries each count (omitted when zero) so the
  terminal log can say why. They lower the percent, but the DM screen is not told more
  often: only lines that read garbled (or a large loss) warn the DM (#699). Not counted:
  packets that arrive while someone never seen before is looked up (rare, #270).
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

**Story memory (decided 2026-10-06; docs/STORY_MEMORY.md, #227).** Phase 5
grows the campaign memory from names and connections into what happened: claims (what
was said, and how: the DM's narration or an NPC, which both describe what the
characters perceive, a player's belief, a plan) kept apart from DM-confirmed facts;
state facts (alive, dead, missing); story threads, promises and clues; who knows what;
hierarchical reputations derived from deeds; and, behind a per-campaign **Shared
story** switch, a separate layer for the plan of a written adventure (published,
homebrew or fan fiction). EntityBot
stays the only writer, split into small AI roles (extractor, continuity checker,
ontology steward, graph curator, summarizer) behind deterministic checks.

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
    Undo works for at least 30 days (`MEMORY_CHANGELOG_KEEP_DAYS`): after each session, older
    change-log rows are deleted, whole batches at a time, so the log doesn't outgrow
    the memory. Undo texts say so, and pressing an older Undo says it's too late (#164).
  - **Backups** include everything except the change log (a restored campaign starts
    with a fresh undo history) and mentions, which are most of the size (about 15 MB
    for a long campaign) and are rebuilt as new sessions are transcribed.
    **One exception to "complete" (Supervisor, 2026-10-17, #1698):** the text of an adventure the
    DM gave DMbot is not in a backup, only its list (name, size, fingerprint, who, when), because
    anyone in the server may download a paid campaign's backup and the DM confirmed the right to
    use that text in that campaign only. A restore shows each as "give DMbot this file again".
    The story plan read from that text (#1714) is left out for the same reason, and so are its
    read records; reading again rebuilds it. **One thing of it is kept:** what the reading cost,
    as amounts by file fingerprint and month (#1723), so a restore does not give a file a fresh $3
    or a campaign a fresh $10; a replace never lowers what a campaign has already spent.
  - **Flags DMbot closes itself get no status of their own (decided 2026-10-08, #347):**
    Undo of the close reopens the flag (#362), and a clash that comes back is flagged
    again when the fact is written again, so the realistic case (the DM undoes a
    rejection, the fact is re-written, a new flag appears) needs no migration. A
    `closed_by_dmbot` status is added only if a recording or a live session shows a DM
    missing a returned clash.
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
- **Confirmed 2026-10-14 (owner asked: RDF-star or a labelled property graph?):** a labelled property
  graph (LPG) in these tables, with RDF's discipline. Every fact is one edge, subject → predicate → object
  (`Mia → plays → Vaelora`), and the edge carries its details as properties (confidence, source, status,
  game time, evidence), which is what RDF-star's statements about statements would give us. **Each
  relationship is stored once, in one direction:** a predicate declares its inverse's label ("played by")
  for reading from the other side, and synonyms (`controls`, `controlled_by`) map to it, so the graph
  never holds the same fact twice under different words. **The vocabulary grows, but governed:** the core
  predicates are in code; the AI may *propose* a new predicate for one campaign, which is accepted only
  if no existing one fits (searched by name and meaning) and it comes with a description, domain and
  range, inverse label, how many are allowed and an example (rules 1–3, 9). An AI free to invent edges
  would scatter one idea across many names (plays, controls, is_player_of, runs) and the checks and
  questions ("who plays Vaelora?") would miss facts.

**A worker container, and a smaller bot.py** (owner, 2026-10-20; details Supervisor's; the first step toward #57's workers).
- **The worker.** Heavy background work leaves the live bot for a `worker` container: recap writing, audio rendering with ffmpeg, cleaning imported sessions, PlotBot's checks and the story extractor. It runs the same `core` image and code with a different start command, against the same database, with no new network API. The bot keeps everything that happens live at the table: voice, the Cleaner, the sidebar, rules cards, the live heads-up and the buttons.
- **No races: the database is the only coordinator.**
  - **Claiming work.** Every job is claimed with a lease, a conditional `UPDATE … WHERE leased_to <= now`, as `recap_jobs` already does. Two processes can never work one job, and a crashed worker's lease simply expires and the job is picked up again. In-process locks (`asyncio.Lock`) don't count across processes: the audio worker's "one at a time" lock becomes a lease row.
  - **Every step resumable.** Every job step is idempotent and resumable, as the recap job already is. A paid call is never repeated for a step that already finished (the stored state says so).
  - **Changes during a job.** A DM's change wins over a running job: jobs re-read the approval or text hash before every paid call, as the audio worker already does, and stop cleanly.
- **Results back to Discord through an outbox.** Only the bot holds the Discord connection, so the worker never posts. It writes an outbox row ("recap ready", "names to check") and the bot delivers it. The bot is woken by Postgres `LISTEN`/`NOTIFY`, with a poll every 30 s as a backstop. Delivery is at most a few seconds late and is never lost: a row is marked sent only after Discord accepts it.
- **No waiting, so no time-outs.**
  - **Nothing live waits on the worker.** Discord buttons answer within Discord's 3 seconds by deferring, then follow up.
  - **The website** queues a job and shows its state ("Writing…", "Checking the change…"), polling the job's status. It never holds a request open for an AI call, except short ones under about 10 s (speech to text for a 2-minute clip).
- **No starving backlog.**
  - **Limits per kind:** audio at most 1 at a time, recaps at most 3, imports and PlotBot at most 1 per campaign.
  - **Fair order:** jobs are taken oldest first per campaign, round-robin across campaigns, so one big import never holds up another table's recap.
  - **Visible depth:** the queue depth and oldest waiting job go to the log every round. The DM sees "queued" with its place.
  - **Stuck jobs:** a job past its tries fails with a plain reason, as now.
- **Resources.** The worker container gets its own CPU and memory limits, so ffmpeg can never starve `ears` or the bot during a game. The bot and the worker always deploy from the same commit (one image), and dev1 restarts both.
- **The switch is temporary.** `DMBOT_WORKER` exists only for rollback while the worker is new. Once dev1 has run the worker live for **two weeks of real sessions**, with every recap, audio render and import done by the worker and no rollback needed, a cleanup PR:
  - removes the switch;
  - deletes the bot's in-process copies of the job loops, and any code that only served them;
  - leaves the bot with only its live work and the outbox delivery.

  dev1 records the two weeks in `docs/testing-history.log`; Supervisor files the cleanup when they're done. **Tracked in #2133, which stays open until the cleanup has merged** (owner, 2026-10-20: this must not be forgotten).
- **Breaking up `bot.py`.** Each feature moves into its own discord.py extension (a Cog) in its package: `recaps/discord.py`, `memory/discord.py` and so on. Each holds its commands, buttons, listeners and session hooks. `bot.py` keeps startup, the session table and the wiring.
  - **One feature per PR,** with no change in behaviour, starting with recaps.
  - **This adds no queues and no new concurrency:** it is the same process and the same event loop.
  - **One owner of session state:** the shared session state (`Table`) stays owned by one place, and features use it through small methods, never by editing each other's fields.
- **One migration per file** (`core/src/dmbot/migrations/NNNN_name.py`), so parallel features stop colliding in `schema.py`'s list. The number claiming on issues stays.
- **Boundaries checked in CI** (import-linter): a feature uses another only through its public module, never its internals.
- **Order:** this starts after the current recap wave (#2096, #2112, #2118, #2113, #2105, #2106). First the migration split (mechanical), then the worker with leases and the outbox, then the extensions one by one, then the CI boundary check.

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
| 0 | Scaffolding, CI, ears ↔ core audio pipeline | ✅ Done. Live capture works (`docs/testing-history.log`) |
| 1 | **Listener**: consent by DM buttons (#33–#35), cloud speech-to-text as the default (#128), live transcript in its own channel `#dmb-transcript-<short name>` (#124), stored session transcripts anyone in the server can download (as heard now; cleaned added in Phase 2b), with download buttons sent privately to the DM and recorded players when DMbot stops (#41, #125), transcript format with speaker labels, end-of-session summary (#109) | No AI yet; useful on its own |
| 1.5 | **Campaigns and setup**: `/dmbot start · stop · help`, first-time guide, campaign picker, one DM screen per campaign, voice-channel picker, target/fallback rulesets, optional rules, campaign export/import, bring-your-own API keys | Foundation for everything after |
| 2a | **Campaign memory (EntityBot)**: entities, aliases, relationships and the ontology in Postgres; entity resolution; names added by the DM, from characters, and from an after-session scan of the raw transcript (#126) | Built first: the Cleaner and every later helper read it |
| 2b | **Transcript Cleaner** (live name fixing, off-topic hiding, #127), off-topic filter (#52), speaker tagging | Every helper depends on clean, labeled input |
| 3 | **Rules advisor + house rules**: alerts with ✅ Agree / 🙈 Ignore / ⚖️ Override, house rules by voice with DM approval, `/dmbot houserules` | Uses the rules hierarchy below |
| 4 | **TimeBot**: game clock, effect durations, rests, dawn/noon/dusk, split-party clocks | |
| 5 | **NPC tracker** (remembers NPCs, relationships, factions between sessions), then **PlotBot** (DM-confirmed story events) | Read the campaign memory; use **confirmed** entities and relationships only. *Split (decided 2026-10-06, docs/STORY_MEMORY.md):* 5a claims, state facts and the first continuity warnings (*slice 1 built, #1267: state facts, one-of-a-kind items with `carries`, nesting and loop checks, state-gated actions; code only, flags go to the after-session report; the extractor, linker and live warnings are the next slices*) · 5b NPC tracker, who knows what, hierarchical reputations · 5c PlotBot: threads, promises, summaries, pre-session note · 5d the Shared story switch |
| 6 | **DM sidebar**: quick, very short AI answers for the DM, by voice memo or by asking out loud at the table; tagged `[DM Sidebar]` in the raw transcript only (owner, 2026-10-09). **Since 2026-10-14 anyone at the table may ask** ("Hey DMbot…" or typed in `dmb-chat`), and table questions are answered in the campaign's chat channel (see "Table questions") | No install needed; being built early, after the rules lookup |
| 7 | **Google Drive** (writing the house-rules file of record directly) and **character data** from D&D Beyond links | Reading a linked house-rules file and the download come first (2026-10-09) |
| later | Paid service billing (owner's key, per-server metering); D&D Beyond companion extension; optional DM hotkey helper | |

## Feature notes

**Delivery to the DM.** Discord has no pop-ups. Alerts go to the campaign's private DM
screen channel (only the DM can see it) and optionally to DMs. When a button, menu, form
or command breaks, DMbot tells the person who used it, privately, instead of failing
silently or leaving "thinking…" up for good (#537, #595). DMbot's replies to slash
commands are only visible to the person who used them.

**Commands (decided 2026-10-04).** Five entry points; everything else is buttons.

| Command | What it does |
|---|---|
| `/dmbot start` | Start listening: pick the campaign and the voice channel (both default to last time). Replaces `/table join` |
| `/dmbot stop` | Stop listening and close the session. Replaces `/table leave` |
| `/dmbot backup` · `/dmbot restore` | Download a complete copy of a campaign, secrets included (anyone in the server, so a campaign is never lost if its DM disappears; decided 2026-10-06, #229; whoever restores a copy becomes its DM); bring one back from a copy (restore needs a file, which only a command can take) |
| `/dmbot help` | A short, friendly guide with buttons |
| `/dmbot houserules` | List, add, edit, and remove house rules for the current campaign (built 2026-10-09, #865: anyone in the server reads the list; only the campaign's DMs get Add, Edit and Remove) |
| `/dmbot optionalrules` | Turn optional rules (e.g. Xanathar's, Tasha's) on or off for the current campaign (built in #49: a catalog of rule names, one-line summaries and their books, never book text; each change noted in the DM screen) |
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
The backup file is compressed JSON (`.dmbot.json.gz`, format version 2, #164): text
shrinks about 2.5 times, so a campaign of up to about 25 MB of JSON downloads as one
Discord file (10 MB). Restore still reads older plain `.dmbot.json` files, and refuses
a file that would unpack past 25 MB. DMbot never hands out a copy it couldn't restore:
a campaign over either limit gets "too big to download as one file. Nothing was lost"
instead (a split or streamed backup can come later if campaigns grow that big).

**Rules sources.** Baseline is the SRD 5.2 (CC-BY-4.0, attribution required). Owned
sourcebook text is never bulk-copied to the server; only short, relevant excerpts are
sent per query, **unless the DM shares the book** (decided 2026-10-06, IP rule in
CLAUDE.md): a DM can share a rulebook or supplement for their campaign and confirm they
have the right to use it (the warning says DMbot doesn't check; who and when are
recorded). DMbot may then copy chunks of it into that campaign's rules data and AI
prompts, so rules alerts can cite it. It stays in that one campaign, never in the
repository, and the DM can remove it. It follows the rules edition and precedence below:
a shared book is matched to its edition (2024, 2014 or other) and is a sourcebook in the
target or fallback ruleset, not a house rule.
**Built, the edition of a shared book (2026-10-16, #1563):** when a DM loads a file and says it is
from a published book, one more question follows "I have the right": **Which rules is this book
for?** (2024 book, 2014 book, Not sure / add-on). The answer is kept with each entry
(`homebrew_entries.book_edition`, migration 0104; an entry loaded before the question reads as
"other"). In `/dmbot rule` the order is: house rules, the campaign's own homebrew (not from a
book), followed packs, then the free rules and the book's entries together, each in its edition's
place: an entry for the campaign's target edition answers ahead of the free rules' entry of the
same name (a tie goes to the DM's copy), one for the fallback edition answers only when the
fallback is where the free rules have the name, and "other" sits between (after the target, before
the fallback). A 2014 book entry is tagged `[Legacy 2014]` while the campaign plays the newer rules.
Its source line names the campaign's copy ("Frostmaiden's shared book (2014): Fireball"), never the
book's title, which DMbot does not keep. A followed pack's entry beats a book entry of the same name.
A book entry still never goes into a pack. *Built, alerts and the sidebar follow the same order
(2026-10-16, #1569):* a book entry that ranks behind the free rules' entry of the same name steps
aside there too (the free rules' card or entry answers), and one that answers carries `[Shared book]`
and, for a 2014 book under newer rules, `[Legacy 2014]`.

*Built, the rules index (2026-10-09, #866):* `dmbot.rules.index` looks a spell or a
condition up by name. The data is the **SRD 5.2.1** (CC-BY-4.0), taken from Wizards of
the Coast's own PDF by `python -m dmbot.devtools.srd` (the PDF is downloaded, never kept
in the repository; the files record its SHA-256): all 339 spells and the 15 conditions, in
`core/src/dmbot/rules/data/srd52/`, with `ATTRIBUTION.md` (the statement the SRD asks for,
and the changes made) and the same statement in the README. Nothing from any other book
is in the repository. Each entry has its source, section and page, so an alert can cite
it ("SRD 5.2.1, Spell Descriptions, p. 131"). A name is matched without case,
punctuation or apostrophes, and the 19 spells the 2024 books renamed are also found under
their 2014 names (`rules/aliases.py`, each with a comment; Feeblemind and Branding Smite
included). `lookup(name, target, fallback)` tries the target ruleset, then the fallback;
a fallback hit is tagged (`[Legacy 2014]` for 2014 content). An older entry is used only
when no newer one matches any name. The 2014 SRD 5.1 (also CC-BY-4.0) is loaded
the same way, as the legacy fallback: all 319 of its spells and its 15 conditions in
`rules/data/srd51/`, with its own `ATTRIBUTION.md` (#873). Only Feeblemind and Branding
Smite are in 5.1 and not in 5.2.1, and they are found as their renamed 2024 spells first.
The 5.1 PDF's text layer is rougher (cut words, a few lost words): the tool repairs cut
words only with words the SRD itself uses, never edits by hand, and `ATTRIBUTION.md` says
that one lost word (a stray letter in Animal Friendship, named there) remains.

*Built, the Rules Glossary in the index (2026-10-18, #1884):* the SRD 5.2.1's Rules Definitions (`rules.json`, kind `"rule"`, section "Rules Glossary", 140 entries: Cover, the Dash action, Long Rest, ...; the 15 conditions stay kind `condition`) are in the index, and the DM sidebar cites them like any other entry ("SRD 5.2.1, Rules Glossary, p. 180"). A glossary entry is found only when its kind is asked for: its names are everyday words ("Action", "Damage"), so the live rules cards, the `/rule` name list and "did you mean" never see them. The sidebar looks them up only when the question names no spell, condition or creature (at most three, longest names first), so an everyday word beside a named spell never adds noise; house rules still come before them. The `/rule` card does not find them yet (a follow-up). *Built, class features in the index (2026-10-18, #1900):* the SRD 5.2.1's Classes chapter is in `features.json` (kind `"feature"`, 232 entries: each `Level N: Name` with its class, level and, for a subclass's, its subclass; the class tables, spell lists, option lists and subclass introductions are left out). A feature is found only when asked for, like the glossary's entries, and a name several classes share ("Evasion", "Extra Attack") keeps one entry per class. The sidebar adds features beside the glossary's, only when the question names no spell, condition or creature; a class named in the question ("rogue evasion") picks that class's. `/dmbot rule` finds them after the glossary's names and the list shows "Evasion (feature: Rogue 7)". *Built, the 2014 class features (2026-10-18, #1903):* the SRD 5.1's Classes chapter is in `srd51/features.json` the same way (179 entries: 123 class features and 56 of the twelve subclasses; the level of a class feature is the class table's, of a subclass feature its first sentence's). They are tagged `[Legacy 2014]` and used only where the target ruleset has no feature of that name for that class: "Brutal Critical" is found, "Rage" is the 2024 one, and the Monk's "Timeless Body" (the 2024 Monk has none) is found beside the Druid's. The name list never offers the 2014 version of what the 2024 rules also have. Species, feats and the rest of the SRD are later issues. *Built, magic items in the index (2026-10-18, #1912):* the SRD 5.2.1's Magic Items A–Z (`items.json`, kind `"item"`, section "Magic Items", 258 items, each with its category, rarity as printed and attunement and for whom) are in the index; the sidebar cites them like the class features ("SRD 5.2.1, Magic Items, p. 212"), and `/dmbot rule` finds "Bag of Holding" and lists it as "Bag of Holding (item: Wondrous Item, uncommon)". They are found only when asked for (their names are everyday words: "Rope of Climbing"), after features, and the sidebar looks for them only when the question names no spell, condition or creature. The tables and the stat blocks printed inside an item are left out (named in ATTRIBUTION.md); the 2014 items are a later issue. *Built, feats, species and backgrounds in the index (2026-10-18, #1913):* the SRD 5.2.1's Feats (`feats.json`, kind `"feat"`, 17 feats with their category and prerequisite) and Character Origins (`origins.json`, kinds `"species"` (9, with creature type, size and speed) and `"background"` (4, with ability scores, the feat they give, skills, tool and equipment)) are in the index, found only when asked for like the class features and items. The sidebar cites a feat as "SRD 5.2.1, Feats, p. 87"; a background comes with the feat it gives, cited as the feat itself. A one-word feat or background ("Alert", "Sage") or "Human" counts only when the question also says feat, species, race or background; other species names ("Goliath") and longer names ("Magic Initiate") need nothing. `/dmbot rule` lists them as "Alert (feat: Origin)", "Goliath (species)" and "Sage (background)", after the features and the items. The tables inside the species (Draconic Ancestors, Elven Lineages, Fiendish Legacies) are left out; the 2014 races, backgrounds and its one feat are a later issue. *Built, weapons, armor and weapon properties in the index (2026-10-18, #1919):* the SRD 5.2.1's Weapons and Armor tables and the property definitions (`equipment.json`: kinds `"weapon"` (38) and `"armor"` (12 and the Shield), and 18 `"rule"` entries of the section "Equipment", tagged Weapon Property or Mastery Property) are in the index, found only when asked for. The sidebar cites a weapon with its row as facts ("Martial Melee, 1d8 Slashing, Versatile (1d10), mastery Sap"); a one-word weapon or armor ("Club", "Shield") and the everyday property names ("Light", "Heavy", "Slow") count only when the question says damage, weapon, armor, AC, mastery or property, and "Shield" with "AC" or "damage" gives the Shield spell and the Shield armor, each cited (only a word that gear alone has, such as armor or weapon, drops the spell, and "spell" or "cast" keeps the spell alone). `/dmbot rule` lists "Longsword (weapon: Martial Melee)" and "Plate Armor (armor: Heavy)", and any name two kinds share is listed as "Shield (armor)", "Light (rule: Weapon Property)" so each can be picked. *Built, tools, adventuring gear, mounts and vehicles in the index (2026-10-18, #1927):* `equipment.json` also holds kind `"tool"` (25: ability, weight, price and the Utilize, Craft and Variants lines as printed) and kind `"gear"` (101: the 81 adventuring gear entries with their words, price and weight, 8 mounts with their carrying capacity, and 12 vehicles; `details.group` says "Adventuring Gear", "Mount" or "Vehicle"). They are found only when asked for. The sidebar cites them like the weapons: a one-word name ("Rope", "Torch", "Oil", "Camel") counts only with "gear", "tool", "kit", "cost", "price", "weight" or "buy" in the question, a longer one ("Healer's Kit", "Thieves' Tools") needs nothing, a mount gives way to its price when the question asks one ("what does a camel cost") and stays the creature otherwise, and "Horse, Riding" is also found as "riding horse". `/dmbot rule` lists "Healer's Kit (gear: 5 GP)" and "Thieves' Tools (tool: Dexterity)"; a gear entry and a magic item of one name ("Spell Scroll") are each picked from the list. Left out and named in ATTRIBUTION.md: the saddles, feed and stabling, the large-vehicle notes, the lifestyle expenses, hirelings, services and the Crafting section. *Built, the 2014 magic items (2026-10-18, #1920):* the SRD 5.1's Magic Items chapter is in `srd51/items.json` (kind `"item"`, 239 items, the same fields as the 5.2.1's), tagged `[Legacy 2014]` and used only where the target ruleset has no item of that name: "Bag of Holding" in a 2024 campaign is the 2024 item alone, "Arrow of Slaying" (the 5.2.1 calls it Ammunition of Slaying) is found as the older item, and in a 2014 campaign the 5.1 item comes first. An item keeps its own edition's rarity (the 5.1 Potion of Invisibility is very rare, the 5.2.1's rare). No new index kind; the sidebar and `/dmbot rule` carry the tag as they do for any older entry. The tables and stat blocks inside an item, and the chapter's introduction, sentient items and artifacts, are left out (named in `srd51/ATTRIBUTION.md`). *Built, the 2014 races, the Acolyte and the Grappler (2026-10-18, #1928):* the SRD 5.1's nine races (`srd51/origins.json`, kind `"species"`: Dwarf, Elf, Halfling, Human, Dragonborn, Gnome, Half-Elf, Half-Orc, Tiefling, each with its subraces kept inside its text), its one background (Acolyte) and its one feat (`srd51/feats.json`, Grappler, with no category) are in the index as the `[Legacy 2014]` fallback, per name: "Dwarf" in a 2024 campaign is the 2024 species alone, "Half-Orc" and "Half-Elf" (which the 2024 SRD dropped) are found tagged, and a 2014-target campaign reads its own first. "Half-Orc" is a two-word name and needs no cue word, and the creature "Orc" gives way to it. The Draconic Ancestry table and the Acolyte's tables of personal characteristics are left out (named in `srd51/ATTRIBUTION.md`). *Built, the 2014 equipment (2026-10-18, #1931):* the SRD 5.1's Equipment chapter is in `srd51/equipment.json` in the 5.2.1 file's kinds (37 weapons with no mastery, 13 armors, 11 weapon properties, 86 gear, 25 tools, 8 mounts and 11 vehicles), tagged `[Legacy 2014]` and used per name only where the target has nothing: "Longsword" in a 2024 campaign is the 2024 weapon alone, the Net or an Abacus are found as the older entries, and a 2014-target campaign reads its own first (its card has no mastery line); an armor is also found with "armor" after its name ("plate armor" is the 5.1's "Plate"). The cue words are unchanged. The Equipment Packs, the Container Capacity table, tack, trade goods, lifestyle expenses and services are left out (named in `srd51/ATTRIBUTION.md`). *Built, the SRD 5.1's core rules as the 2014 rules text (2026-10-18, #1943):* `srd51/rules.json` (kind `"rule"`, section "Rules", 164 entries from the Using Ability Scores, Adventuring, Combat and Spellcasting chapters: the actions, Grappling, Cover, Short and Long Rest, Concentration, Falling, Suffocating, Vision and Light, Two-Weapon Fighting ...) is in the index. The entries keep the 2024 glossary's name where the rule is the same ("Dash", "Cover", "Hit Points"), so the per-name fallback pairs them: a 2024 campaign gets the 2024 entry alone and finds a 5.1-only rule tagged `[Legacy 2014]`, a 2014 campaign gets the 5.1 rule first, and the name list never offers both (except the weapon property Range and the 2014 combat rule \"Range (Attacks)\", which are different rules). A 2014 rule of a name the target has as another kind (the 2024 feat Two-Weapon Fighting) gives way to it, and a single-word 2014-only title (\"Time\", \"Water\", \"Food\") is not sent to the sidebar from a 2024 campaign, only found by `/dmbot rule`. A 5.1 title the glossary spells another way ("Suffocating", "Use an Object") is an alias to the 2024 entry (`aliases.RULE_ALIASES`); a title the PDF prints twice is named for its place ("Hit Points (Constitution)"). The chapters' introductions and four tables are left out and named in `srd51/ATTRIBUTION.md`. *Built, mood tags for the narrator's voice (2026-10-18, #1983):* the recap's `《emotion》` cues reach the `eleven_v3` voice as delivery tags from a fixed table (`script.EMOTION_VOICE`: tense/urgent/battle `[urgently]`, somber/sorrowful/melancholy `[sadly]`, hopeful/tender `[softly]`, triumphant/defiant `[confidently]`, mysterious/eerie/ominous/dread `[quietly]`, playful `[playfully]`, wonder `[in awe]`, calm and grim none), once where the mood changes, never a word a model or player wrote. `DMBOT_RECAP_VOICE_TAGS=0` sends only whisper, sigh and pauses so two narrations can be compared by ear; `recap_run --audio` prints which was used. The tags are the starting map from the issue, not checked against ElevenLabs' tag list from the build machine. *Built, short names find the 2024 piece (2026-10-18, #1937):* a 2024 armor is also found by its short name ("Plate Armor" is also "plate", "Leather Armor" "leather"), and six pieces of gear the 5.2.1 names shorter than the 5.1 (Costume, Flask, Jug, Mirror, Mule, Robe) by the 5.1's name, so the legacy twin is never chosen over the newer piece on a direct lookup; the sidebar still needs a cue word for the one-word names ("leather", "plate"). *Built, the Gameplay Toolbox in the index (2026-10-18, #1935):* the SRD 5.2.1's Gameplay Toolbox is in `toolbox.json` (section "Gameplay Toolbox"; kinds `"rule"` (36: travel pace and terrain, curses and magical contagions, the nine environmental effects, fear and mental stress, poison and traps; the three contagions are tagged Magical Contagion), `"poison"` (14, with type and price), `"trap"` (8, with severity and levels, trigger, duration and the levels table as lines) and `"curse"` (1, Demonic Possession)), found only when asked for. It is the DM's chapter (owner's "DM authority" rule): `Entry.dm_side` marks it, and a table question never gets any of it (a player's "how do I disarm the pit trap" is answered "That's one for your DM."); only the DM's sidebar and a DM's `/dmbot rule` use it (a player's `/dmbot rule` name list leaves it out, and typing one of its names in full answers "That's one for your DM."). A poison, trap or curse with a longer name ("Purple Worm Poison", "Hidden Pit") is found by name and beats the creature inside it; a one-word name ("Malice", "Torpor") needs "poison", "trap", "curse", "hazard" or "dc" in the question; a trap is also found as "<name> trap" and the two pits as "pit trap". `/dmbot rule` lists "Purple Worm Poison (poison: Injury)", "Hidden Pit (trap)" and "Demonic Possession (curse)". A question is matched by name only: "how far can we travel in a day" names no entry. Left out and named in ATTRIBUTION.md: the background-building advice and everything from Combat Encounters on (encounter building and the Magic Items section: crafting, sentient items and their random tables), which are later issues. *Built, the SRD 5.1's game master chapters (2026-10-18, #1944):* `srd51/toolbox.json` (section "Gameplay Toolbox", so every entry is the DM's like the 5.2.1's: `dm_side`, never in a table question, a player's card or name list) holds 17 rules (traps in play, trap effects with the severity tables, diseases, madness with its three d100 tables, objects with their AC and hit point tables, poisons), 11 traps, 3 diseases (a new kind, `disease`, among `HAZARD_KINDS`) and 14 poisons. The per-name fallback applies: Hidden Pit, Spiked Pit and the poisons both SRDs have are the 2024 entry in a 2024 campaign and the 5.1's in a 2014 one, the 5.1-only Drow Poison, Simple Pit and Locking Pit are found tagged `[Legacy 2014]`, and the 5.1's renamed sample traps are aliases (Poison Darts, Rolling Sphere ...). Cackle Fever, Sewer Plague and Sight Rot are contagions in the 5.2.1, so a 2024 campaign gets those and a 2014 one the 5.1 disease. The Credits page names the new kinds (Poisons, Traps, Curses, Diseases). *Built, the golden questions (2026-10-18, #1952):* `core/tests/test_rules_golden.py` runs about 120 questions from `core/tests/golden/rules.json` through the real index and pins what each cites, in both editions, for the table and for the DM; a change to any cue list that moves a citation fails there. It found two things, both fixed: "dwarf traits" cited nothing ("traits" now cues a species) and "half-orc traits" must still give the Half-Orc, not the Orc. *Built, the index budgets (2026-10-18, #1953):* `python -m dmbot.devtools.index_stats` prints the index's build time (median of 5), peak memory (tracemalloc), entries and names per edition and kind, and the time of a sidebar question over the golden questions; `tests/test_index_budget.py` holds it to 2 s to build, 5 ms a question and 150 MB (measured today: 40 ms, 6.7 MB, 0.6 ms), measuring once more before a miss fails. `index.srd()` is the one cached index every caller shares; `build_srd()` is the uncached builder for measuring only. *Built, the two golden gaps closed (2026-10-18, #1956):* the singular of a rule title of two words or more is another name for it ("opportunity attack" finds Opportunity Attacks, `index.rule_keys`), and a spell with an everyday name (Light, Message, Sleep, Command, Fly) is cited only when the question says cast, spell or cantrip (`EVERYDAY_SPELLS`), so "they light a torch" cites nothing, or when the question is about the word itself in a fixed frame (#1989: "how does command work", "what does sleep do", "how long does fly last", "the range of message", "sleep level", "can sleep affect undead"; `asked_about`). The golden suite has no `known_gap` left. *Built, Combat Encounters in the index (2026-10-19, #2000):* the SRD 5.2.1's Combat Encounters section is in `toolbox.json` as 12 more DM-side `"rule"` entries tagged Combat Encounters (difficulty, the three steps, troubleshooting and the adjustments), with the XP Budget per Character table as one line per level ("Level 3: Low 150, Moderate 225, High 400 XP per character") in Step 2's text. They are found only when asked for: "encounter", "xp", "budget", "difficulty" or "deadly", a fight with a hardness or a party word, or "CR" with a fight or party word ("what's a goblin's CR" stays the creature). The sidebar cites the entry with only the lines for the levels asked and does no arithmetic; a table question gets "That's one for your DM.". The section prints no CR-to-XP table (a creature's XP is on its stat block). *Built, the rules about magic items in the index (2026-10-19, #2001):* the SRD 5.2.1's Magic Items section (categories, rarity and values, activating, charges, cursed items, resilience, crafting and the sentient items) is in `toolbox.json` as 39 more `"rule"` entries tagged Magic Items, with its four small tables as one line per row ("Uncommon: 400 GP", "Rare: 50 days, 2,000 GP"). The 28 that players live by (attunement prerequisites, rarity and value, activating, crafting) are cited in the section "Magic Items" and are for the table too; the 11 that are advice to the GM (the sentient items, items made for specific creatures, unusual anatomy, paired items) stay in the Gameplay Toolbox, so a table question gets "That's one for your DM.". They are found only when asked for: a one-word name needs "item", "attune", "charges", "cursed", "craft", "sentient" or "rarity" in the question, a longer one does not; "attune" finds the glossary's Attunement, "can I attune to a Bag of Holding" gives the rule and the item, each cited, and rarity with a price word gives the value lines. The tables of random results (Potion Miscibility, the sentient item traits) are left out. *Built, "What I looked at" on the DM sidebar (2026-10-18, #1957):* under a rules answer sent to the DM privately (never a table question, a refusal or an off-topic reply) is a 📖 What I looked at button. Pressing it shows, privately and only to the DM who asked, one line for each house rule (first, labelled) and entry the answer was built from (`Shield (armor) · SRD 5.2.1, p. 93`; "DM only" for the Gameplay Toolbox; `Answer.looked_at`, taken from the answer's own `context.build`, never looked up again), each entry a button that opens its `/dmbot rule` card by the same "Name (kind)" value. The lists are kept in memory (`sidebar/looked.py`, the latest 300 answers); a restart or an old answer says so in plain words and nothing is written to the database.

Creatures are in the index too (#900): the 330 stat blocks of the 5.2.1's Monsters A-Z and
Animals sections and the 317 of the 5.1's Monsters pages and its two creature appendices,
as `monsters.json` next to the spells, each with its size, type, alignment, armor class, hit
points and dice, speed, ability scores, saves, skills, resistances, vulnerabilities,
immunities, senses, languages, challenge and XP (and, in the 5.2.1, initiative, gear and
proficiency bonus), and its traits and actions as printed, one paragraph each. A creature is
found by its name, by the bracket-less name ("Gnome, Deep"), the word in the bracket
("Svirfneblin"), the comma turned round ("Deep Gnome") and with a "(Legacy)" or other
bracket left off; 27 older names are aliased to the creature the 2025 books renamed (Goblin
is now Goblin Warrior), and the seven 5.1 creatures with no 5.2.1 one (Duergar, Drow, Deep
Gnome, Lizardfolk, Orc, Half-Red Dragon Veteran, Succubus/Incubus) are `[Legacy 2014]`
entries.

**Built: rules lookup (#908).** The first use of the index: a campaign's DM presses
**📖 Look up a rule** on the ⚙️ Settings card (a form with one box) or types `/dmbot rule name:`
(names open as they type, newest rules first) and gets a private card: the name, a short facts
line (a spell's level, school, casting time, range, components and duration; a creature's
size and type, AC, HP, speed and CR), the text as printed, and its source with
`[Legacy 2014]` when it came from the fallback ("Goblin is now called Goblin Warrior" when
the DM typed an older name). Text past Discord's limit is split at paragraphs or sentences,
never cut, with a **Read the rest** button. No match: "couldn't find that in the free rules
(SRD)", up to five close names as buttons, nothing picked for the DM. Two decisions:
**anyone in the server may look a rule up with `/dmbot rule`** (Supervisor, 2026-10-16, #1562; it
was the campaign's DMs only from 2026-10-09). The answer is private, never in a channel or a
transcript. A player gets the same card as the DM but with house rules and the campaign's homebrew
that is on (an entry turned off is not found, "Homebrew (part 2)"): no DM-screen content, no
secret names, no rulings, no AI and no cost; the card ends with "Your DM decides what applies at
your table."; one lookup per person every 3 seconds ("Try again in a few seconds."); a player's card has no "you decide" line of the DM's;
the 📖 Look up a rule button stays on the DM's Settings card. A person who runs a campaign looks
in the campaigns they run, as before; anyone else in the campaigns of the server. And
**precedence is shown, not decided**: a house rule of that campaign (and no other's) that
names the thing comes first as "🏠 House rule 12: …", then the book, and the DM decides what
applies. Not built: the AI. Shared rulebooks: see "Rules sources" (the edition rule, #1563).

**Built: rules cards from the table (#931), the first rules alert.** A campaign setting,
**🃏 Rules cards**, in ⚙️ Settings (**off by default**; only the campaign's DMs change it; it
travels in a backup with the campaign's other settings). When on, a live session looks at
each cleaned line of people who agreed and, when a spell, condition or creature of the
campaign's rulesets is named (whole names only, plurals count, the longest name first), puts
a short card on the DM screen only: never in the transcript, never to players; it follows the
DM screen's visibility like every other post there. The card is the lookup card's top half: a
house rule that names it first ("🏠 House rule 12: …"), the name, the facts line, the source
with its tag, and what was heard ("Heard: “casts Fireball”", the confidence every rules alert
gives), with **✅ Got it**, **🙈 Ignore** (no more cards for that name this session),
**⚖️ Override** (the Add a house rule form with "Instead of" filled in; the answer is private)
and **📖 Read it all** (the full lookup card, privately). Names that are everyday words
(Light, Fly, Shield, Prone, Nightmare, Tough Boss, …) only count after "cast", "casts" or
"casting" right before a spell, or "is", "are", "was" or "were" right before a condition ("the
goblin is grappled"); an everyday creature (Wolf, Bat, Guard, Nightmare, …) never gets a card
from the table (the DM looks it up with 📖); a name the campaign itself uses for a character, NPC
or place (a PC called Sprite) is skipped; the list is in
`dmbot.rules.spotter`, and a test checks every word on it is a name in the index. Not noisy:
one card for each name each session, one card a minute (the rest are dropped, not queued), and
nothing after `/dmbot stop`. The DM screen's level (Quiet, Normal, Chatty) does not govern
it: turning the setting on is the DM's own choice and wins, even on Quiet. Names only: no AI, no cost, so no `can_use_ai` check. It never
decides: the card says what the free rules say. Not built: AI checks of what was ruled. House
rules proposed by voice are built (#953: a DM-screen offer with Edit before saving when the DM says
one at the table) and players' lookups since 2026-10-16 (#1562, #1564: see "Built: rules lookup").

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
which optional rules to use, **default: on**. `/dmbot optionalrules` toggles them any time. House
rules can still override them.

**Xanathar's and Tasha's are not legacy books (web, 2026-10-07, #49).** Unlike the 2014
Player's Handbook, Monster Manual and Dungeon Master's Guide, they are not marked legacy
as a whole; only their parts that the 2024 core rules reprint or replace are:
- A subclass, spell, feat or rule the 2024 books reprint or update is replaced: use the 2024
  version. The old one is used only with a 2014 target, or as the fallback, and is then
  tagged `[Legacy 2014]` like any fallback content.
- Everything the 2024 books don't reprint or update stays usable with a 2024 target,
  untagged: unique rules, downtime activities, magic tattoos, subclasses without a 2024
  version. The optional-rules catalog (`core/src/dmbot/rules/optional.py`) follows this:
  a rule is listed for 2024 unless the 2024 books cover it (knots, tools, custom origins,
  Tasha's class features). A rule nobody has checked against the 2024 books stays listed
  (offering it does no harm; the DM decides), and the "not listed" note is built from
  the catalog.

Lookups must match renamed content (e.g. 2024 dropped many creator names from spell
titles), so the rules index keys each entry by a normalized name plus known aliases,
and a legacy entry is used only when no newer entry matches any alias. If a future
edition supersedes 2024, it becomes "newest" and 2024 content gets its own legacy tag.

**House rules (decided 2026-10-04; the file of record added 2026-10-09).** Each campaign
has its own. **The house rules live where the table can read them offline** (owner,
2026-10-09): a house rule only DMbot can see isn't useful, and most house-rule talk happens
away from Discord. So the DM keeps **one house-rules file of record** (a Google Doc, or any
document with a share link), and DMbot keeps its own copy in step with it:
- **Linked file:** the DM gives DMbot the file's share link once (⚙️ Settings or
  `/dmbot houserules`). **Every time a session starts, DMbot reads the file** (through the
  guarded link reader `dmbot.fetch`) and compares it with its copy. Changes made offline
  (added, changed or removed rules) are shown to the DM on the DM screen as one short list
  with **Accept all / Review / Ignore**. Nothing changes until the DM accepts. If the file
  can't be read, DMbot says so once and carries on with its copy.
- **Changes made in Discord** (Override, by voice, by typing, `/dmbot houserules`): if
  DMbot can write the file (Phase 7: a Google Doc it created, with Google's narrowest
  `drive.file` access), it writes the change there. **If it can't, it offers the DM the
  updated house-rules file to download** right away, with one line saying "Put this in
  your house-rules file so everyone can see it", and reminds them at the next start if the
  linked file still doesn't have it.
- **One plain format** both ways, readable by people: one rule per numbered line,
  `12. <the rule>` with an optional `(instead of: <book rule>)`. Rule numbers stay the
  campaign's own (below), so a rule edited offline keeps its number.
- With no linked file, the download after each change is the record the DM keeps.
DMbot's copy (the database) is what alerts and lookups read during a game. Each rule records the
rule, the book rule it supersedes, and the scenario that created it (session, date, what
happened). Ways in:
1. **From an alert:** ⚖️ Override → "Save as a house rule?"
2. **By voice:** DMbot listens for the DM declaring a house rule, and compares it against
   the existing hierarchy. **Every voice-proposed rule needs the DM's approval** in the DM
   screen: new rules show [Save] [Edit] [Cancel]; conflicts with an existing house rule
   show both and ask which wins.
3. **By typing** in the DM screen ("House rule: …"), with the same approval.
4. **`/dmbot houserules`:** list with Edit and Remove.

**Two house rules that disagree** (owner, 2026-10-21, with the example "drinking a potion is a bonus
action" against "drinking a potion is an item interaction"; details Supervisor's). The DM decides,
always; DMbot never picks one.
- **Today's gap:** a clash is found only when both rules name the same spell, condition or creature
  (the name matcher). "Drinking a potion" names none, so both would be saved, and an answer could
  quote either.
- **When a rule is saved** (by voice, typed, Add or Edit, or accepted from the linked file): one
  fast-tier call compares it with the campaign's own house rules and the set it follows, on topic and
  not only by name. If it disagrees with one, the DM sees both and picks **Replace rule N**, **Keep
  both, this one wins**, **Keep both, rule N wins** or **Cancel**. "Wins" is stored on the pair, so
  later answers know it. Nothing is saved until the DM picks.
- **When an answer finds two that disagree** with no "wins" recorded (older rules, or a followed
  set), the DM's sidebar quotes both with their numbers and asks "Which one applies?", with buttons
  for each that record the answer. It never chooses. A player's table question gets "That's one for
  your DM.", and the DM is told privately.
- **Built in slices (#2284, CloudDev, 2026-10-21):** the save-time check, the stored "wins" and
  backups first; the answer-time question after. At save, a rule accepted from the linked file that
  disagrees is held back with a line saying so (the DM adds it by hand and picks), and the rest of the
  file is added. A check that cannot run never blocks a save: the rule is saved and the DM is told it
  was not checked. Editing or removing a rule forgets what was decided about it, in the same
  transaction.
- **Between sources** the order is unchanged: the campaign's own house rules, then the set it
  follows, then homebrew, then the book rules. The check above is only for rules at the same level.
- **Cost:** one fast call per saved rule. A file accept with many rules checks them in one call.

**Seeing where house rules come from** (owner, 2026-10-21). The House rules menu gets **📂 Sources**,
a private list with one line for each place the campaign's rules came from:
- the **linked file of record**, with **🔗 Open the file** (its share link) and **📥 Download**;
- each **uploaded file**, by its name and date, with **📥 Download**;
- rules **added in Discord** (by voice, typed, from an alert), as one line with **📥 Download**;
- the **set the campaign follows**, if any, by its name and publisher, with **📥 Download**.

Download gives that source's rules in the plain numbered format. The words come from DMbot's
copy; an uploaded file itself is not kept. To know which source a rule came from, each rule records
it (`source`: linked, upload with its file name, voice, typed, alert, or the followed set). Rules
from before are "added in Discord". The Homebrew menu gets the same **📂 Sources** list for its
entries: typed, each upload, each site linked, and each followed pack.

Only the DM can declare or change a house rule. A player may suggest one; it becomes a
proposal when the DM clearly agrees out loud, then goes through the same approval.

*Built, part 1: the store and `/dmbot houserules` (2026-10-09, #865):* no AI, voice or
alerts yet; those build on this.
- **Table `house_rules`** (migration 0031), per campaign, with row-level security like
  every campaign table: the rule (at most 500 characters), `supersedes` (the book rule it
  replaces, free text, optional), `scenario` (what happened, optional), `session_id`
  (optional; nothing sets it yet), who created it, and when it was created and last
  changed. At most 200 per campaign. The website's role has no grant on it. It goes in
  backups (a section that validates every field of the untrusted file) and is deleted with
  the campaign. A backup never carries `session_id`, which means nothing in another server.
- **A rule's number is its own, for good (decided 2026-10-09, Supervisor):** alerts cite
  "house rule 12", so a number never changes meaning. It is the campaign's next number
  when the rule is made (`campaigns.house_rules_made` counts them, under the campaign's
  lock), it is never used again, even after the rule is removed, and backups carry both
  the numbers and the count of numbers used (so a copy goes on where the campaign was,
  even if its newest rules were removed; a backup made without the count goes on after its
  highest number). Restoring over a campaign keeps its own count if that is higher. The list shows each rule's own number, newest (highest) first,
  so it has gaps after a removal, with one line saying each rule keeps its number; the
  buttons and the menu use that number ("Edit 12").
- **Two DMs, one rule:** each rule counts its changes (`version`). Edit and Remove say
  which version the DM was shown; if another DM changed the rule meanwhile, nothing is
  saved or removed, and the DM is told (Edit gives their words back to paste again).
- **Who may change them:** only the campaign's DMs, not even server managers: the store
  checks it in the same transaction as the change. Anyone in the server may list them,
  as they may read transcripts.
- **`/dmbot houserules`** answers privately, newest first. It opens the campaign being
  played, else the one campaign the person is a DM of, else the server's only one, else
  asks which. A DM gets **Add a house rule** (a form: "The rule" and "Instead of
  (optional)", shown in the list as "(instead of: …)"), and Edit and Remove for the rules
  on the page shown: buttons when the page shows four or fewer, a menu above that, and pages
  (what fits in one message, at most ten rules). The DM stays on their page after a change.
  Remove asks first, shows the rule, and says it can't be undone; after it, the words are
  given back whole, to paste into **Add a house rule** if it was a mistake. A refused form
  gives both boxes back, whole and as typed. Nothing is posted to the DM screen (the list
  isn't DM-screen content).
- **Replace means replace:** restoring a backup over a campaign replaces its house rules
  with the backup's, as it does everything else; a backup made before this has none.
- **Wording:** "house rule" only: no "precedence" or "hierarchy" in anything users read.

*Built, part 2: by voice, no AI (2026-10-09, #953):*
- **What starts a proposal:** a line from one of the campaign's DMs (nobody else's) that
  begins with "house rule" (then `:`, `,` or `-`), "new house rule", "for this table"
  (then `:`, `,` or `-`), or "our rule is" / "our house rule is". The words after it, at
  least two, are the rule (cut at 500 characters on a word, and the proposal says so).
  "The house rules say…" and questions start nothing. No AI reads the line.
- **The proposal** goes to `#dm-screen` only, never to a channel, never into the
  transcript: 🏠 **New house rule?** with the words, and **Save / Edit / Cancel**. Nothing
  is saved until a DM presses Save. Edit opens the same form as Add, with the rule filled
  in. A saved rule records `scenario` ("Said at the table, <date>") and `session_id`.
- **Simple conflicts:** the rule-card name matcher finds this campaign's rules that name
  the same spell, condition or creature; the proposal shows up to two and says what each
  button does. With one clash: **Save as new rule** (keeps both), **Replace rule N** (the
  old rule keeps its number and its "instead of"; the note says what it used to say) or
  Cancel. With several: Save as new rule, Edit or Cancel, never Replace (it would be unclear
  which goes). A rule changed by another DM meanwhile is never replaced.
- **One save only:** a proposal is claimed before the save, so two presses (or two DMs)
  write one rule; it is given back if the save is refused. If the campaign's rules couldn't
  be read, the proposal says it didn't check for a repeat.
- **Limits:** one proposal a minute, and the same words once a session. If the post fails
  the words and the minute are given back.
- **Every press** re-checks that the person is a DM of that campaign now; a proposal is
  forgotten when the session ends or the bot restarts, and an old button says so.
- **Typed proposals (built 2026-10-09, #960):** a DM types the same phrases to DMbot in
  their private chat (the DM sidebar's way in). It is the same proposal on the DM screen,
  with the same buttons, limits (shared with what is said aloud) and conflict check, and the
  rule's "scenario" reads "Typed by the DM, <date>". It is not a sidebar question: no AI, no
  `[DM Sidebar]` line, nothing written to the transcript, and it works even while quick
  answers are switched off. The reply is short and says to press Save ("nothing is saved until
  you do"); if it can't offer one it says why (a minute apart; already offered). A message that
  starts with one of the phrases is a house rule, unless it ends in a question mark (then it
  is a question for the sidebar, as before). The
  DM must have agreed to be recorded (else they are asked), and with several running games
  they pick which one by button. The bot still doesn't read messages in servers (narrowest
  intents); the **Add a house rule** form in `/dmbot houserules` is the other typed way.

*Built, part 3: the house-rules file, first half (2026-10-10, #969):* the owner's decision is
that house rules must be readable offline, so DMbot keeps a plain-text file of record.
- **The format** (`dmbot.rules.house_file`, pure): a title line, `#` help lines, then one
  rule per line, `12. <rule> (instead of: <book rule>)`. Reading and writing go both ways
  without loss (a rule that itself contains " (instead of: " is written with square
  brackets). A line that is not a rule is reported with its line number, never guessed at;
  over 200 rules, a rule over 500 characters, a number used twice or a number too big are
  reported, not kept. `compare` says what was added, changed or removed against DMbot's copy
  by rule number, and calls the same words under another number "moved".
  Reading costs time in proportion to the file (it will be run on files fetched or
  uploaded): a file over 200,000 characters is refused unread, a line over 2,000 is refused
  unread, reading stops at the 201st rule, and at most 20 problems are listed (then a count).
- **Download after every change:** after any change a DM makes in Discord (Add, Edit,
  Remove, an Override, a rule said or typed) the DM gets the updated
  `house-rules-<campaign>.txt` as a private message: "Put this in your house-rules file so
  everyone can see it." A problem sending it never undoes the change. A **📥 Download**
  button on the `/dmbot houserules` list gives anyone in the server the same file (they may
  read the list already); it holds this campaign's house rules only.

*Built, part 3b: the linked file and the upload (2026-10-10, #969):*
- **Link a file** (the campaign's DMs only): ⚙️ Settings → **House-rules file**, the list's
  **📄 House-rules file** button, or `/dmbot houserules link:`. Stored per campaign in
  `house_rules_file` (migration 0040, deleted with the campaign, no grant for the website).
  A share link can be private, so it is never logged, never shown back (only the site it is
  on, such as docs.google.com), and not in backups. Unlink and **🔄 Check it now** are in the
  same menu.
- **At every session start** DMbot reads the linked file in the background (`dmbot.fetch`:
  Google Docs, Drive, Dropbox, OneDrive; every address checked; plain text only) and
  compares it with the campaign's rules by number. If anything differs, one short note on
  the DM screen with **Accept all**, **Review** (one at a time: Accept, Skip, or Accept the
  rest) and **Not now** (= ignore until the file changes). The note says what pressing does
  ("2 to add, 1 to change, 3 to remove"); when anything would be removed, **Review** is the
  main button and Accept all says how many removals it includes; with many removals (6 or
  more, or over half of DMbot's rules) Accept all is switched off. Nothing changes without a
  press; every press
  re-checks that the person is a DM, and the store checks again for each change (a rule
  another DM changed meanwhile is left alone and said). A new rule keeps its number from the
  file if that number was never used in this campaign; otherwise it takes the next free
  number and the DM is told. The same words under another number are not a change (DMbot
  keeps its own numbers). After accepting, the DM gets the updated file (part 3).
- **Never a mass removal by mistake:** a file with no readable rule is not compared (it would
  offer to remove every rule); the DM is told instead. A file that can't be read gets one
  short note and the session goes on with DMbot's copy. "Ignore until the file changes"
  remembers which version of the file you set aside (its rules, not its title or notes). A
  file's changes are done one at a time, so one that fails does not undo the rest, and doing
  the same offer twice never adds a rule twice. Only the newest offer for a campaign works.
  A note is shown once per session start and never when the files match.
- **Numbers:** a rule from the file keeps its number only if it was never used and is within
  200 of the highest (one stray number such as a year must not use up the campaign's
  numbers); otherwise it gets the next one and the DM is told.
- **Careful with the network:** the file is read in the background (never delaying a
  session), 10 MB at most and 200,000 characters of text; comparing by hand waits a few
  seconds after the last time and never runs twice at once for a campaign.
- **📎 Upload a file** does the same comparison from an uploaded `.txt` file, shown privately
  to the DM; nothing is stored.
- The offers wait in memory (the 20 newest); after a restart an old button says it has ended.

**House rules from any file or link** (owner, 2026-10-22). The plain numbered format stays what DMbot
**writes** (the download, sharing and review copy), but DMbot **reads** the house rules a DM already has,
in whatever shape they are:
- **What it reads:** an upload of PDF, Word (.docx), Excel (.xlsx) or CSV, plain text or Markdown; or a
  link to a Google Doc, Google Sheet, Drive, Dropbox or OneDrive file, or a public web page. Everything is
  read through the guarded link reader (`dmbot.fetch`: public https only, every address checked), 10 MB
  and 200,000 characters of text at most. Text only: images and scanned pages without text are said, not
  guessed at.
- **How:** a file already in the plain format is read by code as today, with no AI. Anything else goes to
  one AI call (the careful tier) that lists the house rules it finds, each with a short **quote** from
  the file. Code keeps only rules whose quote is really in the file (DMbot never invents a rule), at most
  200, each 500 characters at most, and drops repeats of a rule the campaign already has.
- **The DM decides:** the result is the same offer as a linked file's changes today (**Accept all /
  Review / Not now**, one at a time in Review), plus the plain-format file to download so the DM can
  check it. Nothing is saved without a press, and every new rule goes through the disagreement check
  ("Which one wins?", #2284). 📂 Sources shows "from <file name>" or "from <site>" (never the full link).
- **A linked file in another shape** is read the same way at session start, **only when it has changed**
  since the last read (DMbot keeps a fingerprint of the text, not the text), so the AI call is made once
  per change, not once per session.
- **Only the DM's own rules:** the form says "Your own house rules only. For a published book, use Share
  a rulebook." A file that reads as a published book (long, page-numbered rules text with no house
  rules) gets that line instead of an offer. The file's text goes to the AI for this one read and is not
  stored; the link is stored and shown as today (site only, never logged).
- **Cost:** one AI call per file read (or per change of a linked file), counted in the campaign's usage.

**Homebrew (part 1)** (decided 2026-10-16 by Supervisor, #1520, migration 0097). A campaign stores its
own homebrew: **entries** the DM wrote, in the DM's own words (a spell, monster, item, feat, condition,
species, subclass, background or other). **A rule the DM makes up is a house rule, not homebrew** (owner, 2026-10-16, #1628): there is
no "rule" kind. A file heading `## Name (rule)` is refused for that entry alone ("Rules go in House
rules, not Homebrew: ⚙️ Settings → House rules."); a pack carries no rules (a pack made before is read
without them); an old backup's rule loads as "other". Migration 0111 moved every existing rule entry of
a campaign into its house rules (the name in front unless the text starts with it), numbered after the
rules it already had (a hidden rule moves too: nothing is hidden from players any more, "Homebrew (part 2)"); one from a shared
book (its book confirmation cannot live in a house rule), one too long for a house rule (a line of 500
characters) and any past a campaign's 200 house rules stay as homebrew of the kind "other"; open pack
suggestions of a rule were dropped, and a linked house-rules file is not rewritten. Never a published book's text: the form says "Your own creations only. For a published
book, use Share a rulebook." An entry has a name (1 to 80 characters, unique in the campaign, any case),
its words (1 to 2,000, plain text) and an optional one-line summary (160, shown in alerts). **An entry is
on or off** (part 2: the 🙈/👀 "players may know" switch is gone, nothing is hidden from players; see the
on/off line below). At most
200 per campaign; table `homebrew_entries` with the campaign scope and forced row-level security; in backups
(a `homebrew` section, validated field by field) and deleted with the campaign; never read across campaigns
or servers; only the campaign's DMs change anything (the store checks in the same transaction).
- **Turn an entry on or off, instead of hiding it** (owner, 2026-10-16, #1629, migration 0112, which also drops
  `players_may_know`: every entry is on, a hidden one included): each entry has **Turn off / Turn on** (no confirmation) and shows "(off)" in the list. Off means kept but used nowhere at the table: no lookups or alerts, no sidebar answers, no players' card or table questions, no homebrew spotter (one `usable` read filter). The file writes an `Off: yes` line and reads it back, and the backup carries it (an old backup loads with every entry on); a published pack leaves off entries out; a follower's copy can be turned off on its own and a pack update never turns it back on. The web doors show every entry with `enabled`.
- **Where an entry came from** (owner, 2026-10-16, #1633, migration 0113; no user-visible change yet): every entry records its source: **typed** (the DM who typed it), **upload** (a file; the uploader by default, or a credit: a website, whose domain needs the same approval as a link's, or a typed name) or **link** (a published page). Entries from before are typed by the DM who made them. A link entry's words and summary are read from its page, so they are not on the entry's row. `homebrew_pages` keeps **one copy per address**, with no campaign scope and no row-level security: the second exception to campaign isolation, next to shared rulesets; it holds nothing about who imported it, and the website's role has no grant on it. `site_approvals` is per campaign (isolated, deleted with the campaign, in its backups): who approved a site and when, once per site. One rule, in the store's read (`_USABLE`), decides what DMbot may use: the entry is on and, if it has a site, that site is approved in this campaign. A backup writes a link entry with its page's content (restore re-points to the page already here, else recreates it) and its sites' approvals. A link entry's words cannot be edited, and it, or an upload credited to a website, is never copied into a pack as words (packs carry them by reference later). Words in a backup file never become a page other campaigns rely on: a page recreated from a backup is `restored` (no fetch time) and is skipped by `page_for` until a fetch replaces it. A backup brings a campaign's site approvals back as they were (decided with the issue). Known costs of one copy per address: the table can tell that some campaign imported an address, and roughly when, never who or where; a page that no entry points to and no pack (followed, an Undo copy, or published) names is deleted by the daily job once it is 30 days old (#1646; one INFO line with the count, never an address).
- **Who made a loaded file** (owner, 2026-10-16, #1633): at the Load step, after "is any of this from a published book?" answered No, the DM picks **Who made this?**: **Me** (the default), **A website** (the site's domain; the right-to-use warning appears once per site in this campaign, and its approval is kept, then an entry credited to that site can be used) or **Someone else** (a typed name, up to 80 characters). The credit is kept with each new entry of the file; an entry already in the campaign keeps its own.
- **Add from a link** (owner, 2026-10-16, #1634): ⚙️ Settings → 📚 Homebrew has **🔗 Add from a link** beside ➕ Add and 📥 Load file. The DM pastes the address of ONE published page; only `dndbeyond.com` (and its `www.`) for now, kept as one constant (`rules/dndbeyond.py`); anything else gets "DMbot can only add homebrew from D&D Beyond for now…". The first link from a site in a campaign shows the right-to-use warning once ("I have the right" records the site's approval); after that nothing is asked. The page is read once, when the DM asks, and shown with Save / Cancel before anything is saved: name from the page's heading, kind from the first part of the address's path (magic-items → item, spells → spell, monsters → monster, feats → feat, species or races → species, subclasses → subclass, backgrounds → background, else other), words as plain text up to 2,000 characters (it says if the end was cut). An address already kept in `homebrew_pages` is re-used, never fetched again. The fetch (`rules/page_fetch.py`, on `dmbot.fetch`'s guards): https on port 443 only; every address the host resolves to must be public (private, loopback and link-local refused, and the connection goes to the addresses checked); redirects only within the allowed site, at most 3; no cookies, no sign-in; a plain user agent naming DMbot; 10 seconds and 2 MB; one page at a time per campaign and six in ten minutes. A 404 or a not-found page says "That page isn't public. Publish your homebrew on D&D Beyond first…"; a block or any other failure says "D&D Beyond didn't answer. Try again in a minute." and is logged without the page or its address. **The terms risk is accepted by the owner:** D&D Beyond's terms limit automated access; the owner reads them as aimed at scraping, which this is not (one page, when one user asks). Revisit if D&D Beyond objects or blocks it. Whether D&D Beyond answers server traffic at all is checked live by dev1.
- **Where the DM manages it:** ⚙️ Settings → 📚 **Homebrew** (or `/dmbot homebrew`), DM only, checked on every
  press: the list ("(off)" marks an entry that is off), ➕ Add (pick the kind, then a form), and on an entry
  Edit, Turn off / Turn on, its kind, 🗑 Remove. **Undo** a removal is kept in memory (the last five per campaign, gone at a
  restart), nothing stored.
- **Where it is read:** the precedence is house rules → homebrew → target → fallback, and **a homebrew name
  shadows the book's** (the book is never used for a name the campaign has overridden).
  - `/dmbot rule` shows the campaign's entry with the same name, tagged `[Homebrew]` with the source
    "<campaign>'s homebrew: <name>", to a player and to the DM alike.
  - The DM sidebar and table questions use every entry that is on. Up to 3 entries, each cut to 600
    characters, go to the AI after the house rules and before the free rules, with the tag and the source.
  - An entry that is off is read by none of these (one `usable_only` filter in the store's read, used by the
    reader). An own entry that is off stops shadowing a followed pack's entry of the same name: the pack's
    answers then (owner, 2026-10-16).
- **Part 1b (#1524):**
  - **Live rules cards** (`rules/homebrew_spotter.py`): when the table names one of the campaign's homebrew
    entries (whole name, longest first, a plural counts; an everyday word like Light only with its lead-in,
    and a homebrew creature with an everyday name never), a short card goes on the DM screen only, tagged `[Homebrew]` with the source "<campaign>'s homebrew: <name>", after any house rule that
    names it and before the book; a homebrew name shadows the book's card; once per entry per session and
    within the cards' one-a-minute limit; the buttons are the book cards' (**Read it all** shows the entry).
    The names are read when a session starts and again after any change.
  - **A file** (`rules/homebrew_file.py`): ⚙️ Settings → 📚 Homebrew has 📤 **Download file** and 📥 **Load
    file** (DM only). One entry per block, `## Name (kind)`, optional `Summary:` and `Off: yes` (an
    entry that is off; an older file's `Players may know:` line is accepted and ignored) lines, a blank line,
    then the words; read and written both ways without loss; a block that is not an
    entry is reported, never guessed. Loading shows what it would do (new, changed, the same, and what
    could not be used), then asks **"Is any of this from a published book?"**: **No** loads it as the DM's
    own; **Yes** shows the right-to-use warning ("DMbot doesn't check this. It is kept only in this
    campaign, never shared…") and **only after "I have the right: load it"** records who confirmed and when
    (`shared_confirmations`, purpose `rulebook`) and loads it with `from_book` set (migration 0099): that
    text can never go into a shared pack. An entry whose name the campaign has (any case) is updated
    (version + 1); a file that would take the campaign over 200 entries loads nothing and says the count.
    An entry from a shared rulebook carries the players' card tags in the file (2026-10-16, #1584): a line
    `[Shared book]` under its heading, and `[Shared book] [Legacy 2014]` when its book is for the 2014
    rules and the campaign plays newer ones (the same `book_tag` the card uses). Loading a file reads
    that line and ignores it: only "I have the right" ever marks an entry as from a book. The website's
    download writes the same file for the entries it shows, with the same tags (2026-10-16, #1593): its
    door returns `fromBook` and `legacy` for each entry (`legacy` is `book_is_legacy`'s test, worked out in
    the database against the campaign's own ruleset), and `homebrew_file.write` takes that answer per entry,
    so the two files are byte-equal for the same entries.
  - **On the website** (#1525, #1545, migration 0100; tags 2026-10-16, #1579, migration 0107): the signed-in
    Homebrew page shows a campaign's entries to anyone in the server, with a text-file download that is
    byte-equal to the bot's file. Since part 2 (#1650) it shows every entry, an entry that is off muted and
    marked "(off)", and where each came from (#1657: "from dndbeyond.com", "by Sam"). Entries loaded from a
    shared book are tagged like the Discord players' card: "From a shared book", and "[Legacy 2014]" for a
    2014 book while the campaign plays newer rules (legacy content is tagged wherever it appears). Still
    never: who added an entry (a Discord id), its id, a page's address or the book's name.
- Sharing as a pack (part 2) is built: see "Shared rulesets" (#1527).

**Homebrew (part 2): links, sources and on/off (owner, 2026-10-16).** This changes part 1. Where the two
differ, this wins.
- **Rules are house rules, not homebrew.** Homebrew is game content: spells, monsters, items, feats,
  conditions and other things. There is no "rule" kind; migration 0111 moves rule entries into house rules
  (#1628).
- **Homebrew can be added from a link.** The DM pastes the address of a **published** homebrew page,
  for example `https://www.dndbeyond.com/magic-items/2302039-amulet-of-trueshot`, and DMbot reads that one
  page and saves the entry. **Typing an entry in (➕ Add) and 📥 Load file stay**, because they already
  work (owner, 2026-10-16), each recording its own source (below).
  - **Only approved sites are fetched**, starting with dndbeyond.com. One address, fetched once, at the
    direction of the one DM who pasted it: never a crawl, never a list of pages, never following links
    from the page. https only, the site's own address after any redirect, a size and time limit, and no
    D&D Beyond sign-in (no password or cookies, ever).
  - **Not published:** D&D Beyond answers an unpublished homebrew page with "page not found". DMbot then
    says: "That page isn't public. Publish your homebrew on D&D Beyond first, then paste the link again."
  - **The risk is accepted (owner, 2026-10-16):** D&D Beyond's terms limit automated access. The owner
    reads them as aimed at automated scraping, which this is not: a single page, pulled when a single
    user asks for it. Revisit if D&D Beyond objects or blocks it.
- **Permission once per site, per campaign.** The first time a campaign imports from a site, the DM sees
  the right-to-use warning ("DMbot doesn't check this…") and confirms once. That approval sticks: every
  later link from the same site in that campaign needs no new confirmation. Who confirmed and when is kept
  (table of approved sites per campaign). A campaign that has not approved a site cannot use content from
  it, whether imported there or reached through a pack. **Seen and taken back (#1652):** ⚙️ Settings → 📚 Homebrew → 🌐 Approved sites lists each approved site with who approved it and when, and a DM can take one back after one plain question ("Take back dndbeyond.com? DMbot will stop using the N entries from it until a DM says yes again. Nothing is deleted."). The entries stay saved and the list marks them "(waiting for the site)"; adding from that site again asks the right-to-use question again. A follower's approvals are its own list.
- **🔄 Update from the link (#1645).** On a link entry, a DM can fetch its one page again: same guards,
  same one-at-a-time and hourly limits, and the site's approval checked again. DMbot shows the new words
  and what changed ("Nothing changed on the page." when so); only **Save** replaces the kept copy, and
  because there is one copy per address, every campaign pointing at it gets the new words (the button says
  so). A page that has gone private or been removed is marked stale and its words are kept. A restored
  page becomes trusted once a fetch replaces it. Nothing refreshes by itself: only a DM's press fetches.
- **Every entry records its source.** The database keeps, for every entry:
  - **Link:** the address it came from and its site (the domain), plus who added it.
  - **Typed:** the Discord user who typed it.
  - **Uploaded** (📥 Load file): the Discord user by default, or a credit the uploader gives instead: a website
    (its domain then needs the same once-per-site approval) or a typed name.
  Entries saved before this record "typed" by the DM who made them.
- **One copy of a published page.** A page imported by link is public content, not a campaign's own data,
  so DMbot keeps **one copy per address** and each campaign's entry points to it. This is the second
  exception to campaign isolation, next to shared rulesets: only the published page's content is shared.
  Who imported it, from which campaign, and that campaign's on/off choice stay with the campaign.
- **On/off replaces hidden.** Homebrew and house rules are not hidden from players any more; the 🙈/👀
  "players may know" switch goes. Instead each entry is **on** (used everywhere: rule lookups, rules cards,
  the DM sidebar, table questions) or **off** (kept, but DMbot doesn't use it at the table). **Remove**
  deletes it. Existing entries are all on.
- **Packs carry imported entries.** A pack holds pointers to entries, so imported pages travel by
  reference, never as copies. A campaign that follows a pack must have approved each site the pack's
  entries come from, once per site, before those entries are used. Entries from a shared rulebook
  (`from_book`) still never go into a pack.
- **The homebrew menu shows kinds first (owner, 2026-10-17).** A full list gets huge, so ⚙️ Settings → 📚 Homebrew
  opens with one button per kind, each with how many are on out of how many there are: **[Spells 10/12]**
  (10 on, 12 in all). A kind's button opens that kind's list. Kinds with no entries show no button. #1665.
- **Kinds** (owner's live check, 2026-10-17): species, subclass and background join spell, monster, item, feat,
  condition and other (#1663).
- **Build order:** #1628 (rules out, PR #1630) → #1629 (on/off replaces hidden) → sources and site approvals
  (schema) → import by link → packs with imported entries → the website.

**Shared rulesets: homebrew packs and house-rules sets (owner, 2026-10-14).** Rules can be shared between
campaigns, in the same server or another one, when both sides choose to. Only rules cross; a campaign's story
never does.
- **Three layers:**
  | Layer | What | Who uses it |
  |---|---|---|
  | **Official** | SRD rules data and the core relationships (#1189) | Every campaign, always; read-only, versioned |
  | **Shared** | A named **homebrew pack** or **house-rules set**: rules entries, house rules, and the relationships that content needs | Campaigns whose DM chose to follow it |
  | **The campaign's own** | Its own homebrew, house rules and relationships, and all its story memory | That campaign only |
- **Precedence, highest first:** the campaign's own house rules → the house-rules set it follows → its own
  homebrew → the homebrew packs it follows (in the order the DM lists them) → target ruleset → fallback.
  Every rules answer names where it came from ("Frostmaiden homebrew, v3").
- **Relationships layer the same way:** a campaign's vocabulary is the official list, plus its shared sets'
  relationships, plus its own. The reuse-before-create check looks across all three, and every relationship
  and fact records its source (official, which set and version, or this campaign) with its definition version.
- **Publishing:** a DM of a **paid** campaign may publish its homebrew as a pack, or its house rules as a set,
  with a name. Within the server, other DMs pick it from a list; for another server, the publishing DM gets a
  **share code** to hand out (it can be turned off, which stops new followers only). Try It campaigns can
  follow but not publish (they have no backups either).
- **What a set may hold** (updated 2026-10-16, #1635, "Homebrew (part 2)"): a homebrew pack holds the campaign's
  homebrew: anything but an entry from a shared rulebook or adventure. **Never** anything from a shared
  rulebook or adventure (the right to use it was confirmed for one campaign only, CLAUDE.md, IP), checked by
  where each entry came from; never story memory, names, transcripts, DM notes or character sheets.
  **Imported entries travel by reference:** an entry imported from a published page is a pointer in the pack
  (the page's address and site, no words and no summary), because the page is kept once in `homebrew_pages`.
  An upload credited to a website carries its words and names the site. **A follower must approve each site
  once per campaign** before its entries are used (the same right-to-use step as importing): the Homebrew
  packs card says how many entries from which site are waiting and has a button for each (**Use dndbeyond.com**),
  the follow step and an update's DM-screen note say so too, and approving turns the entries on with no other
  step. A new site in an update waits the same way; a site already approved asks nothing. A pointer whose page
  isn't kept here (or was only recreated from a backup) is skipped, not shown. A declined change that holds a
  pointer lands in the campaign's own homebrew as the same page.
- **One writer.** Only the publishing campaign's DMs change a set; every change is logged with who, which
  campaign and when. *(Supervisor, 2026-10-14, #1256: no separate co-editor list. To let another DM edit the
  set, make them a DM of the publishing campaign. Publishing a version can be undone: Undo publishes the earlier
  rules again as the next version, which followers take at their next session.)*
  - **A follower can't change it.** When a following campaign's DM changes a shared rule, or a ruling is made
    at their table, it goes into **their own** house rules, which win over the set. The publisher never sees
    it and is never affected. **💡 Suggest** sends it to the publisher's DMs as a suggestion
    ([See] [Add to set] [No thanks]); nothing changes until they press Add to set.
  - **The document of record can be edited outside DMbot** by anyone with edit access. Its changes reach the
    publisher's **own** house rules through the usual document sync (the DM confirms them), and reach
    followers only when the publisher presses **🔄 Publish my changes** (Supervisor, 2026-10-14, #1259; no
    separate pending-changes screen and no write-back to followers' files).
  - **Suggestions** (#1259): the publisher's note says "A table that uses <set> house rules suggests: …" and
    never names the other campaign, server or DM; Add to set makes the next version. *(Supervisor,
    2026-10-15, #1315: wording matched to the buttons as built.)*
- **Updates reach followers at their next session start, never mid-game.** DMbot takes the update then
  (Follow, the default) and posts a note on the DM screen: "📚 **Frostmaiden** house rules updated to v4:
  2 rules. DMbot took the new version. [See changes] [Pick changes] [Undo]".
  **When several shared rules changed at one start (#1538)** the notes are combined into one: "📚 Shared
  rules updated at this start. DMbot took the new versions. Buttons are by number:" with a numbered line for
  each update ("1. **Frostmaiden** house rules, now v4: 2 rules", "2. **Frost Pack** homebrew, now v3: 2
  entries changed"), the lines about players now knowing something or the campaign's own entries still
  winning under their update, and each update's own row of buttons, short and numbered to match ("See 2",
  "Pick 2", "Compare 2", "Undo 2"; a phone cuts a label from the right, so the number comes straight after
  the word). Four updates get rows. A fifth and later are still taken and still named, with "N without
  buttons here: open ⚙️ Settings → 📚 Shared rules". A set or pack that stopped being shared joins as a plain
  line and is never cut from the note; when the text is long, only the extra lines under an update give
  way. A single update keeps the note above, unchanged. The buttons are the same restart-proof ones, each
  tied to its own version, and one check failing never loses the other's note.
  - **Follow** (the default, also what happens if the DM does nothing): the changes are taken and the campaign
    keeps following. **Undo** goes back to the earlier version and keeps it: DMbot does not take that same
    version again at the next session, only a newer one.
  - **Pick changes** (cherry pick): undo the take and decide change by change. **If every change is accepted, it is the same
    as Follow.** **If any change is rejected,** the set as the campaign had it, plus the accepted changes and
    without the rejected ones, is **merged into the campaign's own house rules** (or its own homebrew), and
    the campaign stops following that set. Its own rules still win over what was merged. The merged rules
    keep a note of where they came from ("from Frostmaiden house rules v4"); the campaign may follow the set
    again later.
  - A shared rule the campaign already changed for itself stays its own: "A shared rule you changed was
    updated. Yours still applies. [Compare]".
- **Never lost:** a following campaign's backup holds a copy of the set version it uses. If the publisher
  deletes or stops sharing a set, followers keep their last version, marked "no longer updated".
- **Isolation:** shared sets live in their own tables, readable only by the campaigns that follow them
  (row-level security through the follow record), and never readable by anything else of the publishing
  campaign. A follow, a share code and every change are visible and can be undone by the DM.
- *How updates are taken (Supervisor, 2026-10-14, #1233):* at the session's start the update is taken at once
  (Follow), credited to DMbot, and the DM-screen note offers [See changes] [Pick changes] [Undo]. Pick changes
  undoes the take and opens the picker, and a choice is checked against the version the DM was shown. A share
  code that is turned off still answers updates for follows made before it was turned off; only Stop sharing
  ends updates. After a merge, the merged rules rank below the campaign's own and are cited "from <set>".
- **Build:** #1190 (sets, publishing, following, precedence, share codes) and #1191 (updates: Follow and Cherry
  pick, pending document changes, suggestions), CloudDev.
- **Build status (#1190):** built: the tables and store (#1221), the Settings menu (#1223), house rules
  reaching the sidebar, rules lookup, rules cards and alerts (a followed set's rules come after the campaign's
  own, each cited as "<set> house rules, v4"), follower backups (never the share code) and the note at a
  follower's next start when a set is gone (#1262).
- **Build status (#1541, Make it my own), built:** the card of a followed pack's entry (`/dmbot rule`, and
  the rules card's Read it all) has a **✏️ Make it my own** button for the campaign's DMs, re-checked on the
  press. It copies the entry as it is (kind, name, summary, text, players-may-know) into the campaign's own
  homebrew, tells a running session, and opens Edit on the copy; saving shows the entry's own card with
  "This is now your own, and it replaces the pack's version in this campaign." Cancel on the form keeps
  the copy as it is, and the button comes off the card (a second press says the entry is already the
  campaign's own). The campaign's own entry then wins over the
  pack's, and a later pack change to that name is reported as "yours wins". A full homebrew or a name that
  became the campaign's own meanwhile copies nothing and says so. A pack holds no book text, so the copy is
  never marked from a book.
- **Build status (#1527, homebrew packs), part A, built:** a pack is a shared ruleset of kind `homebrew` on
  the same tables, codes and door as sets (no new migration: `rule_sets.kind` and `rule_set_follows.position`
  were there). Its content is the campaign's own entries only (`rules/pack_content.py`); an entry with
  `from_book` set is left out and named, so book text never leaves its campaign (CLAUDE.md, IP rule). A
  campaign publishes one pack and follows up to five, in the DM's order; its backup carries each followed
  copy, never the code. Readers (`/dmbot rule`, rules cards and alerts, the sidebar, table questions) read the
  campaign's own homebrew first, then its followed packs in order (a name is taken once, by the first that has
  it), cited "<pack> homebrew, vN" and tagged `[Homebrew]`; a pack's entries are all on. Part B built (⚙️ Settings → 📚 Shared rules → 📦 Homebrew packs): Share (paid plans; names the
  entries a book keeps at home), Follow (pick from this server or a code), order with Earlier/Later, Stop
  following with Undo; a running session reads the new names at once. Part C built: at a session's start DMbot takes a
  followed pack's newer version (Follow, credited to DMbot) and posts one DM-screen note with See changes /
  Pick changes / Undo, exactly as for sets; Undo keeps DMbot from retaking that version; leaving anything out
  of Pick changes copies the pack into the campaign's own homebrew (its own entries win) and ends the follow,
  one Undo for all; a pack whose publisher stopped sharing is marked "no longer updated", said once. No
  Compare button: the note says when a changed entry shares a name with the campaign's own (which wins).
  Suggestions built (#1537, migration 0102): a following campaign's DM presses 💡 Suggest to the pack on the
  campaign's own entry (Settings → Homebrew → the entry; a pick when several packs are followed; an entry
  that came from a book says it stays home). The publisher gets one DM-screen note, "A table using **<pack>
  homebrew** suggests (sender not shown)", with See / Add to pack / No thanks, and the sender is never
  named. Add to pack puts the entry in the publisher's own homebrew (a new name, or replacing their own
  entry of that name) and makes the next pack version through the same checks as Publish my changes (paid
  plan, a DM, nothing from a book). Limits as for sets (#1259): one open per entry per pack, an open cap,
  stale ones expire; the sender hears the outcome once at its next session start.
- **Relationship definitions travel with a set (#1456; Supervisor decision, 2026-10-16).** At Share the DM
  sees a tick list of the campaign's own relationships (each with its label and what it means; all ticked to
  start) and only the ticked ones go into the set's content: words only (the name, how it reads from the
  other side, what it means, the kinds it connects: exactly what the list shows), built from the official
  kinds of thing, never a fact, name, note or example (examples and the free-text lists hold the publisher's
  names). A follower gets them read-only and marked as the set's (they can't be
  retired by the table); one the follower already has by the same name, or an official one, is never
  overwritten (its own wins, with one line at Follow time). Unfollow removes a definition no fact uses and
  **deprecates** one facts use (old facts still read; no new fact can use it). A new version's changes to
  them are taken at session start with the rules and come with Follow; Cherry pick takes them one at a time
  like rules: a ticked one becomes the campaign's own (the follow ends, as for rules), the rest follow the
  Unfollow rule. A follower's memory backup holds its own definitions and not the set's: the set's copy rides
  in the followed-sets part and Follow makes them again.
  **Changing which relationships travel, later (#1475, #1479).** 📤 Publish my changes shows the same
  tick list as Share, with the relationships the set carries ticked and the campaign's other shareable
  ones not. The new version holds exactly what is ticked: ticking a new one adds it, unticking one takes
  it out of the set (the publisher's own relationship is never touched). Followers get the change at their
  next session start through the usual update, listed as new or removed; a removed one follows the Unfollow
  rule (deleted when no fact uses it, retired when one does). "Keep as it is" changes nothing. No list is
  shown when the campaign and the set have no relationships.

**AI models by task (decided 2026-10-10, built #1006).** Every AI call names a *tier*, never
a model.
- **Which job uses which:** **FAST** (the small model, Haiku today) for simple jobs:
  transcript cleaning, the off-topic filter, rules checks and lookups, the audio check and
  the DM sidebar's first answer. **CAREFUL** (the middle model, Sonnet) for **Find names**
  (its proposals go into the campaign's memory; it runs per batch, not per line), house-rule
  changes, and the sidebar's second try (below). **DEEP** (the strongest, Opus) for PlotBot
  (phase 5c; nothing uses it yet).
  `dmbot.ai.FEATURE_TIERS` lists every job's tier; a test checks no other file names a model.
  Model names change, so the tiers are the stable thing.
- **The settings:** `AI_MODEL_FAST`, `AI_MODEL_CAREFUL`, `AI_MODEL_DEEP`, each with a
  default; a malformed one (not a `claude-…` name) stops start-up naming the setting. The
  old `AI_MODEL` stands for FAST, with a line in the log, and is removed in a later update.
  Note that it now covers every quick job (before, it chose the model for Find names only;
  the filter and the sidebar were always on the small one). Find names is on CAREFUL since
  #1017, so `AI_MODEL_CAREFUL` is the one that now picks its model.
- **If a model isn't available** (not found, or a `permission_error` for this key) the call
  goes to the next tier down (DEEP, CAREFUL, FAST). It is noted once in the log, naming the
  setting to check, and only when a lower tier then answered (so a bad key is not blamed on a
  model). The model is tried again after 15 minutes, so fixing the name or the access needs
  no restart. A 403 that isn't a `permission_error` (a blocked request) never counts.
  Running out of money never falls back (it messages the admins, #972).
- **A careful second try for the sidebar (#1017):** when a quick answer fails a check (it
  says the rules don't say although an entry for the thing asked about was given, it breaks
  the length rules, or it cites a house rule or an SRD entry that doesn't say it), the careful
  tier is asked once, before the source is cut or the "not in DMbot's rules" note is added. It
  must fit in what is left of the sidebar's 8 seconds (at least 4). With less time, or if it
  is slow, fails, or the AI is out of money, the quick answer stands as it always did (no
  other tier is tried). The model that answered is recorded in the sidebar's lineage. Without
  a careful tier the old same-model retry is unchanged.
- **Rules alerts (when built):** a FAST candidate is confirmed by one CAREFUL call before it
  reaches the DM screen (`Feature.RULES_CONFIRM` is listed ahead of use).
- **PlotBot (owner, 2026-10-10):** the continuity check runs as a batch when a listening
  session ends, on the DEEP tier. DMbot first tells the DM in #dm-screen that it is making
  a quick check for plot holes and for clarifications about changes in the plot. When it is
  done it tells the DM either that the check found nothing or what needs the DM's
  clarification, each with Confirm / Not right buttons. Nothing is changed in the campaign's
  memory without the DM. In-session NPC tracking and live continuity hints use CAREFUL; Opus
  is never used live. Reading shared adventures and rulebooks uses CAREFUL through
  Anthropic's batch service (about half the price; it is not urgent).
- **One model for each job:** a job stays on one model, because Anthropic keeps cached
  prompts separately for each model; switching a job's model back and forth throws the
  cache away.
- **DEEP work runs in batches:** PlotBot and the after-session continuity pass run at a
  break, at the end of a session, or when the DM asks; never on every transcript line.
  The strongest model costs several times what the small one does.
- **Changing a tier's model:** a tier's model changes only after its checks pass on the new
  model (for the sidebar, `python -m dmbot.devtools.sidebar_check`). Moving a job to
  another tier is a change to `FEATURE_TIERS` and its own issue (Find names and the
  sidebar's second try moved in #1017).
- **Logging:** the start-up line shows the three models in use (or that AI is off). Each
  call logs one line: tier, model, tokens in and out, tokens read from the cache; never
  content. `python -m dmbot.devtools.sidebar_check` prints the model that answered.

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
  recorded and gets the question again. An old consent button from before the switch
  shows the new question instead of saving a yes. The general terms version (#35) still
  covers other wording changes.
  It has a **✅ I'm 16 or older, record me** button (#33).
  **Minimum age 16 (owner, 2026-10-10); the consent button confirms it** (#1018): the request
  says "You must be 16 or older to be recorded.", no age or birthdate is stored, and the
  terms version went up (4), so earlier yeses are asked again at the next join. Someone under
  16 has nothing to press (the request tells them to press No thanks and keep playing): they
  are not asked for an age and nothing of theirs is captured.
- **Consent carries over** between sessions, per server. In **every session**, a consented
  person gets one short private reminder with the date they consented, plus a
  **🗂️ Menu** button (#33, #34; the menu replaced a red 🛑 button, below). Rejoining in
  the same session doesn't send another (built 2026-10-05: once per person per session,
  not once per join).
- **The 🗂️ Menu, and a warning before stopping** (owner decision, 2026-10-08: the big red
  🛑 Stop recording me button on every private message was annoying). DMbot's private
  messages that offered 🛑 (the "you said yes" message, the per-session reminder, the
  `/consent give` answer) carry one grey **🗂️ Menu** button instead. The menu is about that
  one server and shows only what applies: **📜 My character sheet** (#723, moved in from
  its own button), **Stop recording me**, and **Close**. **Stop recording me** first shows
  one warning: "Stop recording you in <server>? DMbot won't write down anything you say
  from now on. The campaign's record will have gaps wherever you speak, so its summaries
  can miss things and plot holes can appear. You can start again any time." with **Yes,
  stop recording me** (red) and **Keep recording**. Yes stops at once, exactly as before.
  `/consent revoke` shows the same warning. Rules that keep stopping easy:
  - one warning, one tap to confirm; never a second ask, a wait, or a reason to give;
  - plain facts only, no guilt ("you'll ruin the game") and no pressure;
  - **Keep recording** changes nothing; it says "OK, DMbot keeps recording you in
    <server>" and how to stop later;
  - **in place on lasting messages** (2026-10-08, #807): on the per-session reminder and
    the "you said yes" message, 🗂️ Menu swaps that message's buttons for the menu, Stop
    puts the warning at the top of that message's text, Yes leaves it reading "🛑
    Stopped…" with the ✅ consent button, and Keep or Close restore it exactly. So a reminder never
    keeps saying "recording you" after a stop. The warning is in the text, never an embed,
    because Discord hides embeds for people who turn previews off;
  - 🗂️ Menu and `/consent revoke` answer from the in-memory consent first, never waiting
    on the database before Discord's 3-second limit, so stopping can't fail on a slow
    database;
  - a 🛑 button on a message sent before this change shows the warning too;
  - **No thanks** on the first request stays one tap (nothing is recorded yet).
  Why: the owner wants a calmer message, and people who stop should know what it costs
  the campaign. Stopping takes three taps (Menu, Stop, Yes) where saying yes takes one;
  the owner's legal review found that acceptable (2026-10-08). The warning still stays
  short and one-time, and the menu button is on every reminder.
- **Off the record** (owner question, decided by Supervisor 2026-10-23, #2466): a person can step off
  the record for a while without stopping. **A button is the main way.** **⏸️ Pause recording me** in the
  🗂️ Menu (and on the per-session reminder) takes 5 minutes, 15 minutes, or until ▶️ Resume. It needs no
  warning because it ends by itself, and it always ends with the listening session. A paused person is
  off ears' allowlist, exactly like someone who never agreed: nothing of theirs is captured, decoded,
  transcribed or stored, and core re-checks the pause after every async step as it does consent. The
  DM also has **⏸️ Pause the table** for breaks. The table chat says "⏸️ <name> is off the record for 5
  minutes." and "▶️ <name> is back on the record.", and both transcripts get one marker line, "(<name>
  was off the record, 20:14–20:19)". Nothing is cut from a transcript, because nothing was recorded, so
  this fits the unedited raw transcript (#933). **Saying it** works only when it is said to DMbot: **"Hey
  DMbot, off the record"** (spoken or typed) pauses the speaker for 5 minutes. A plain "off the record"
  is never acted on, because NPCs say it in the story; a line said as a named NPC never triggers
  (#1624). There is no spoken "back on the record", since DMbot hears nothing from a paused person;
  they come back with ▶️, the timer, or the session's end. It is not retroactive: what was said before
  the pause stays on the record. A paused person's **typed** question to DMbot is still answered
  privately (typing to DMbot is deliberate), but nothing of it is written to any transcript or stored
  (Supervisor, 2026-10-23, #2470). A pause has no lasting effect: it changes no stored consent or
  choice, a restart while paused keeps the pause (it never starts recording someone who was told they
  are off the record), and stopping during a pause ends it at once.
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
  read it" wording; version 3 (#52) adds that DMbot's helper has an AI company (Anthropic)
  read the text, with who said it, to give the DM notes, not used to train their AI (said
  once for every helper); every yes saved before versions were recorded is treated as version 1
  (we can't tell which wording each person saw). A test pins the request's wording to
  the version number.
- The public "DMbot is listening" notice in the voice channel's chat still posts once per
  `/dmbot start`, is not repeated after a voice-service reconnect, and the DM is warned if
  it can't be posted.

**Transcripts vs. the DM screen (decided 2026-10-04).**
| Content | Who sees it |
|---|---|
| **Transcripts** (what was said at the table) | **Anyone in the Discord server** (decided 2026-10-05): live in the transcript channel (#124), and as downloads, raw or cleaned (#41, #125). Only people who agreed are ever recorded, and the consent request tells them the whole server can read it. **View only:** "Did they mean…?" prompts, Undo buttons and all other DM-screen content never appear in the transcript channel or a transcript. The DM sidebar is the one exception (owner, 2026-10-09): its lines go into the raw transcript, tagged with their data lineage, and never the cleaned one. The raw transcript is unedited and unredacted for everyone who may read transcripts, spoilers included (#933). |
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

**How much DMbot says (built 2026-10-08, #504).** A campaign setting for the DM screen,
picked with buttons when creating a campaign (**Quiet / Normal**, sharing the last row
with Create; Chatty isn't offered until something uses it) and changed any time with
**⚙️ Settings**. **Where it is** (owner, 2026-10-21): only in the campaign's DM Bot channel
(`#dmb-dm-bot-<short name>`), under its menu button, now named **👤 DMbot Main Menu** (it was "My
buttons"). It is no longer on the DM screen's help card or the "Listening" message. Two private ways in
stay, because only the DM sees them: the ⚙️ Settings button on `/dmbot start`'s channel picker (#1619) and
`/dmbot settings`, the fallback when the DM Bot channel can't be reached (Supervisor, 2026-10-21, #2289).
**How it is grouped**
(owner, 2026-10-21), so the card keeps the often-used buttons and puts the rest a tap away:
- **👁️ Who can see the DM screen** opens the three choices (Only the DM, Players can peek, Everyone in
  the server), with the current one ticked.
- **🗂️ Campaign management** opens 🤝 Hand over, ⏸️ Pause this campaign, 🗄️ Transcript retention (was
  "Transcripts": how long transcripts are kept), 👑 Game masters and 🗑️ Delete this campaign, each with its
  own rules as before (Hand over and Delete for the plan holder only).
Earlier wording, kept for the history: Settings was on the DM screen's help card (there between sessions too) and first on
the "Listening" message (built 2026-10-08, #515). Settings opens privately, for the
campaign's DMs or a server manager: how much DMbot says (one tap saves it, and a running
session follows the change from now on) and who can see the DM screen, each tap redrawing
the card with what's saved, plus where saved transcripts are (`/transcript`). A level
change is noted in the DM screen ("🔇 How much DMbot says: Quiet."), so a co-DM knows
why DMbot went quiet, and a session starting at that moment follows it too (#553). The
help card is posted again when its words or its buttons change; after a quick restart
it isn't (it's refreshed at the next `/dmbot start`), so a resumed session may lack
⚙️ Settings on its Listening message until then (decided, #553).
Backups carry the level, and a restore uses the backup's (older backups: Normal).
| Level | What DMbot posts on its own |
|---|---|
| **Quiet** | Only what you ask for, plus warnings and the 🙈 Left out as off-topic list. Fewer misheard names get fixed. |
| **Normal** (default) | Also questions and fixes, one at a time: "Did they mean…?" and ✏️ Name fixes to check |
| **Chatty** | Also what it noticed (reserved: not offered yet; behaves like Normal) |

Every post a level can turn off asks `dmbot.dm_screen.levels.allows` first, with its
kind: `question`, `fix_note` or `notice`. **Alerts** (speech-to-text stopped or working
again, hours warnings) always post, at every level, and so does the one "🙈 Left out as
off-topic" message (#677: the DM's only chance to undo). The level is read when a session
starts, and ⚙️ Settings updates a running session too. An
unknown kind never shows, so nothing new slips past Quiet. At Quiet a fix from a name
DMbot only suggested isn't made at all, because such a fix is never silent and there'd
be no Undo to show (nor in a stopped session still finishing its last lines).
Continuity warnings (Phase 5a) and rules alerts must pick a kind
when they land.

**The DM Bot channel (owner, 2026-10-17).** The DM screen was getting cluttered by settings and options, so
they move to a channel of their own, **DM Bot** (`dmb-dm-bot-<short name>`, one per campaign like the other
sub-channels), and the DM screen keeps only what the game needs while it is played: rules alerts, the
sidebar, the clock, cards and the after-session notes for the DM.
- **Everyone in the server can see it; only DMbot posts in it.** It holds one control panel message, kept up
  to date. Every press answers **privately**, and what a person is offered depends on their role in that
  campaign, checked again on every press: a **game master** gets every button (⚙️ Settings and all it holds:
  homebrew, house rules, game masters, transcripts, the clock's settings, delete campaign for the plan
  holder); a **player** gets their own things (their 🗂️ Menu: consent, Stop recording me, Delete my
  transcripts; 📖 Look up a rule; where to read transcripts); anyone else in the server gets what any member
  may do (rule lookups, transcripts). A button nobody in a role may use is not shown to them.
- **Nothing secret is ever posted there:** it is a public channel, so DM-screen content never goes into the
  panel; it only appears in a game master's private answer.
- **The DM screen** loses the Settings card and the option buttons; a one-line pointer ("Settings and
  options are in #dmb-dm-bot-…") replaces them. `/dmbot settings` still works and answers privately.
- **📇 Names (owner, 2026-10-18, #1986):** a game master's card and ⚙️ Settings each have a 📇 Names
  button that opens the same private names home as `/dmbot names` (list, search, fix, add). The home
  also shows, only while something waits, one line and a button for each review the after-session posts
  open (❓ Check names, 🧑 Check who is who, 🗣️ Check what was said), so a missed post is never lost.
- Built in #1666 (CloudDev); Help follows (WebDev).

**Channel structure (decided 2026-10-04, #85).** Every channel DMbot creates starts with
`dmb-`, so its channels group together in the sidebar and are clearly bot-managed.

*Names.* Discord turns every text channel name into lowercase with dashes for spaces,
so the docs always show names the way Discord does. For the campaign
**Rime of the Frostmaiden**:

| Channel | Name in Discord | Built in |
|---|---|---|
| DM screen | `dmb-dm-screen-rime-of-the-frostmaiden` | Phase 1.5 (now) |
| DM Bot (settings and options, everyone; owner, 2026-10-17) | `dmb-dm-bot-rmfthfrstmdn` | #1666 |
| Live transcript (cleaned lines, view only) | `dmb-transcript-rmfthfrstmdn` | Phase 1 (#124, built 2026-10-06; misheard names fixed since 2026-10-07, #127) |
| Rules archive (house rules, overrides, rulings) | `dmb-rules-rmfthfrstmdn` | House rules: #1752 (overrides and rulings: Phase 3, with the AI rules alerts) |
| Game time (the players' clock; owner, 2026-10-13) | `dmb-gametime-rmfthfrstmdn` | Phase 4 |
| DMbot chat (table questions and their answers; owner, 2026-10-14) | `dmb-chat-rmfthfrstmdn` | Phase 4 |
| NPCs (roster, relationships, factions) | `dmb-npcs-rmfthfrstmdn` | Roster: #1761 (standing and relationships beyond the roster: Phase 5) |
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
  channels are `dmb-dm-screen-2frozens-cake` and `dmb-gametime-2frznsck` (and so on).
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
- **The rules archive is built (2026-10-17, #1752), house rules only.** `dmb-rules-<short name>`
  is made and repaired at every `/dmbot start`, in the DM screen's category, with a topic and a
  pinned "What's this channel?" card. It is controlled: its overwrites are the DM screen's, the
  peekers included, except that nobody posts there, the DM too. Every change to the screen's
  visibility, DMs or peekers is applied to it; a peek and a hide open and close both channels, and
  the peek warning names both. It shows one message per saved house rule ("🏠 House rule N": the
  words, what it replaces, where it came from: your DM, said at the table, or a followed set's
  copy) and one "📚 This campaign follows the house-rule set **<set>**" line per followed house-rules set. Nothing proposed or waiting,
  no DM-screen content and no rulebook text goes there, and a rule's "what happened" note stays
  out. Messages are edited in place when a rule is edited and deleted when it is removed; they
  are found again by their first words, so nothing about them is stored and a restart rebuilds the
  same list, at most 5 edits or deletes a run (the rest next time). A change reaches the channel a
  few seconds later; the bot also brings every campaign's channel up to date once at start. A
  channel deleted by hand is made again at the next `/dmbot start`. Its id is not in a backup.
- **The NPC list is built (2026-10-17, #1761), a roster with no AI.** `dmb-npcs-<short name>` is
  made, repaired and kept like the rules channel (own lock, batching, the start-up sweep, edits in
  place) and is controlled the same way: it follows the DM screen's visibility and peekers, nobody
  posts there, and a peek and a hide open and close all three channels, with the peek warning
  naming the rules channel and the NPC list. One message per **confirmed** NPC, god or group
  (never a player's character or the party): the name, what it is, the other names it goes by,
  the groups it is in and who it is closely tied to (up to 5, then "+N more"), only from facts the
  DM confirmed that are true now. Because players can peek here and the list sits in view all
  session, it is built by `RecallService.roster`, which applies the **players** audience's rules
  and not a second copy: never a secret name or a name only misheard, an entry the DM marked
  "Players don't know yet" (#2321, see "Players don't know this one yet" below), a limited clue or anything
  that points to one, a story thread, a deed or standing, the DM's note (so there is **no
  description line**: the only description an entry has is the DM's note), a condition such as
  dead or missing (the table may not know yet), or anything from the adventure plan. Groups
  come first, then people, alphabetical, at most 200 messages and then "+N more: open a name card
  for the rest". A new entry is posted at the end, so the order is exact only after a reorder: when 10
  or more messages are out of place and no session is running for the campaign (at the start of a
  session, or at its end), whatever follows the first misplaced message is deleted (in bulk) and
  posted again; a deploy's start-up sweep never does this while a session runs, and never for fewer
  than 10. A change to the campaign's memory (confirm, merge, reject, rename, secret flags, Undo)
  refreshes the list a few seconds later, at most one pass a campaign a minute, and a pass sends
  edits that take a name away before cosmetic ones. Which message shows which entry is a small
  table (`npcs_channel_messages`, migration 0128), so no id is in the text; it is not in a backup,
  and a lost row is made good by deleting DMbot's own unrecorded messages and posting them again.
  Organized mode is a separate
  decision and is not changed here.
- **Every NPC opens its own page, with private notes** (owner, 2026-10-21; details Supervisor's).
  - **In Discord:** each message in the NPC list gets one small link button, **📖 Open**. It opens
    that NPC's page on the website. It is a plain link button, so no click is handled by the bot,
    nothing breaks after a restart, and the message text is unchanged. The link carries the entry's
    reference in its address, not in the text.
  - **The name is the button** (owner, 2026-10-22, replacing 📖 Open, which looked sloppy repeated down the
    list): each NPC's message is the button itself, labelled with the kind's icon, the name and the kind
    ("👤 Belleros · NPC" style; at most 80 characters, cut with "…"). Its other lines ("Also called: …") sit
    beside it, as text with the button on the same line, using Discord's newer message layout (a section
    with a button). With no other lines the message is just the button. With no website address the message
    stays as it was, the name in bold and no button. Existing messages are replaced once, because a message
    can't switch to the newer layout by an edit.
  - **Who sees the page:** anyone in the campaign's Discord server, signed in on the website with
    Discord, the same people who may read the transcripts. Someone not in that server gets "This
    page is for the people in that Discord server."
  - **What the page shows** is the NPC list's own players' view, from `RecallService.roster` with
    the same rules, not a second copy: the name, what it is, its other names, its groups and close
    ties. It also links the **posted** recaps that mention it, which the table has already seen.
    Never a secret name, a clue, a condition, a thread, the DM's note or anything from the
    adventure plan. The campaign's DMs also see their own note on the NPC (DM-only, as in Discord).
  - **My notes:** a private notes box for each person and each NPC, up to 5,000 characters.
    - Only that person ever sees their notes: not the DM, not other players.
    - Notes never go into transcripts, AI prompts, the story memory or a campaign backup, since
      anyone in the server may download a backup.
    - Each person can download all of their own notes, and delete them (Delete my notes).
    - They go when the person deletes them, or when the campaign is deleted. When an NPC is merged
      into another, its notes move to the one kept, the ones already marked "no longer listed" too: they keep
      their 30-day clock, and are unmarked only if the kept NPC is listed. When an NPC is rejected or deleted, the person's
      note is kept for 30 days in "Notes on names no longer listed", then deleted.
    - Notes are never logged, are kept to one campaign and one server, and are protected by
      row-level security.
  - **Plans:** the same as the NPC list (where the NPC list exists, its pages do too).
  - **Words:** "📖 Open" on the button. On the page: "My notes: only you can see these." The privacy
    page says what My notes keeps and who sees it.
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
  **The audio warning checks the transcript before it warns (owner decision 2026-10-08,
  #671, after a TV in the room set it off; #697 stage 1, #699 stage 2):** pieces shorter
  than the transcription minimum (by length as ears measured them, so a short answer
  that breaks up still counts) never count; among the rest, under 95% received and at
  least 2 s lost in a rolling 60 s window per speaker is the *trigger*: it starts a
  check of that speaker's lines in the window (the engine's per-word confidence first,
  with each engine's own limits: local Whisper's number is the chance it gave the words it
  wrote, `exp(avg_logprob)`, not mapped onto Deepgram's scale, #733;
  else one small AI call per speaker per minute on the cleaned text), and the ⚠️ shows
  only if they read garbled. Large losses skip the check: the #631 watchdog (sending,
  nothing heard) and under 50% received with at least 10 s lost in a minute warn at
  once. Until stage 2 lands, stage 1 warns directly at the DM screen's existing 90%
  (90–94% rarely costs words), not 95%. The end-of-session line keeps the raw numbers
  for the logs; the summary's "kept cutting out" follows the same rule. *Two more
  decisions (2026-10-08, #699):* when no check is possible (no AI key, or an engine
  without confidence and a middling read) the stage-1 rule warns on its own, with the
  skipped check logged; and a rule that fires while the minute holds no transcript
  line at all for that person counts as garbled once past the 2 s floor (an empty
  transcript while someone is clearly talking is the clearest sign there is).
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
  the channel still shows as unread. Misheard names are fixed since 2026-10-07 (#127);
  the channel has no `{entity}` labels (#53).
- The end-of-session message on the DM screen ends its facts with one line of name counts when any is
  above zero (#1510): "✏️ Names: DMbot fixed 3 by itself, asked you about 2 (1 not answered), you
  corrected 1, players suggested 2 (1 still waiting)." Counts of the session only; nothing is stored.
- **DMbot stops by itself when nobody is there** (owner, 2026-10-19, #2034). A forgotten session would
  keep billing listening minutes against the owner's hours. People only count: a bot in the voice
  channel (a music bot, DMbot itself) is nobody, by Discord's own bot flag, the same test the consent
  allowlist uses.
  - **The rules:** no person in the voice channel for 5 minutes (always on), or no person speaking or
    typing in table chat for 10 minutes. The DM sets this in ⚙️ Settings → Stop when quiet, a button
    that cycles 5 → 10 → 15 → 30 minutes; 10 is the default, and there is no Off (owner, 2026-10-21:
    a forgotten session must always end). Campaigns that had 60, 90 or Off move to 30, the longest
    choice; a 30 stays 30, and a backup holding an old value loads as 30.
  - **A warning first:** the DM screen says it will stop in 5 minutes, with a **Keep listening** button.
    Someone speaking or rejoining cancels the warning.
  - **The stop** is the same as ⏹ Stop: the transcript is saved, the session-end messages go out and the
    recap starts. The DM screen says why it stopped, and hours stop being counted.
  - **Everyone is told privately** (owner, 2026-10-21): when DMbot stops by itself, it sends a private
    message to the campaign's DM and to each person (never a bot) still in the voice channel, once per
    stop, saying it stopped listening and why. The DM's message also says where to change it:
    ⚙️ Settings → Stop when quiet. A person whose private messages are closed is skipped; the DM screen
    line still says why it stopped.
    *Built (2026-10-19, #2034; auto-stop private messages #2288):* when DMbot stops by itself it also sends the campaign's DM a private message with the reason and where Settings is, and each other person still in the voice channel a short one (anyone there, opted in or not: it is a notice, not capture; a closed inbox is skipped and only counted in the log). `idle_stop.py` holds the pure rule; the DM's choice is `campaigns.quiet_stop_minutes` (migration 0132; since #2288 migration 0166 makes the choices 5, 10, 15 and 30 minutes, default 10, with no Off, and moves any campaign that was on Off, 60 or 90 to 30); the warning's **Keep listening** button and any speech or rejoining cancel it, and the stop is the same as the DM's own.
- `/dmbot stop` posts the "Session ended" divider in the background, so it answers in
  time. Words still being written down when the session stops are lost (as before,
  #109); at shutdown, waiting lines are posted first.
- The 15-second capture checks now go only to the core log. The DM screen gets
  "⚠️ Mia's voice is cutting out for DMbot (82% got through)…" below 90%, once per
  person, again only if it gets 10 points worse or after 10 minutes (#134).
- The card mentions `/transcript` now that downloads exist (#125).

**Starting and stopping, as the DM sees it (2026-10-06, #107, #108).** The DM screen's
"✅ Listening in …" message names who in the voice channel is being recorded and who
isn't yet (DMbot is asking them privately), and a line follows for each yes that counts
("🎙 Mia said yes"), each stop, and each person joining (recorded or not), so it stays a
true picture of who is recorded. Only people at the table get these lines. The
message carries a **⏹ Stop listening** button (the same as `/dmbot stop`: this
campaign's DMs or a server manager; anyone else is told how to stop recording
themselves). Only the newest listening message has the button, it comes off when the
session ends, and it works after a restart. The help card says how to stop too. The
button asks first, privately ("Stop listening and end the session for **…**? [⏹ Yes,
stop] [Cancel]", for 60 seconds, tied to that session); the typed `/dmbot stop` stops at
once (#554).

**Writing speech down for several tables: built (2026-10-07, #173).** Each Discord
server has its own queue of speech (64 pieces; 256 across all servers bound memory when
the engine is down for everyone). `TRANSCRIBE_WORKERS` workers take turns between servers:
3 by default with Deepgram or cloud, and exactly 1 with local Whisper (one model on the
CPU; more would only wait). A server's speech is written one piece at a time and in
order, so its lines never swap, while a slow table can't hold up the others. When
writing stops, every table with speech waiting is told, and told again once it's steady
(3 answers in a row, or an answer and 30 s without a failure; #470), so a flapping
engine doesn't churn the DM screen. A table whose session starts during an outage is
told too. Deepgram's "wait" (Retry-After) is looked at again after each wait.
Each table's status shows its own backlog and delay.

**End of a session: built (2026-10-06, #109).** When the DM stops DMbot:
- Nothing said before the stop is lost: speech still being heard is closed off and
  queued, and the session stays "ending" until its own queued speech is written down
  (up to 2 minutes; other servers' backlog doesn't count, and the summary says so if it
  gave up). Then, in this order: save the last lines and end the stored transcript
  (private download messages), the last capture warning, the "Session ended" divider in
  the transcript channel, the summary, and the new-name suggestions. `/dmbot stop` still
  answers at once ("A short summary follows in the DM screen"); all of this runs in the
  background, and each step runs even if an earlier one fails. Pressing Stop recording
  during this time still drops that person's words, and a session started again right
  away never gets the old one's words. On shutdown the wait is cut short and saving
  comes first.
- **The summary** goes to the DM screen: "📋 Session ended: Frostmaiden", when it
  started (in each reader's own time) and how long it ran, who spoke and for how long,
  a ⚠️ line for anyone whose voice kept cutting out (under 90% got through), anything
  that went wrong (speech missed because DMbot fell behind, speech it couldn't write
  down, last words not finished), and where the transcript is. Names and numbers only,
  never anything that was said. Missed and failed speech is counted per session.
- Only `/dmbot stop` ends a session today; a resume that gives up after a restart has no
  summary yet. After a restart, speaking times count only from the restart (the "ran"
  time is the whole session). The AI recap of what happened is built: see "Session recaps"
  under Feature notes.

**Stored transcripts and downloads: built (2026-10-06, #41, #125).** Decided while
building:
- Each session has a row (kept after a restart: same campaign and start time) and each
  piece of speech from someone who agreed is a line, with `heard` (never changed) and
  `text` (cleaned; NULL while it's the same, that is when no name was fixed). Lines
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
  (`frostmaiden-session-7-as-heard.txt`): a short header, then
  `[0:42:10] (Mia) {Cerric}: …` per line (#53; no `{…}` for someone who plays no
  character, such as the DM, until speaker tagging). The file says it's what DMbot wrote down, with no fixes, and that some words
  may be misheard, and which speech-to-text wrote it ("Speech to text: Deepgram, an
  online service (model nova-3)"; #173). Each session stores its engines as "engine model host" (more than
  one if a resumed session switched); the endpoint's host stays in the database. While DMbot is still recording that session it warns first
  ([Download anyway] [Cancel]), in different words for the DM and players. Replies
  are deferred first, since building a file can take more than Discord's 3 seconds.
- When a session ends, the DM(s) and everyone recorded get a private message with one
  **[🎙 Download transcript]** button, which works after a restart and only for people
  still in the server. People with private messages off use `/transcript`.
- Until the cleaned download exists, there's only the "as heard" version. If saving
  fails at the start, the DM screen says there'll be no download for this session.
- **Delete my past transcripts is built (#1532):** 🗑 in the 🗂️ Menu on the consent messages, and
  `/dmbot delete-my-lines`. One warning with the numbers ("N lines in M sessions"), one confirmation, one
  transaction per server. What goes and what stays is in "Retention" below.
- **Keep transcripts for is built (#1533, migration 0101):** see "Retention". The cleaned version,
  both downloads and the `{entity}` labels for the DM's lines (narrating, or which NPC; #53) are
  built too; see "Transcripts and downloads" below.

**Transcripts and downloads (decided 2026-10-05, #124, #125).** Sessions and their
participants are stored. Each line is kept in two versions: **as heard** (exactly what
speech-to-text produced, never changed) and **cleaned** (after the Transcript Cleaner),
plus the list of fixes between them.
- **Live:** the cleaned lines stream into `#dmb-transcript-<short name>`, grouped into a
  message every few seconds (Discord allows a bot about 5 messages per 5 seconds per
  channel, and edits share that limit). Misheard names are fixed before a line
  appears (#127, step 1). Each session opens and closes with a divider ("── 🔴 Session started ·
  Oct 5, 7:30 pm ──", "── ⏹ Session ended ──"). Speaker names are bold, Discord's own
  message time replaces per-line times, and transcribed text can never ping anyone.
- **Late fixes:** messages from the last ~30 s are edited in place. Older fixes update the
  stored cleaned transcript and the downloads only.
- **When DMbot stops,** the DM(s) and every player recorded **in that session** get a
  private message: "The session for **<campaign>** has ended. Download the transcript:"
  **[📄 Cleaned]** **[🎙 As heard]** **[📄🎙 Both]**, with one line explaining each
  ("Cleaned: names spelled right, off-topic chat left out." "As heard: exactly what
  DMbot heard, word for word."). It's a shortcut: **anyone in the server** can get the
  same choice with `/transcript`.
  Before #296 it was one **[🎙 Download transcript]** button (old ones still work).
  Later the message may add "If the DM fixes names later, download again for the updated
  version." Today a DM's answer re-fixes the earlier lines of the session while it runs
  (see "Transcript Cleaner step 3"); a correction made after the session ended applies to
  later sessions only.
- **The cleaned file starts with a note:** "DMbot fixed the spelling of some names. The
  'As heard' file has the exact words."
- **Downloads use the transcript format** `[0:42:10] (Mia) {Cerric}: …` with time
  since the session started (owner decision, 2026-10-07, replacing the readable labels
  `0:42:10 Cerric (Mia): …`), and a header line explaining it.
- Transcripts never contain DM-screen content. The Cleaner never **adds** a secret
  identity to a line; the "as heard" version contains only what was actually said.

**Transcript format (decided 2026-10-04).** One line per utterance:
`[timestamp] (Discord name) {entity}: text`, where `{entity}` is the in-game character,
an NPC or other in-game entity, `{narrating}` for the DM, `{table_talk}`, or
`{non-game_content}`. DM sidebar lines (2026-10-09, see "DM sidebar") go into the raw
transcript only, tagged `[DM Sidebar]`, with DMbot's in-game replies under `DMbot`; never
into the cleaned transcript.
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
  the cleaned transcript (skipping lines labeled off-topic) that proposes new names for
  the DM to confirm.
- **Names heard during play (#394):** the scan reads the cleaned line, so a known name
  misheard and fixed live is never suggested as new. **Exact matches are never
  suggested; near matches come with the match pre-filled; DMbot never merges by sound
  on its own.**
  - **Near:** sounds like a confirmed name and is spelled at least 0.8 alike (one word:
    0.9, as in the Cleaner); never a secret name; when two known names fit about as
    well, it's offered as new. It's worked out when the DM opens the review (one small
    query for names sharing its sound codes), never saved: names can change, or be made
    secret, after the session. The saved note is only "Heard 3 times".
  - **The review** then reads "📝 **Rothgr** · heard 3 times / Sounds like **Hrothgar**.
    The same, or new?" with **✅ It's Hrothgar** · **➕ New name** · **🔗 Another known
    name…** on the first row and **🚫 Not a name** · **⏳ Later** on the second (so a
    phone never cuts the labels; the same two rows without a match). "It's" merges it
    as an other name in one change (with the usual guards), so one undo takes it all
    back.
  - **Near-duplicates heard in one session** ("Oskar Vane" and "Vane") are one question,
    grouped before the cap of 10. The shorter is shown ("Also heard as **Vane**: saved
    with it."), and confirmed or turned down with it; every note names all of them. A word that fits two names ("Lord" in "Lord Neverember" and
    "Lord Dagult") stays its own question; one-word names fold only at 0.9.
  - **No auto-accept setting.** Nothing is added until the DM presses a button.
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
  Secret names are never sent. The in-memory copy follows changes live. (This fixed
  order is a placeholder: hints follow the scene next, see below.)

**Names at scale and hints that follow the scene (decided 2026-10-06, #126, #127;
reviewed in #203).** A long campaign has hundreds or a thousand names, so neither the
names panel nor the speech-to-text hints can be a fixed list.
- **Who and which campaign, everywhere below:** only the campaign's DMs and server
  managers, in private replies. The campaign is the one being played, otherwise the
  DM's only campaign, otherwise DMbot asks which (the type-ahead can't ask, so it
  suggests nothing until that's settled). Every action is checked again when it's used
  (server and campaign), never trusted from a button or a typed value. **Secret names
  and secret connections are shown, searched and downloaded only for the campaign's
  DMs:** a server manager who isn't one of its DMs gets the panel, cards, browsing and
  download without them (they may be at the table).
- **All names on the website** (owner, 2026-10-21). The "✏️ Fix, change or remove a name…" menu shows
  at most ten names a section (Discord's limits), so its **last choice is "🌐 All names on the website…"**.
  Discord can't open a link from a menu, so choosing it answers privately with one **🔗 Open all names**
  link button. The same last choice ends every other name menu that is cut short (Browse by kind, the name
  card's lists). The page, **Names** at `/account/names/<server>.<campaign>`, is the whole list made for
  a big screen: search as you type, filter by kind (people, places, factions, items, events…), sort by
  name or newest, a count per kind, and paging; never a fixed cap.
  - **Who sees what:** the same rule as the names panel above. The campaign's DMs see every name, secret
    ones marked 🤫, and can act; a server manager who isn't one of its DMs sees no secret names; anyone
    else is sent to the NPC pages, which already show the players' view.
  - **What a DM can do there**, each the same action as the name card in Discord, recorded the same way
    so **↩️ Undo** on the Discord card still works: fix the spelling, change the kind, add or remove other
    names and nicknames, mark or unmark secret, merge two names, and remove a name (one confirmation).
    An NPC's row also links to its NPC page.
  - **How:** the website's role still has no right on the memory tables. It reads and writes through
    database doors scoped to one campaign and checked against the signed-in DM on every call, like the
    "said as" spelling (#1451). Logs hold counts, never names.
- **The name card's connections, one per line, with a dropdown** (owner, 2026-10-21). The card reads:
  ```
  Jack Uzi · character · NPC · not heard in a session yet
  🤫 Secret: Jack Uzi (hidden from players)
  Connections:
  • is the boss of Sss'thlix 🤫
  • is a member of The Thieves' Guild
  ```
  Each connection is its own line. Only a secret one carries 🤫; a known one has no icon. Discord can't put a
  button on a line of text, so there is one **✏️ Edit a connection…** dropdown listing each connection
  in the same order and words (🤫 on secret ones). Choosing one opens that connection's choices: change the
  words, mark secret or known, or remove it (one confirmation). Each is the same action and Undo as today.
  More than 25 connections: the dropdown's last choice is "🌐 All connections on the website…".
  **Where it is** (Supervisor, 2026-10-22, #2414): in the card's 🧭 Connect to… screen, not on the card itself,
  because the card's five rows are full (three rows of five buttons, a thread's buttons, and Show all ▾).
- **The relationship web on the website** (owner, 2026-10-21): a graph of names (nodes) and connections
  (edges) at `/account/web/<server>.<campaign>`.
  - **Centre and distance:** pick the centre from a searchable list of every name; the graph shows
    everything within **N hops** (default 2), set with a spinner or by typing 1 to the campaign's largest
    useful number. If the chosen N would draw more than about 300 names, it warns first ("This will be
    slow; show it anyway?"). Clicking a name makes it the centre and redraws.
  - **Edges:** between two names, one line with the most relevant connection's words and "(+n)" for the
    others ("works with (+3)"). Most relevant: the DM's latest confirmed connection, never a secret one
    when a known one exists. Clicking the line lists all of them, each secret one marked 🤫.
  - **Editing in place** (the campaign's DMs only): rename or change a name's kind, add, change or remove
    a connection, mark secret or known, the same actions and Undo as the Discord card.
  - **Who sees what:** DMs see everything, secret ones marked. Anyone else in the server sees the players'
    view only (what the NPC pages show) and cannot edit. Secret names and connections are never sent to
    them.
  - **Players View / Full View, and revealed names** (owner, 2026-10-22). The graph shows players only
    what the story has **revealed** to them.
    - **Revealed** means said at the table: a line heard in the table voice channel, or typed in the
      table's chat that DMbot reads ("Typed chat at the table"), while DMbot listens and only from people
      who agreed. A past session the DM imports counts, because it is what was said at the table. DM-screen
      lines, the DM sidebar, DMbot's own messages and the DM's private channels never count.
    - **Not revealed yet** (new, besides 🤫 secret): every name and connection from 📜 Upload backstory, and
      every name the DM adds by hand (➕ Add a name, 📥 Add many), starts unrevealed. Names DMbot first
      heard at the table start revealed. Existing names and connections that aren't secret count as
      revealed (no change for players today).
    - **When it changes, by itself:** an unrevealed name becomes revealed when its name or one of its other
      names is said or typed (the whole-name matcher that guards secrets). An unrevealed connection becomes
      revealed when both its names are revealed and the session states it; that step comes with the knowledge
      map review (#2410). Until then the after-session report points the DM to connections whose names are both
      revealed (Supervisor, 2026-10-22, #2451). The DM can also press **👁️ Reveal
      to players** on a card or in the graph.
    - **🤫 Secrets said out loud are revealed too, and the DM can recant** (owner, 2026-10-22, replacing "🤫 is
      stronger"): after the session, the **knowledge map review** lists "Secrets exposed" with **↩️ Recant**
      for a slip, a misheard word, or a name the DM will change to keep the mystery. A player's **guess** is
      never a reveal: it becomes a theory, drawn as uncertain. Red herrings and lies are beliefs the truth
      disagrees with. See docs/STORY_MEMORY.md section 9, "Assertions, perspectives and evidence".
    - **Revealed is a belief, not a flag:** players see what **The party** believes (theories marked "?"), and
      Full View shows **The truth**. "Revealed" decides only what players see; every listener still uses
      every name.
    - **The same rule everywhere for players:** the graph, the NPC pages and the All names list show players
      revealed names and connections only. Code decides it, never the AI, and nothing unrevealed is sent to
      a player's browser.
    - **How it is stored** (CloudDev, 2026-10-22, #2404 slice 2): a name is revealed when the players may
      know it, which is the "Players don't know this one yet" switch (`hidden_from_players`, #2321) turned
      off; names from ➕ Add a name, 📥 Add many and 📜 Upload backstory start with it on. A connection is
      revealed when The party believes it (STORY_MEMORY section 9); `memory_relations.unrevealed` (default
      off, so every connection that exists is revealed) keeps the party from believing a backstory
      connection until the DM presses **👁️ Reveal to players** (both its names must be revealed first).
      Every players' reader already looks at those two, so none changed. **Unrevealed is not secret for the
      listeners** (Supervisor, 2026-10-22): the cleaner and the DM sidebar use an unrevealed name like any
      other; only a 🤫 spelling or a clue only some know keeps the secret treatment. The recap may use an
      unrevealed name only when the table said it in that session's lines (the recap writes what the party
      knows); otherwise it counts as a name not for players. Full View marks `revealed` on each
      node and connection. **After each session** (and each imported session) a held-back name that a
      recorded person said or typed at the table, by its name or another of its names as whole words, is shown
      to the players in one change; the DM's own table speech counts, the sidebar, DMbot, people who stopped
      and the 🤫 spellings never do (a secret said aloud is the knowledge map review's question, #2410). The DM
      screen says "Revealed at the table: A, B (Session N)" with ↩️ Undo. Connections are not revealed by this
      step: until the knowledge map review (#2410), the after-session report points to the held-back
      connections whose names are both revealed now.
    - **📌 100% truth** (CloudDev, 2026-10-22, #2447, migrations 0190 and 0193): the DM's own word that a connection is
      true in the story; nothing else is 100%. A DM presses 📌 Pin as 100% truth on a connection's card
      (Unpin takes it back), or answers once per lore upload, "may change in play" (default) or "📌 100% truth", and
      the connections kept from that upload are pinned. It is a `pinned` flag on the truth belief
      (`memory_beliefs.pinned`), so Undo, backups and isolation work as for any belief; the web graph
      shows it to a DM in Full View only. After a session, a claim that clashes with a pinned connection
      is a heads-up even before the campaign has enough story for the others, worded as a "Check" line
      with the line; the DM screen says only how many. Code finds it with the same rules as the other
      heads-ups, never the AI, and DMbot only points: the DM decides. On the website the pin is the
      `link-pin` action of the relationship web's edit queue (migration 0193), DM only and checked again by
      the bot, like the other link actions.
    - **The DM's switch: Players View / Full View.** Full View (the default for DMs) is today's view: everything,
      with 🤫 on secret ones and a dimmed "not revealed yet" look on unrevealed ones. Players View shows the
      DM exactly what a player sees, read-only, so the DM can check before sharing a screen.
    - **The player's switch: Normal view / Peek behind the DM screen.** It follows the campaign's DM-screen
      visibility:
      - **Players can peek** or **Everyone in the server:** the switch works. Turning it on first shows:
        "Peeking shows you the DM's secrets and things your characters haven't found yet. It can spoil the
        mystery and the fun. Your DM will be told you peeked. [Yes, show me] [Cancel]". Yes shows the full
        view, read-only, and tells the DM once on the DM screen ("<name> peeked at the relationship web").
        Normal view hides it again. The choice lasts for that browser visit only.
      - **Only the DM:** the switch is shown greyed out and can't be turned on, with: "Turned off by the DM.
        To turn it on, the DM opens 👤 DMbot Main Menu → ⚙️ Settings → 👁️ Who can see the DM screen →
        👀 Players can peek."
      - The API re-checks the visibility and the person on every call; the switch alone never unlocks
        anything.
  *Built (#2404, slice 1, migration 0186):* `GET /me/campaigns/<ref>/web?view=players|full` (and `/web/size`).
  The door decides on every call: a DM gets Full View unless they ask for `players` (read-only); anyone else
  gets the players' view unless they ask for `full` and the campaign's DM-screen visibility is `peek` or
  `open`, else 403 `peek_off`. The answer carries `view`, `visibility` and `canPeek`. `POST .../web/peek`
  (after the page's warning) records the visit; the bot posts "<name> peeked at the relationship web" once
  for each visit on the DM screen. Revealed names and their automatic changes are slice 2.
- **Merging names, the easy way** (owner, 2026-10-22, after the NPC list showed one creature under four
  spellings). Today a merge is one pair at a time (🔗 Same as… on a card, typing the other name). Three
  easier ways, all the same merge underneath, so the kept name collects the others as "Also called", their
  connections, notes and mentions move to it, and **one ↩️ Undo** reverses the whole merge:
  1. **🔗 Look-alikes:** DMbot groups names that look or sound alike (spelling, sound, a shared word, "the"
     dropped), in code, never by AI. The names overview gets **🔗 Look-alikes (N)**, and the after-session
     knowledge map review lists new ones. Each group is one message: "These may be one name:" and a menu with
     every name in the group already ticked, so the DM unticks any that are different. Then **Keep which
     name? ▾** (the most-used one first), then **🔗 Merge** with one confirmation. **Not the same** stops that
     group from being offered again. DMbot never merges by itself (two confirmed names are never merged
     automatically).
  2. **🔗 Merge several…** on a name card: a menu of up to 25 names (search first if there are more) to fold
     into this one, in one go.
  3. **On the website's All names page:** tick names, then **Merge selected**, pick the name to keep, and
     confirm.

  A merge of different kinds (a place into an NPC) asks once more. A secret name merged into a known one stays
  a secret other name. Nothing is deleted: the merged names are kept as merged, so Undo is exact.

  *Built (#2426, migration 0183):* `MemoryStore.merge_many` joins up to 50 names into one in a single write,
  all or nothing, so one Undo reverses it. The finder (`memory/lookalikes.py`) joins names that are the same
  letters (spaces, hyphens and a leading the/a/an ignored), a letter or two apart (one up to 8 letters, two
  beyond; never below 5 letters, never when a number or a one-letter last word differs, the linker's rule),
  sound alike (names of 5 letters or more), or one inside the other as whole words ("Belleros" and
  "Belleros the Elder"). "Not the same" is kept per pair in `name_not_same` (the DM's alone, not in backups,
  gone with either name). 🔗 Merge several… sits on the Same as… screen, because the card's rows are full. The
  website's door is the all-names action `merge-many {others[]}` on the kept name.
- **The recap posting clock** (#2435, migration 0184). A recap job records `posting_since` (whole Unix seconds),
  `posting_step` (`check`, `post`, or `audio` while narration is made) and `posted_url` (the first message's
  Discord link). A database trigger starts the clock at `check` when the status becomes `posting` and stops
  it when it ends (posted, put back as a draft, dropped), so every writer of the status is covered; the bot
  moves it to `post` once its checks pass; the audio worker runs the `audio` step only when no post is under
  way. The web API's recap answer carries `postingSince`, `postingStep`, `postedUrl` and `serverNow`.
- **Where everything is on the website** (owner, 2026-10-22, the account and campaign pages reorganised; it
  builds on "A page for each campaign" below). One rule: **everything about one campaign lives on that
  campaign's page; everything about you, or shared across campaigns, lives on My Account.** Every page shows
  where it is (a breadcrumb: My Account › <Campaign> › Story graph) and the same campaign tabs, so nobody is
  lost.
  - **My Account** (`/account`), top to bottom:
    1. **My campaigns:** one card per campaign. The **campaign's name is the link** to its page, and the card
       shows its facts at a glance: 🟢 when DMbot is listening there right now; your part (You run it, You help
       run it, You play in it); sessions; hours played; players; NPCs; started; last played. The DM tools are
       not on the card; they are on the campaign's page.
    2. **My characters.**
    3. **Shared rules:** the one place for house-rules sets and homebrew packs you publish or follow, at
       `/account/sharing`.
    4. **My plan and billing.**
    5. **My account:** email, DMbot ID, servers, sign-in.

    The account-wide House rules, Homebrew and Transcripts cards go: each lives on its campaign's page.
  - **The campaign's page** (`/account/campaigns/<server>.<campaign>`), the campaign's home. The heading has
    the name, 🟢, your part and the facts. Below it are four groups, in this order, and the same four are the
    tabs on every page of the campaign:
    1. **Story:** Recaps · Story timeline · Story graph · Names and NPCs.
    2. **At the table:** Transcripts · Import a past session.
    3. **Rules:** House rules · Homebrew. Each of those pages links to **Shared rules** for sharing them.
    4. **Settings** (the campaign's DMs only): who can see the DM screen (peek) · how much DMbot says ·
       transcript retention · game masters · hand over · pause · delete. These are the same choices as
       ⚙️ Settings in Discord, with the same words. Players see the groups they may use.
       **Changing them from the site (#2463):** who can see the DM screen, how much DMbot says
       (Quiet or Normal) and transcript retention are changed through the website's action queue
       (`setting_actions`, the same pattern as the All names page). The DM is checked when the choice is
       queued and again when the bot applies it, and the bot applies it exactly like the Discord button:
       the same store calls, the channel permissions for visibility, the notice in the DM screen and the
       same words back to the DM. A shorter transcript limit deletes old sessions, so it needs the DM's
       yes sent with it, as Discord asks first. Only a campaign's DMs may use it (Discord also lets a
       server manager change visibility; the site is narrower). The logs hold counts and outcome words,
       never the choice. **Words the page shows** (UX review): while it waits "Saving…", and after a
       while "This is taking longer than usual"; a choice gone after an hour reads "That choice timed
       out. Try again." The answer's `note` (Discord's own words, which may carry `**bold**`) is shown as
       it is under the page's own lead-in, and a refusal reads "Discord didn't allow this change:" then
       the note. `not_allowed` → "Only this campaign's DM can change this. Ask them, or use ⚙️ Settings
       in Discord."; `not_found` → "DMbot can't find this campaign any more."; `needs_confirm` → the
       page shows Discord's own deletion warning (`transcript_keep.warning_text`) with "Delete after N"
       and "Keep as it is", and queues nothing until the DM says yes; `failed` → "Something went wrong.
       Try again."; `bad_value`, `bad_setting` and `too_soon` → "That choice isn't available. Reload the
       page and try again." and "Slow down a moment." DM-screen settings are never shown to players.
       Game masters, hand over, pause and delete stay in Discord: the page says "Do this
       in Discord: 👤 DMbot Main Menu → ⚙️ Settings → 🗂️ Campaign management → …".
  - **Story timeline:** the campaign's sessions in order, each with its number, date and length, its recap
    (linked) and what was revealed at the table that session. It's built after the other pages.
  - **Words:** a page is called by the same name everywhere: in Discord, on the tabs, in the breadcrumb, and on
    the account page.
- **A page for each campaign** (owner, 2026-10-22). On the account page, each campaign in **My Campaigns**
  is a link to `/account/campaigns/<server>.<campaign>`. That page brings together everything that belongs
  to that one campaign:
  - its recaps (the recap list, showing only this campaign's);
  - its house rules and its homebrew;
  - its story map (the relationship web);
  - its names and NPC pages;
  - its transcripts and importing a past session.

  Each link opens that page already on this campaign. Every campaign keeps its own house rules, homebrew and
  story map, as the isolation rule says. A **← My Campaigns** link goes back to `/account#campaigns`.
  Players see only the links they may use (transcripts, NPC pages, the story map's players' view); the DM's
  own tools show only to the campaign's DMs. The API decides, not the page.
- **`/dmbot names` becomes an overview and a search, not a list.** Discord allows 2,000
  characters per message, 25 choices per menu and 5 fields per form, so:
  - **Overview, about 15 lines:** "🧠 **412 names**: 212 NPCs, 87 places, 41 groups, 72
    other" (top 3 kinds, then other), the waiting check, "Heard last session:" up to 10
    names and "… and N more", "Added lately:" up to 10 (no repeats). An **Open a
    name…** menu lists the names shown, for a two-tap jump to a card.
  - **Buttons:** row 1 **🔍 Find a name**, **➕ Add a name**, **📝 Check new names (N)**;
    row 2 **🧑 Add a player's character**, **📚 Browse by kind**, **📥 Add many** /
    **📤 Download all**.
  - **🔍 Find a name, two ways:** the button opens a one-field form ("Type part of a
    name, a nickname, or how it sounds"): one match opens its card, otherwise a menu of
    up to 25 with **Search again** and **➕ Add it as a new name** ("No name like
    **Xyz**." if none). And `/dmbot names find:` with Discord's type-ahead: with
    nothing typed it offers the 25 names said most recently; each choice reads like
    "Belleros · NPC (matched "Bell")" and carries the name's ID, not its text.
    **The type-ahead runs the same DM check as the panel and suggests nothing to anyone
    else** (Discord runs it for anyone who can see the command, peekers included).
    Secret names appear only to the campaign's DMs, marked 🤫. It answers from the
    in-memory copy, never a database query per keystroke; copies loaded only for
    searching expire after a while.
  - **Name card**, under 2,000 characters, each section about 3 entries then "… and N
    more", and one **Show all ▾** select under the card for the cut sections: what it is, last heard, **Also called**, **🤫 Secret**,
    **Connections**. Buttons: **✏️ Fix spelling** (one field, filled in), **Edit other
    names** (each: ⭐ make it the main name, 🤫 make it secret or not, ✖ not this
    name, which also undoes a wrong **Same as**), **Add another name** (with a secret-name field, as when adding
    a name), **Same as…**, **🧭 Connect to…**, **Change what it is**, and **Remove**
    last, in grey. **Remove asks first and can be undone:** "Forget **Belleros**?
    DMbot stops listening for it and its other names (Bell, the old knight). Past
    transcripts don't change. [Forget it] [Keep it]", then "Forgot **Belleros**.
    [Undo]". Its other names, secret names and connections go with it, and come back
    with Undo (which keeps working after a restart, like DMbot's other buttons); a
    player's character loses its link to the player the same way.
  - **📚 Browse by kind:** pick a kind, sort by **Last heard** (default) or **A–Z**,
    "Page 3 of 11" with ◀ ▶, up to 20 names a page (fewer if lines are long), and a
    menu of the page's names to open a card.
  - **📥 Add many / 📤 Download all:** one name per line, `name | kind | other names`,
    with other names separated by `;` and the kind optional, in plain words (NPC,
    place, group, creature, item, god, spell, event, other). Paste up to about 200
    names (a form holds 4,000 characters) or upload a file (UTF-8, up to 256 KB and
    2,000 lines; each name up to 100 characters; at most 20 other names and 20 secret
    names on a line, decided 2026-10-08 on #598 so a worst-case file stays bounded, a
    line over it being refused with its number and "split them"; and, decided the same
    day on #598 because the per-line cap alone leaves one name able to gather tens of
    thousands of other names across lines, **at most 50 other names and 50 secret
    names per name and 5,000 other and secret names per upload**, counting only what
    the upload adds so a Download all file always uploads again). **A campaign too big
    for one upload (over 2,000 lines or 256 KB) downloads as several files** (decided
    2026-10-09, #685), named `names-01-of-12-…` (the number first, padded so they sort): each is within the
    limits above counting its `#` notes, holds whole names (a name on several lines is
    never split), starts with the instructions and a line saying it is one of N files that
    can each be added on its own, in any order; the
    ten files Discord takes in a message go ten to a message. **Names only:** lines with
    descriptions or other columns are refused as unclear, and DMbot never offers
    ready-made sourcebook name lists (IP rule). It writes only into the chosen
    campaign, through the normal memory rules (checks, change log), **saved in one go**
    (batches of about 200 for a big file, with live transcription reloading its names
    once, after the last batch). Imported names count as confirmed (the DM gave them);
    a line needs a look when its kind is unclear or it sounds like a different known
    name. Then a summary: "Added 260 names. 40 need a look. [Check them now] [Later]";
    one **Undo** removes the whole import, every batch. **Duplicates (decided 2026-10-07,
    #369): exact matches fold in quietly, near matches ask, DMbot never merges by sound
    on its own.** A line whose name or other name DMbot already knows is the same name:
    its new other names are added, its kind is left as it was, and a swapped line
    (`Frostmaiden | god | Auril` when Auril is known as the Frostmaiden) changes nothing
    and says so. A close spelling (likeness 0.9, or the same sound at 0.8; one-word
    names 0.9 only) is saved as a proposed name and asked about after the import, in one
    grouped message ("Aurill → Auril?" Same / Different / Remove, with "Same for all"
    and "Different for all"); unanswered ones wait in Check new names. Matching is
    bounded: a sound shared by more than 64 known names (`CROWDED`) isn't compared name
    by name, and such a line lands in Check new names unchecked, the safe direction. A kind that differs on an exact match
    is asked the same way, and kept as DMbot has it when ignored. Repeated lines in one
    list pool their other names. Non-DMs matching a secret name see no hint of it.
    Both kinds of question come as their own messages right under the summary, since
    one message holds only five rows of buttons; the summary keeps the kind menus and
    Undo, which also takes back the other names added to known names.
    The download is a file, sent privately:
    "This file includes secret names. Don't share it with players." A campaign holds
    up to about 10,000 names.
  - Nothing is deleted by itself. The after-session check stays capped at 10
    suggestions a session, link suggestions included.
- **Campaign lore, and one way in for names** (owner, 2026-10-22, replacing the separate backstory and
  "names from any document" paths for prose).
  - **The name:** 📜 Upload backstory becomes **📜 Upload campaign lore** (the same place in the 👤 DMbot Main
    Menu).
  - **One process for prose.** Whatever arrives through 📜 Upload campaign lore or 📥 Add many (a pasted text, a
    file, a link):
    - **A strict names list** (`name (pronunciation) | kind | other names`, one per line) is added straight
      away, with no AI. **The bracket after a name** is how to say it only when code judges it a respelling
      (#2447, built 2026-10-23): after dropping hyphens, stress marks and capitals its sound key is close to
      the name's, and it holds no plain English word ("the", "of", "a", "an", "and"…). `Tavin (TAV-in)` and
      `Ka'zeth (kah-ZETH)` keep the bracket as the name's said-as (#1451). Anything else is an aside, kept as
      another name: `Tavin (the rogue)` gives "Tavin the rogue". A near miss (`Jorvik (YOR-vick)`) asks the DM
      once after saving, "Is (YOR-vick) how to say **Jorvik**?" with [It's how to say it] and [It's another
      name]; nothing changes until they answer. A bracket is accepted without asking only when it
      also looks like a respelling (a hyphen, a stress mark or a shouted syllable: `TAV-in`, `kah-ZETH`); a plain
      look-alike (`Kaela (Kael)`) is asked. A name the campaign already has with its bracket (a Download all
      writes `Fenn (Lower Quay)`) stays whole when uploaded back. The first line that gives a pronunciation decides.
    - **Anything with prose in it** goes through the same single AI read as campaign lore: names and the
      connections the text states, each with its quote. Code keeps a connection only when its quote is in the
      text and both names are in this read or the campaign. **Nothing is invented:** no AI (PlotBot included)
      adds a connection the text doesn't state. The DM answers each one: ✅ Keep, 🤫 Keep as secret, ❌ No.
  - **How the read works** (owner, 2026-10-22, after a long document's answer was cut off, #2454): the whole
    text is read at once (the models read hundreds of pages). **Pass 1** (the careful model) writes the names
    and the relationships the text states, each with its quote, as a plain list, with output room sized to the
    text and continued if needed. **Pass 2** (the fast model) only reshapes that list into the ingestion
    format, never adding. Code then checks every quote against the original text. DMbot never says "no names
    found" when an answer was cut short.
    *Built (#2454, migration 0191, `memory/lore_read.py`):* pass 1's room is about an eighth as many tokens as the
    text has characters (at least 3,000, at most 8,000 and never above the model's own output limit in
    `ai.MAX_OUTPUT_TOKENS`); a cut answer is asked for again with twice the room, and one still cut is continued
    from the list so far (up to 3 follow-ups). Pass 2 shapes the list 50 lines at a time and halves a slice whose
    answer is cut. Pass 1's prompt carries two worked examples (a constant, `EXAMPLES`, pinned by a test); a rumour
    goes under NOT INCLUDED and is never a relationship. Pass 2 maps each relationship onto the fixed kinds or the
    campaign's own, or `other` with its words: the catch-all `associated_with`, labelled with those words
    (`backstory_links.detail`). When nothing could be read before the cut, the DM is told so, never "no names".
  - **📌 Asserted or 100% truth:** the lore upload has one switch, **Asserted** (the default) or **📌 100% truth**.
    Kept connections from a truth upload are pinned. Either way, everything uploaded starts not revealed to
    players.
  - **Pronunciation in the list** (`Tavin (TAV-in) | NPC | Mo`): the bracket after a name is read as how to say
    it only when it looks like a respelling of that name. Code decides, never the AI: its letters (hyphens,
    stress marks and capitals ignored) sound close to the name, and it holds no plain words like "the", "of" or
    "a". Otherwise it's an aside, kept as an other name with the name in front: `Tavin (the rogue)` gives
    Tavin, also called "Tavin the rogue". A near miss asks the DM ("Is (Kael) how to say Kaela?" [It's how
    to say it] [It's another name]). A pronunciation goes to the name's "said as" (#1451).
  - **📌 In the story graph:** the DM can pin or unpin any connection as 100% truth (DM only, with Undo).
  - **Off the rails:** after a session, a claim that contradicts a 📌 truth gives the DM one private DM-screen
    notice with its line. Nothing is said when nothing is pinned.
- **Connections from a backstory** (owner, 2026-10-22). 📜 From a story or background also suggests
  the connections the DM's text states ("Vex is the boss of the Ashen Hand"), in the same AI read
  (no extra call). Each comes with a short quote. Code keeps a connection only if its quote is in the
  text, both ends are names from this read or already in the campaign, and its kind is one of the
  fixed core kinds above (or the campaign's own definitions). After the DM has answered the names, the
  connections are offered one at a time with their quote: **✅ Keep**, **🤫 Keep as secret** or **❌ No**.
  A kept one is confirmed (`source="dm"`, as with Connect to…), and it shows on the name cards and the
  website graph, with ↩️ Undo. A connection to a name the DM said no to is dropped. Nothing is saved
  without a press, and the text is never kept.
  **Where it is** (owner, 2026-10-22): a **📜 Upload backstory** button in the 👤 DMbot Main Menu (for the
  campaign's DMs only), opening the same flow, so a new campaign's DM finds it straight away. ⚙️ Settings →
  Names → 📥 Add many → 📜 From a story or background stays too.
  **How they wait** (CloudDev, #2380, 2026-10-22): a found connection waits in the campaign's
  `backstory_links` table (migration 0173): the two entries' IDs, the kind, a short quote and a secret
  flag, never the text. It is asked only once both names are confirmed (after ✅ Add all, at the end of
  📝 Check new names, or with 📜 Check the connections), so a restart loses nothing. Every read cleans
  the queue: a refused name drops its connections, a merge moves them to the name that stayed, and a
  connection the campaign already has is not asked again. One the text wrote with a secret spelling can
  only be kept as secret.
- **Connections between names.** Hints and helpers need to know that Ulfgar belongs to
  the Frostwolf tribe. They come from the DM (**🧭 Connect to…** on a name card) and
  from DMbot suggesting them after a session, with what was heard ("**Ulfgar** seems to
  be part of the **Frostwolf tribe** (heard: "Ulfgar, chief of the Frostwolves").
  Right? [Yes] [No] [Later]"), only from speakers who still agree to be recorded. A
  connection reads as one sentence, and the other name's card shows it the other way
  round ("Frostwolf tribe: members include Ulfgar"). The plain words map onto the fixed
  core: lives in / is in (`located_in`), is a member of (`member_of`), is a friend or
  ally of (`ally_of`), is an enemy of (`enemy_of`), is family of (`kin_of`), owns (`owns`),
  knows (`knows`), works for (`serves`). "Leads" isn't in the core; it would be added
  in a code release (character → faction) if live use shows it's needed. `appears_in`
  isn't offered to the DM. Connections never use 🔗 (that's **Same as**). **Only
  connections the DM confirmed are used**, in hints and anywhere else, **and secret
  connections never feed hints**: today's lookup also loads suggested ones, so step 1
  adds a map of confirmed, non-secret connections.
- **Hints follow the scene** (replacing the fixed order). Hints go with every piece of
  speech sent to speech-to-text, so they can change clip by clip. Up to 50 per clip
  for Deepgram (fewer when names are long; local Whisper keeps about 600 characters),
  taken from the front, so the order is what matters. **Never secret names, and a
  match on a secret name adds nothing:** saying "the hooded stranger" must not pull
  Belleros, or anything connected to Belleros, into the hints.
  1. **Always:** the players' characters and the display names of the people who agreed
     **and are in the table's voice channel** (players call each other by name), each
     capped at a few nicknames. People who agreed but aren't there go **last**, after
     every campaign name, so a big server's members never crowd out the scene (#173).
     Who's there is looked at every 5 seconds per server; anyone who stops being
     recorded is gone from the very next clip's hints.
  2. **The scene:** confirmed names said in about the last 10 minutes, newest and most
     said first. Each written-down line, only after the per-line consent check, is
     matched against the campaign's non-secret names (exact names, other names and
     "fixed" spellings; "keep as heard" is skipped; no sound-alikes, which would match
     almost anything). A name said once fades after about 10 minutes; when someone
     stops being recorded, their lines stop counting. To keep a hint from feeding
     itself (a hinted name is more likely to be written down even if it wasn't said),
     a name stays in the scene past 10 minutes only after two mentions or a second
     speaker.
  3. **The tip of the tongue:** names connected to the scene's names by confirmed,
     non-secret connections, ranked by how strongly the scene points at them (each
     scene name pointing counts, weighted by how recently it was said). If the table talks
     about the Frostwolf tribe, its chief's name is already a hint before anyone says
     it.
  4. **Recently:** confirmed names from the last session or two.
  5. **Fill:** the most-heard confirmed names, then suggested names (a guess never
     pushes out a real name). Whether to fill every slot or stop after the scene is a
     setting the live tests decide, with fill as the default.
  How it's built: the scene lives with the running session, in memory, per campaign,
  cleared when the session ends; hints are worked out per clip from memory (no
  database query per clip), with the fixed tiers prepared once per change to the
  names. How often and when each name was heard is written once at the end of the
  session (the `memory_mentions` table), from the lines of people who still agree to
  be recorded when it's written (checked then, after any wait), kept in backups and
  removed with the campaign (and with a person's lines, see Retention); writing it
  never triggers a reload. No AI call: it's instant and free. A
  later, optional layer could ask an AI every few minutes where the scene is heading,
  only if tests show the connections miss too much. Names unsaid for months drop out
  of hints until they're heard again; names never heard yet (freshly added or
  imported) count as recent, so a prepared NPC list helps from the first session. Deepgram refusing a list that's too long is
  handled separately (#209). Hints are never shown to the DM or players.
- **Order of work:** (1) a line matcher (finds names in a written-down line: groups of
  1–4 words, exact and "fixed" spellings) and the scene-based hints, ahead of the
  Cleaner, which will reuse the matcher; (2) the name card, find and **Connect to…**;
  (3) browse by kind, add many and download all.
- **Step 1 built (2026-10-06, `dmbot.memory.scene`):** the line matcher and all five
  tiers. **How often names were said is kept as counts, not per mention** (changed
  from the `memory_mentions` idea after review measured a year of per-mention rows at
  ~1M rows and ~400 MB per campaign, with reloads reading them all): `memory_heard`,
  one row per name, session and speaker, counting lines that named it; written once
  at the end of a session from people who still agree (checked again right before
  writing); observations, not edits, so no undo entry, no reload (the session's copy
  is dropped so the next one sees them), and Undo of adding a name still works (its
  counts go with it). A merged-away name counts for the one it became. "Recently" is
  this session, then the last two sessions, then names never said yet that were added
  since then (newest first); fill is most said first (names unsaid for about six
  months drop out), then older names never said. Lines from someone who stops being
  recorded stop counting at once, and the speaker is kept so their counts can be
  removed with their lines (Retention). In campaign backups (#1747) as counts only (entry,
  session start, speaker, times): at restore, counts of anyone not recorded now in the
  restoring server, and of entries the restored campaign lacks, are dropped (the file lists
  which speaker said which names how often, and anyone in the server may download a paid
  campaign's backup); a damaged
  section is refused whole, and an older backup restores with no counts. The fixed tiers
  are prepared off the event loop, once per change to the names. Until the DM can add
  connections (step 2), the tip-of-the-tongue tier stays empty.
- **Step 2, first part built (2026-10-06, `dmbot.ui.name_card`, `dmbot.memory.search`):**
  the overview (counts by kind, the waiting check, "Heard last session", "Added lately",
  an **Open a name…** menu; buttons 🔍 Find a name, ➕ Add a name, 📝 Check new names,
  🧑 Add a player's character), **🔍 Find a name** (the form and `/dmbot names find:`
  with type-ahead), and the **name card** with ✏️ Fix spelling, Add another name (with
  a secret-name field for the campaign's DMs only), Change what it is, and Remove
  (asks first; Undo). The rest of step 2 is built too: Edit other names, 🔗 Same as…,
  🧭 Connect to… (#126, #1528), and **Show all ▾** on long sections (#1542): the card
  cuts other names, secret names and connections to three, and one select under it
  lists each cut section ("Other names (12)"); choosing one sends the whole section as
  a private message of its own, in the card's words and order, 25 lines a page with
  Earlier / Later, worked out again on every press. Secret names and connections are
  listed only to the campaign's DMs (the card's own check). The card does not change,
  and nothing is written. The type-ahead answers from the copy the
  session already keeps. A copy nobody has asked for in 30 minutes, for a campaign with no
  table running, is dropped and simply loaded again on its next ask (2026-10-18, #1883). Decided while building:
  after DMbot's own change the copy is marked stale at once (not waiting for the change
  notification), so the redrawn card and the next search are right; Fix spelling and
  Add another name redraw the card in place with what changed on top; Fix spelling
  and adding a name refuse a name another entry already has (checked against names
  everyone may know, so the reply never reveals a secret one) and never turn a secret
  name into the main name; Undo works only to bring back that forgotten name. A
  connection reads the same sentence on both cards for now ("Ulfgar is a member of
  the Frostwolf tribe"); the reversed wording ("members include Ulfgar") comes with
  🧭 Connect to….
- **Step 3 built (2026-10-06):** **📚 Browse by kind** (pick a kind, last heard first
  or A to Z, up to 20 a page with ◀ ▶, a menu to open a card), **📥 Add many** (📋 paste
  a list, or upload a .txt file with `/dmbot names file:`; **📄 Get the template**, a file
  whose `###` lines explain the format, with examples to edit, as the owner asked) and
  **📤 Download all** (the same format, so it can be edited and added back; secret names
  only for the campaign's DMs). One name per line: `name | kind | other names | secret
  names` (the fourth part so a DM's download reads back with its secret names; only the
  campaign's DMs may add them, and managers get a template without it). Other names are
  separated by `,` or `;`, as in the ➕ Add a name form; lines starting with `#` are
  skipped. Decided while building:
  - the whole list is saved as **one change** (one Undo, one reload for live
    transcription) rather than batches of 200, since a list is capped at 2,000 lines; a
    long list holds the campaign's memory writes for a few seconds (a bulk insert can
    shorten that later);
  - **Undo** takes the list back only while none of its names were checked, changed,
    connected or heard since;
  - a name with no kind, or that sounds like a known name, is saved as a suggestion
    waiting in 📝 Check new names (the summary's button); names DMbot already knows are
    skipped and counted, checked again inside the save so two lists at once never
    duplicate a name;
  - for anyone but the campaign's DMs, a clash with a secret name looks exactly like no
    clash.
  - **names from any document** (owner's decision): 📥 Add many has 📋 Paste a list,
    🔗 Paste a link, 📎 Upload a file (a .txt, .pdf or .docx; `/dmbot names file:` is the
    fallback) and 📄 Get the template. **A link can point anywhere** (#264, 2026-10-07):
    a document or a web page anyone with the link can open; Google Docs and Drive,
    Dropbox, OneDrive and SharePoint share links are turned into their download address,
    and web pages are reduced to their readable text. A link pasted into Paste a list is
    read as a link, and a web address is never saved as a name. Fetching is guarded
    (`dmbot.fetch`): https on port 443 only, at most 3 redirects, 10 MB and 30 seconds,
    and every address a host resolves to must be public (no private, loopback,
    link-local or cloud-metadata addresses), checked again on every redirect and used
    for the connection itself; a file host's sign-in page is never sent to the AI. Links
    are never logged. A list DMbot can read all of is added straight away; anything else
    (a document, **anything from a link**, or a list with any line that doesn't fit)
    goes to the AI (Anthropic, `ANTHROPIC_API_KEY`,
    `AI_MODEL_FAST`, the small model by default), which writes the names list. The DM first
    confirms the right to use the material and that its text goes to Anthropic (one
    press, logged with who and when: the IP rule), then sees the list (the start in the
    message, all of it as a file to edit) and adds it with **Add these names**. The
    document is treated as untrusted data: quoted, with the AI told to ignore
    instructions in it, and its answer read by the same strict parser; secret names are
    asked for only for the campaign's DMs. Without a key, documents are refused with a
    pointer to the template, and a list adds what fits. Up to 10 MB, 500 PDF pages and
    about 100 pages of text, in pieces of 40,000 characters read side by side (at most 3
    AI requests at once across all servers, 10 minutes per document). Each server reads
    one document at a time and up to 20 a day (the operator pays until bring-your-own
    keys, #50). Whoever may add names (the campaign's DMs and server managers) may
    confirm the right to use a document; the confirmation is saved with the campaign
    (`shared_confirmations`, #252: who, when, what for, and a SHA-256 fingerprint of the
    text, never the text or the file's name), before the AI reads anything: if it can't
    be saved, nothing is read. It's deleted with the campaign and carried in its
    backups; rows read from a backup are marked restored (anyone in the server may
    restore one, so they say what the file says, not what DMbot saw pressed). The
    Shared story switch (#238) and shared rulebooks (#49) record the same way
    (`shared_story`, `rulebook`). An empty list, or a list whose only
    problem is a secret name from someone who may not add one, never goes to the AI;
  - **a kind DMbot doesn't know is asked once per word** (owner's decision): the summary
    shows a menu for each of up to 4 unknown words ("What is every “wizard” (50)?"), and
    picking one confirms all those names as that kind in one change; the template lists
    the other words that work (town, monster…). The list file has **no emoji**: kinds and
    instructions are plain words.
- **Step 2, second part built (2026-10-06):** **Edit other names** (pick one: ⭐ make it
  the main name, where the old main name stays one of its other names and a secret name
  never can be; 🤫 keep it secret or 👁️ stop, for the campaign's DMs only; ✖ not this
  name), **🔗 Same as…** (find the other name, then "Call it X" or "Call it Y"; the two
  become one, with **Undo** on the card), **🧭 Connect to…** (pick how, from this
  name's side or the other way round, then the other name; saved as confirmed; for the
  campaign's DMs only, a **🤫 Secret: no / yes** toggle sits beside Back (it starts off each time the
  menu opens and applies to the next connection saved); a secret connection never shows on players'
  cards or Show all, and the DM's cards mark it 🤫; a name found through a secret name makes the
  connection secret by itself; and a
  **✏️ Edit a connection…** menu (#2323; it replaced the separate Remove and Hide-or-show menus) changes the words, hides or shows it, or takes one back, with Undo for ten minutes on a removal, secret ones listed, marked 🤫, only to DMs), and a **Show all ▾** select when a
  section is cut. Decided while building: a card groups connections by how they read from that
  name's side ("is a member of **Frostwolf tribe**"; on the tribe's card "has as
  members **Ulfgar**"; ally, enemy and family read the same both ways); a connection is
  secret only when the DM keeps it secret (#1546: the save checks again that the person is a DM,
  and saves it as not secret, with a line saying players can see it, if not; a connection already
  secret is never made public by saving it again); a connection that
  breaks the usual rules (a member of a place) is still saved, flagged for the
  after-session check.

**Campaign memory rules (the ontology) (decided 2026-10-05).** EntityBot alone builds and
maintains the ontology; there is no human graph engineer. So it is small, strict,
versioned and self-checking:
1. **A fixed core, defined in code:** types (**one kind for each entry, what it
   fundamentally is, #1034**: character, place, group, item, event, idea; see "Entity kinds"
   in docs/STORY_MEMORY.md) and relationships
   (`located_in`, `member_of`, `ally_of`, `enemy_of`, kin, `owns`, `knows`, `serves`,
   `appears_in`). EntityBot can extend the core but never change it; core changes come
   only in code releases, after review.
2. **Extensions belong to one campaign** and never cross campaigns or servers. A useful
   pattern reaches the core only through a code release.
3. **Reuse before creating:** before adding a relationship, search the existing ones by
   name and meaning ("friends_with" maps to `ally_of`). Every new term needs a plain
   description, a parent, a domain and range, examples, and a reason.
   *How (owner, 2026-10-14):* synonym list, spelling, then meaning by in-context embedding distance
   between terms that connect the same kinds of things; thresholds set offline on a golden set; the DM never
   approves terms, only facts; a refused term's fact goes on `associated_with` with a label. Term embeddings
   are stored on the predicate's definition, each fact's in-context embedding in the edge's properties.
   See docs/STORY_MEMORY.md, "How the ontology steward decides a new term".
   *Traceable definitions (owner, 2026-10-14):* every relationship, core and campaign, has a real-world
   definition (description, covers, not this, examples) kept in the database by version, and each fact records
   the version it was written under; `sworn_to` joins the core (paladin oaths, warlock pacts). See
   docs/STORY_MEMORY.md, "The official list of relationships"; build #1189.
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

*Built, #1034 (2026-10-10): entity kinds.* An entry's kind says what it fundamentally is; roles
and categories are never kinds.
- **Kinds:** `character` (always unique: PCs, NPCs, gods, named monsters, a named horse),
  `place`, `faction` (shown as "group"), `item`, `event`, `concept` (shown as "idea"). The older
  `npc`, `player_character`, `deity`, `creature` and `spell` are deprecated in the ontology
  (`replaced_by` character, or concept for a spell): no new entry takes one.
- **Role:** `player_character`, `npc` or `god`, on a character, one at a time (`memory_entities.role`;
  it moves to `memory_states` when that table is built). Only a player character has a player.
- **Links to the rules** (`memory_rule_links`): species (shown only as the 2014 name, tagged
  `[Legacy 2014]`), creature type, stat block, class, background, by source and name; a name the
  rules data doesn't have keeps its words and shows "not in DMbot's rules". One species and one
  creature type for each character, any number of classes. A card reads "Snot: character · NPC ·
  goblin (humanoid) · Goblin Warrior". Rules vocabulary (species, creature type, class, background,
  feat, spell, skill, ability) lives in the rules data, never in campaign memory; unnamed monsters
  ("three goblins") are not entries.
- **Compatibility:** callers may still say npc, player_character, deity or creature: the store
  turns it into a character with that role (a spell is refused: look it up with `/dmbot rule`).
  Lists and menus keep their familiar words.
- **A campaign's own kinds** can't be a role or a rules category (a block list with a plain
  message). **Migration 0043** moves what exists: npc, player character and god become a
  character with that role; a named creature becomes a character, and a spell an idea, both marked
  "needs a look" for the DM (nothing is deleted); a campaign's own kinds and relationships that
  named an older kind name its replacement. Older backups are mapped the same way on load.

**Players and the DM in the names (owner, 2026-10-14).** DMbot also learns the names the table uses for
the **real people** at it, so "Mike, what do you do?" followed by "I run at it with my sword" tells it
that Mike is the person who just spoke, and that this person plays the character the DM was talking to.
- **A new kind, `person`, shown as "player or DM".** It is the one kind that is not part of the story: it
  stands for a real person at this campaign's table and is always tied to their Discord account. Its
  names are the names the table calls them ("Mike", "Mikey"); their server nickname and display name
  count as names already, without asking. It sits next to the story kinds in every names menu ("What is
  it?", Browse by kind, the overview, Add many), but PlotBot, NPCBot and the story checks ignore it.
- **Every person is checked: they play a character, or they are the DM.**
  - `plays`: person → character (read from the other side as "played by"). This is the "played by" link
    that **🧑 Add a player's character** and the D&D Beyond sheet already make, now stored as one edge from
    the person. `controls` and `controlled_by` are registered synonyms that map to it (rule 3, reuse before
    creating), never second edges. A person may play more than one character; a player character has one player at a time.
  - **The DM:** a person who is one of the campaign's DMs (from the campaign's DM list, never guessed)
    answers to **DM, GM, Dungeon Master and Game Master** as well as their own names. These four are
    built in for every campaign and are never suggested, fixed into anything else, or used for a
    character.
  - A person with neither (someone who only watches) is flagged "needs a look" for the DM, never fixed
    silently (ontology rule 5).
- **How DMbot learns it, without AI.** Only from consented speakers' lines, while listening. DMbot knows
  who said each line, so it watches for **a name, then an answer**: a line that calls someone by name
  ("Mike, what do you do?", "…your turn, Cerric") and, within about 10 seconds, the next line, said by
  someone else, who answers in the first person ("Ok, I'll do that.", "I run with my sword", "I cast…").
  - A **new name** that is answered that way by the same person **at least twice in a session** is
    suggested in **📝 Check new names** as a player or DM: "When someone said **Mike**, **Mia** answered (3
    times). Is Mike a name for Mia? [Yes, that's Mia] [Someone else…] [Not a player]".
  - A **character** with no player that is answered that way by the same person at least twice is
    suggested as theirs: "**Mia** answered to **Cerric** 3 times. Does Mia play Cerric? [Yes] [No]".
  - When the answers point to someone other than the known player, nothing changes and nothing is asked;
    it is only counted (it may be a shared microphone or crosstalk).
  - Nothing is stored as true until the DM confirms (rule 8). Everything the DM sets by hand counts as
    confirmed, as today.
- **The Cleaner** treats a player's name like any other name: it may fix its spelling. It never turns a
  player's name into a character's name or the other way round, because they are different people, even
  when they sound alike ("Mike" and "Mikal").
- **Where it helps:** the `Q (name)` line in `dmb-chat`, speaker labels in transcripts, the sidebar
  knowing who "Mike" is, and FightBot and the sheet re-reads knowing whose character an action belongs to.
- **Privacy:** these are the real names of people at the table. They stay in this one campaign (never
  shared with another campaign or server), they go into the campaign's backups with the person kept as a
  Discord user ID (as the DM list already is), and the DM can remove them from the person's card. A DM may
  list a server member who never spoke or consented (Add many, Download all, 🧑 Add a player's character;
  decided 2026-10-16, #1559): it is the DM's own act, records nothing from anyone's voice, stays in the one
  campaign and is removable from the card; recording consent is a separate thing and is never implied. **Stop
  recording me** removes the names DMbot *learned* for that person and any suggestions still waiting;
  names and links the DM set by hand stay. Never in GitHub, tests or logs: tests use made-up names.
- **Build:** #1188 (CloudDev). **Built (2026-10-14, PRs #1204, #1207 and the consumers PR):** the
  `person` kind and `plays` link (the character keeps `played_by` as a mirror written in the same
  change); the four DM words refused as names; learning from "a name, then an answer" with the DM's
  questions on their own 🧑 Check who is who button; Stop recording me removing what was learned;
  `Q (name)` and the sidebar's who-is-who lines from the `plays` link. **Also built (#1208, then
  #1547):** the table's people are in the Cleaner's lookup (kept apart from the story's names, per
  campaign), so it fixes a person's spelling under the same rules as any name, shown with Undo when
  it is one word, and it never maps a word between a person and a character ("Mike" the player and
  "Mikal" the character stay as heard); the four DM words are never a source or a target of a fix;
  and the who-answers-to-what learner reads "DM", "GM", "Dungeon Master", "Game Master" or a DM's own
  name as a call to the DM who is here (one DM who has spoken; with two, the one who spoke last in
  the answer window; otherwise no one), never as a nickname to learn, and writes nothing to the story
  graph. The sidebar's "DM, GM… all mean <name>" line names the DM resolved this way when that DM
  agreed to be recorded (#1558), else the session's starter. **Players and DMs in names lists
  (#1559):** for the campaign's DMs only, Download all lists each person as `table names | player or
  DM | more table names | | who | plays`, where `who` is their Discord user id (never a name read
  from Discord) and `plays` the characters they play, and the file's notes say so; a player's
  download leaves people out. Add many reads such a row from a DM: the id must belong to a person in
  the server (a bot, an outsider, or a row with no id is refused by row, the rest still loads), the
  names are added as table names the DM confirmed (never replacing, never twice, not the `Player
  1234` placeholder), and a character that exists and has no player (or is already theirs) is linked
  as theirs; any other character named is left alone and the person is marked "needs a look". No
  format version: the extra parts only exist on those rows, so older files read as before.

*Proposed additions (2026-10-06, docs/STORY_MEMORY.md, not yet decided):* a core v2
with story threads, promises, clues and state facts (`condition`: alive, dead, missing…,
one at a time, with allowed changes); new checks (one holder at a time, only for items
marked unique; nesting and no loops for places and causes; a dead or destroyed thing
can't act; an NPC mentioning a limited-audience clue they never learned); claims kept apart from facts so an NPC's lie or a player's guess is
never world truth; and the ontology and graph upkeep split into AI roles that only
propose, each with its own golden test set.

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
  - **A look-alike is not always a mishearing (decided 2026-10-09, #573).** Three rules
    keep the Cleaner from merging two people into one:
    1. **Two people in one line:** a look-alike word is never made into a name the same
       line already says. With Ysolde in the line, "Isolde" stays "Isolde" (it is a
       second person); the same holds for the options in a "Did they mean…?".
    2. **Silent only when near-certain.** A fix by sound is silent only when the heard
       word is spelled at least **0.95** alike to the name (`NEAR_CERTAIN`). Below that
       (and above the old bars of 0.7 joined, 0.8 one word) it is a **medium** fix:
       made, with a note and Undo in "✏️ Name fixes to check". With **How much DMbot
       says: Quiet** it is not made. Measured on names-stress and the tests: a new
       name that looks like a known one scores 0.83 ("Isolde"/Ysolde, "Cedric"/Cerric)
       up to 0.93 ("Rothgar"/Hrothgar, which names-stress calls the likeliest wrong fix),
       and a likely mishearing of a known name 0.91 to 0.94 ("Gorak"/Gorrak, "Beleros",
       "Belle Ross"). The ranges overlap, so only near-identical spellings (0.95 and up)
       stay silent; the rest are noted, and the DM decides. The cost is a few more lines
       in "✏️ Name fixes to check". Under **Quiet**, noted fixes are not made, so
       sound-alike names are left as heard: that is what Quiet promises (fewer misheard
       names get fixed, no notes). The one-word rules (the name in the scene, 0.8) are
       unchanged. This rests on about a dozen pairs: revisit it with the twin when more
       real mishearings are on record. A one-letter slip in a short name scores about
       0.83 to 0.86.
    3. **Once the DM confirms a name it is known.** "Isolda" confirmed next to Ysolde is
       never rewritten; "Isolde", sounding like both, is asked about, never made Ysolde.
  - "Did they mean…?" and Undo appear **only in the DM screen**, never in the transcript
    channel. The question leads with what was heard: "❓ **Mia said "Bell or us"**: did
    they mean… [Belleros] [Bellamy] [Type it…] [Keep as heard]". At most 3 options.
    **Keep as heard** saves a "don't change this" rule, like Undo.
  - **Not flooding the DM screen:** medium fixes go into one "✏️ Name fixes to check"
    message that is edited in place, one line and one Undo each. At most one question is
    open at a time, with a cooldown, and only for names that come up again or matter to
    the scene. Unanswered questions expire quietly (the line stays as heard) and move to
    the after-session report. When **How much DMbot says** is Quiet there are no live
    questions at all (#504).
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
  showing, and the line is updated when the answer arrives. The off-topic filter is
  outside this budget: the line is posted at once and edited to the marker if its
  window comes back off-topic (decided 2026-10-08, see "Off-topic filter").
- **Re-listen audio:** kept in memory for about 60 s only, in the process that captured
  it (never queued, never in Redis, never logged, never stored). **Consent is re-checked
  right before every re-listen request**, and the audio is dropped at once on revoke.
  Re-listen is added after the first version, once close calls can be measured.
  **Built, close calls are counted (2026-10-16, #1577):** the Cleaner counts, per session and in
  memory, the candidates whose spelling-likeness lands within 0.05 of a threshold (fixed just
  above, left just below, one word or more, people apart), counts only (no words, names or
  speakers). One INFO log line at the session's end, "Cleaner close calls: fixed-near 3,
  left-near 7, one-word 2 of 412 lines", is where dev1 reads it; nothing goes to the DM screen
  or to players. `python -m dmbot.devtools.cleaner_close_calls --names FILE SOURCE` runs the
  Cleaner offline over a kept session or a transcript file and also lists the ten nearest misses
  as words, for dev1 on the owner's recordings (never in GitHub).
- **Test recordings (owner, 2026-10-10; #1019, built): the one place audio is kept.** On a
  test server only (`DMBOT_TEST_RECORDING_GUILDS`, empty by default), and only for people who
  pressed a second, separate button, **Save my voice for tests**, in their consent message
  (it needs the recording yes first). Each utterance core heard is written as one FLAC file
  under `DMBOT_TEST_RECORDINGS_DIR` (a volume, owner-only), with a `session.json`: made-up
  speaker numbers (1001 the DM, 1002 and up players), each with a role and a *voice code* (a
  keyed hash, the secret lives in that folder), times in ms on one session clock, consent
  events, and the transcript lines and sidebar questions and answers DMbot produced. No
  Discord id, name or server id is in the files, and the files are never in Postgres, a
  backup or the repository. Both yeses are checked before the audio is encoded, before it is
  written and after. **Stop saving my voice** (🗂️ Menu), or **Stop recording me**, stops it at
  once and deletes the person's files from every session; a kept session that lost a speaker
  is marked incomplete. Sessions nobody keeps (`python -m dmbot.devtools.test_library keep`)
  are deleted after 7 days by a daily job. Replaying them is a later issue.
- **Learning is immediate:** a confirmed correction is saved as its own rule (a
  campaign's corrections are their own table; a card's `misheard` spelling is a known
  word the Cleaner never rewrites) and reaches the Cleaner within about a second. Earlier lines of the same session with the
  same mishearing are re-checked from "as heard" and fixed, with each change logged.
  Previous sessions change only if the DM asks.
- **Measured** on a test set of mishearings ("Sara" → Cerric, "Bell or us" → Belleros)
  and traps (real words, nicknames, secret aliases, the serrated-blade sentence), plus
  live timing (speech end → line shown).

**Transcript Cleaner step 1: built (2026-10-07, #127, #53).** Pure logic in
`dmbot.transcript.cleaner`, run on every written-down line with no wait, so the
consent check just made still holds:
- **Silent fixes only, of three kinds:** the same letters spelled another way
  ("Kazeth" → "Ka'zeth"; a word in lower case keeps its capitals, so "the bell" never
  becomes "Bell"); a spelling the DM fixed ("Sara" → Cerric); and an unknown
  capitalized word, or up to 3 of them in a row ("Ka Zeth"), that sounds like exactly
  one confirmed name (`lookup.by_sound`) and is spelled much like it (at least 0.7
  alike). Matching reuses `scene.find_mentions` and the lookup's sound codes.
- **Unknown word:** capitalized, not a common word or game term (the after-session
  scan's lists), not a known name, "keep as heard" word or the display name of anyone at
  the table (people who agreed, the DM, everyone in the voice channel; #295), and not
  said in lower case this session. A word starting a sentence counts only once it was
  also written with a capital mid-sentence (in that line or earlier),
  so "Thorn bushes…" is never "Thorin". Without a dictionary this misses a name the
  first time it starts a sentence; a wrong fix is worse than a missed one. All capitals
  are left alone.
- **One word alone needs context:** the name was said in the last ~10 minutes (the
  scene tracker) and must be spelled closer (at least 0.8 alike), because real names
  and brands sound like campaign names ("Mary" and Mara, 0.75; "Amazon" and Amazonia).
  Joined words need neither.
- **Never:** a fix from a name DMbot only suggested (no Undo note yet, so it does
  nothing); a change inside a secret name (of any length); a fix where the words, with
  the words around them, sound like a secret name ("Silas Vain" for "Silas Vane"), or
  sound like two entries; a fix from an out-of-date copy of the names; a DM's fixed
  spelling that renames someone at the table. **The line is checked again as written:**
  a DM's fixed spelling needs no likeness, so "Silas Bane" with the rule "Bane" → Vane
  would write the secret "Silas Vane"; any fix whose written name, with the words
  around it, is or sounds like a secret name is taken back (#321 review). Names not
  in Latin letters have no sound codes, so they're compared with secret names by
  spelling instead ("Сайлас Вейна" for the secret "Сайлас Вейн"). After 8 rebuilds the
  line is kept as heard.
- A fix writes the name the way it was said (the matched other name, not the main
  name); a DM's fixed spelling writes the main name.
- **Stored:** `heard` as before, `text` cleaned. The live transcript channel shows the
  cleaned line; downloads are still "as heard", now labelled
  `[0:42:10] (Mia) {Cerric}: …` with each player's confirmed character. The
  after-session scan reads the cleaned line too (#394), as do scene hints and heard
  counts.
- **Measured offline (synthetic names, one vowel changed):** fixed back 92% at 50 names,
  56% at 500, 31% at 5,000, since in a big campaign more names sound alike and those are
  left alone. Safe, but to check on real campaign data (PyCharm session). About 5 ms per
  60-word line at 5,000 names.
- Built since: Undo notes for medium fixes (`transcript/fix_notes.py`, #296), re-checking
  earlier lines after a correction, and off-topic hiding with the DM's "🙈 Left out as
  off-topic" list (`transcript/left_out.py`, #52, #677). Next: people in the Cleaner's lookup
  (#1547).

**Transcript Cleaner step 2: built (2026-10-07, #296).**
- **"Did they mean…?":**
  - **When:** a word that sounds like two or three confirmed names stays as heard, and
    the DM screen asks: "❓ **DMbot heard Mia say "Marin".** Did they mean… Not sure?
    Ignore this and it stays as heard. [Maren] [Marron] [Type it…] [Keep "Marin"]". Each name must
    be spelled at least 0.7 alike (0.8 for a player's character), none may be secret or
    only suggested, and the words must not be next to a secret name. No scene is
    needed to ask. A question about a longer run of words never blocks a sure fix of
    fewer words.
  - **Not flooding the DM screen:** one question is open at a time; after any question
    closes (answered, expired or taken down) there is a 2½-minute cooldown; each word
    is asked about at most once per session, and only once it has been heard a second
    time this session or may be a name in the scene (said in the last ~10 minutes, or
    a player's character). A question nobody answers expires after 5 minutes and
    shrinks to one line (`⌛ Not answered: "Marin" stays as heard.`). Not asked at all
    when How much DMbot says is Quiet (#504).
  - **Answering:** only the campaign's DMs can answer. A name becomes a fixed spelling
    (`add_correction`, fix, source DM); **Keep** becomes a keep rule. Either way the
    same words are handled silently from the next line, and the message turns into the
    answer with **↩️ Undo**, DM-only like answering. Undo takes back the saved change
    and works after a restart; an answer that was already saved has no Undo.
    **A correction the DM made stays if the speaker later stops being recorded** (the
    DM wrote it, and it names no one; decided 2026-10-07).
  - **While saving:** the question stays open, so a second press gets "Already
    saving", a failed save can be tried again, and the question can still be taken
    down.
  - **Consent:** checked before the question is posted, before the answer is saved,
    and again after. If the speaker stops being recorded, their question is taken down
    without repeating their words.
  - **When it closes:** questions live with the running session. When it ends, the
    open one is closed; after a restart, a press says it's closed.
  - **Type it… (built 2026-10-08, #503):** a button after the names opens a form, "The
    name, as it should be written", starting with the words heard (DM only, at most 60
    characters, the names list's rules: no `|`, at most 8 words, no links). A name or
    other name DMbot knows (spelled the same way) means that name, written its own way;
    the words exactly as heard mean Keep; a name it doesn't know becomes a new name
    waiting in 📝 Check new names, with the fix rule, in one change (one Undo takes back
    both), and the answer says it's waiting there. Any DM rule pointing at a name not
    checked yet fixes later lines silently (the DM wrote it, so no fix note); the name
    doesn't count as said until it's checked. Refused, with the question left open: a
    secret name, a name that would make or stand next to a secret one in the line, a
    spelling two names share, and a name already in the campaign (checked again when
    saving, secret names included). The names are checked as they are now, not the
    session's copy. A typing mistake answers privately, saying to press Type it… again.
    An answer that arrives after the question closed repeats the typed name and points
    to `/dmbot names`. Consent is checked again when the form is sent.
  - **The line that was asked about (built 2026-10-08, #503):** the answer is written
    into that line too: saved, waiting, and in the transcript channel within ~30 s
    (the same paths as a fix's Undo). The answer says "in that line and from now on…
    Earlier lines of the session are written the same way too: see step 3." Never if it would put a secret name in the line (the
    same check as every fix, on the line as written): the rule is saved, the line stays
    as heard. Undo of the answer puts that line back while the session still runs
    (after a restart only the rule is taken back). Keep changes nothing in the line.
  - **Quiet:** when How much DMbot says is Quiet, no questions are asked (#504).
- **Cleaned and both downloads:**
  - **At the end of a session:** the message has **[📄 Cleaned]**, **[🎙 As heard]** and
    **[📄🎙 Both]**, one line explaining each. Buttons sent before this still give
    the "as heard" file.
  - **`/transcript`:** sends the cleaned file as soon as the session is picked, with
    **🎙 As heard** under it.
  - **The files:** each says which version it is and how to get the other. Files are
    built off the event loop. "Both" must fit Discord's limit together.
- **Fixes with Undo (decided 2026-10-07 on #296):**
  - **Which fixes:** a misheard word that sounds like a name DMbot only *suggested*
    (spelled at least 0.9 alike, never secret, not next to a secret name) is fixed,
    but never silently. So is a close look-alike of a *confirmed* name spelled less than
    0.95 alike (decided on #573, see the Cleaner's "look-alike" rules above).
  - **Where they show:** only in the DM screen, in one "✏️ Name fixes to check" message
    edited in place: "DMbot changed these words in the transcript but isn't sure.
    Wrong? Press its Undo to put back what was heard." One numbered line and one
    **↩️ Undo N** each (the newest 10, fewer if the names are very long). A fix keeps its number for the whole session,
    so a number never changes meaning while the DM aims at it. A burst of fixes is one
    edit, and if the message is deleted a new one is posted. Nothing about these
    guesses ever goes in the transcript channel.
  - **Undo** (DM-only) puts the heard words back in that line, and in any other place
    in the line with the same words: the stored line (waiting for a save in progress),
    a line waiting to be posted, and the channel message if it was posted in the last
    ~30 s. It saves a "keep as heard" rule, and the private reply names the words:
    "↩️ Undone. "Beleros" stays as heard: DMbot won't change it to Belleros again in
    this campaign." **↪️ Allow again** under it takes the rule back.
  - **Consent:** if the speaker stops being recorded, their lines leave the message.
    Undo only touches their words while they're still recorded.
  - **When it closes:** at the session's end the list stays but the Undo buttons go.
    After a restart, a press says it can't be undone any more.
- **Built, unanswered questions go to the after-session check (2026-10-16, #1572):** a "Did they
  mean…?" that closes with no answer (it expired, or the session ended) is kept in memory, at
  most 25 a session (the oldest go first), and saved when the session ends as a waiting question
  of the campaign (its own table, `waiting_name_questions`: the line's reference, the heard
  words and the 2 or 3 names, never the line's words). The DM screen says how many and shows
  **❓ Check names**; DMs only, checked on every press. Each question reads as it did
  live: **[Maren] [Marron] [Type it…] [Keep "Marin"] [Later]**. A name picked or typed is saved as
  the campaign's correction (so later sessions write it that way) and written into that one
  stored line's cleaned words, never what was heard, and only if the words are in the line exactly
  once and no secret name would stand there; **Keep as heard** saves the "don't change this"
  rule. Earlier lines with the same words are not rewritten. Nothing is kept for a speaker who
  stopped being recorded (a person who stops, or whose lines are deleted, has theirs removed), a
  question whose line was deleted drops out of the list, one split across two pieces of speech is
  not kept, and a question unanswered for 7 days expires quietly.

**A player may suggest a fix to their own line; the DM decides (decided 2026-10-16 by Supervisor,
built #1491).** The bot advises; the DM decides; what was heard never changes. A player who sees
their own line misheard types `/dmbot fix`, or presses **✏️ Fix one of my lines** in the private
message that comes when a session ends, picks the line from their newest ten (their own spoken lines
only, never a DM sidebar line or typed chat) and types what it should say (1 to 200 characters,
one line). The DM-screen gets one short line: who, what was heard, what they suggest, with **Accept**
and **No** (buttons that work after a restart, DM-only on every press). **Accept** changes only the
line's cleaned words (the running session's notes and the channel message follow, as for every fix;
a finished session's saved line is changed in the database); **Keep as is** changes nothing. The player is
told once in a private message. Nothing is posted publicly. One suggestion per line at a time
(table `transcript_suggestions`, migration 0093). At the next session start (not after a restart in
the middle of one) the DM screen shows the oldest three still waiting again, as the same line with the
same two buttons, and "…and N more that go when this session ends." for the rest (#1504); the
player is told nothing. A suggestion that waits past the end of the next session is dropped with no
message. Checks: the speaker must still be recorded when they send it and
when it is accepted; the words must not write, or stand next to, a secret name (refused with
"That can't be used", no reason, so it cannot hint at one); everything is escaped on the DM screen.

**The DM fixes any line at once (decided 2026-10-16 by Supervisor, built #1499).** `/dmbot fix` run
by a DM of the campaign, or **✏️ Fix a line** under the end-of-session message on the DM screen,
opens the same picker over the newest ten spoken lines of any recorded speaker (never sidebar or
typed lines), then the same one-line box. The words are written at once through the same path as
an accepted suggestion (`text` only, never what was heard; the secret-name check with the same
refusal words; the line must still be as shown; its speaker still recorded), while the session
runs it is listed in the DM screen's fix-notes message as a "DM edit" with the usual **Undo** (it
puts the line's words back as they were, and saves no "keep as heard" rule); a finished session's
edit has no Undo and no list. A line with a name fix, an answer or an open "Did they mean…?" is
not edited until that is settled. Nothing goes to the player and nothing is public;
the DM is checked on every press.

**Transcript Cleaner step 3: built (2026-10-16, #127): earlier lines are re-checked after an
answer.** When the DM answers "Did they mean…?" (by picking a name or by Type it…), the
spelling they chose is also written into the **earlier lines of the running session** that
said the same words, saved ones and ones still waiting to be saved (`transcript.refix`,
`Bot._fix_earlier_lines`). It is narrow on purpose, because a wrong fix is worse than a missed
one:
- only the DM's spelling for **exactly those words** (no new guess by sound, whatever the scene
  is now), found by the Cleaner itself from `heard`;
- only a line that still says **exactly what was heard** (a line with any other fix, a typed
  line and a DM sidebar line are left alone);
- never where the name would be, or stand next to, a secret name (the same check as every fix,
  on the line as written); never a table person's own name;
- only people still recorded, checked again before every line (consent);
- at most **100 lines**, the newest first; the channel message is edited if it is still recent
  (the same ~30 s rule as every fix).
The DM's answer says how many ("3 earlier lines were fixed too."); with none it still says
"Earlier lines stay as heard." **One Undo** of the answer takes the answer and every earlier line
it wrote back together. Each run is logged as a count, never as words. **Not done:** corrections
made outside this question (the names list, `/dmbot names`) do not re-fix earlier lines; a
session that has ended is not re-checked; sessions before the correction change only if the DM
asks (as decided).

**Transcript Cleaner step 4: built (2026-10-16, #127): the look-back pass asks about a name split
across two pieces.** Speech is cut into pieces after a pause, so a name said with a breath in the
middle ("Ka" | "Zeth") reaches the Cleaner as two lines. For the same person, when the earlier piece
ended at most **2.5 s** before this one began (they may overlap by up to 1 s), the Cleaner reads the
two pieces as one line and, if the last words of the earlier one and the first words of this one (2
or 3 words, at least one on each side) are what it would have fixed or asked about in one line
(`transcript.lookback`), the DM gets a "Did they mean…?" that **shows both pieces** ("DMbot heard Mia
say "…we meet Ka" and then "Zeth at dawn…". Is that one name?"), so they can see the answer joins two
lines. **It asks only; it never changes a line by itself.** (Only the 4 words either side of the seam are read, so it costs little per line.) Every check of the one-line path holds
(confirmed non-secret names only, spelled alike enough, one name, never a table person's name, never
next to a secret name, checked on the line as written), and nothing is asked when either piece alone
already has a fix or a question on those words, when the tail ends in punctuation, when the head
is followed straight away by punctuation or is the whole of its piece (taking it out would leave a
stray mark or an empty line), or under Quiet; the usual limits apply (one question open at a time, a cooldown, once per
words a session). A run of real words ("Bell" | "or us") is not found, because the Cleaner never reads
those as a name. **The answer** saves the usual rule for the joined words (so a later "Ka Zeth" in one
line is fixed, and step 3 re-checks the earlier lines of the session), writes the name where the tail
words were in the earlier piece and takes the head words out of this one: both or neither (if the
second can't be written the first is put back), only while the speaker is recorded, never where it
would write a secret name; **one Undo** puts both pieces back. Not done: silent seam fixes. (Helpers'
copies of a changed piece are brought up to date: see step 5.)

**Transcript Cleaner step 5: built (2026-10-16, #127): helpers' copies follow a changed line.**
When a piece's words change after the helpers read it (an answer to "Did they mean…?", a name
split across two pieces, the re-check of earlier lines, an Undo), `Bot._rewrite_line` tells
`_line_changed`, which brings the helpers' **copies** of the piece up to date, and only when the
write really changed it and the speaker is still recorded: the names scan's line (`table.heard`), the
sidebar's recent scene (`table.recent`, which DM sidebar answers and FightBot read), the off-topic
filter's waiting copy (held, and the open window), and which names count as heard and in the scene
(a name found in the new words and not the old one is counted as heard, and noted in the scene only
if the piece was said in the last 10 minutes; an Undo takes the count off and leaves the scene,
which fades by itself). A copy is changed only when it is certainly that piece's: the names scan's
line only if the piece isn't held back or hidden and its words match exactly one entry, and the
sidebar's scene only where exactly one entry matches (two identical lines from one person can't be
told apart, and a missed update is safer than a wrong one). A speaker who stops being recorded has
their words forgotten here too.
**Helpers that act are never run again**: a rules card, a house-rule proposal, a clock phrase, a
sheet re-read or a sidebar question fires once, on the first words (acting twice is worse than
missing a name). The words each piece has in the copies are kept for the last 3,000 pieces; an older
one is skipped quietly.

**Transcript Cleaner step 6: built (2026-10-19, #2048, #127): the AI ranks the options of "Did they mean…?" and never fixes a line.** When a question is about to be posted, one fast-model call (the off-topic filter's, so the same plans) reads the line as heard, the last 3 spoken lines of the scene (only people still recorded; no speaker names) and the options' names, and answers with one option's name or "none" in a fixed shape (structured outputs, `transcript/rank_options.py`). The likely option goes first and its button says "Marron (likely)"; the DM still answers, and nothing changes a line without that answer. "None", an unknown name, a refusal, an error or more than 3 s leaves the question as it was; nothing is sent when the line, the scene or an option holds a secret name. At most 40 calls a session (`DMBOT_CLEANER_RANK_MAX`, 0 is off), about a cent an hour of play. The session's end log line counts calls, likely, none, errors and how often the DM picked the likely name: that number decides whether a silent fix could ever be allowed, which is not decided here.

**Off-topic filter (decided 2026-10-04; updated 2026-10-05).** A very light, fast AI pass
right after the Cleaner. Scheduling, life updates, and other non-game talk are labeled
`{non-game_content}` and not analyzed further, which saves cost. In the cleaned
transcript, clearly unrelated talk shows as `[1m 22s of off-topic chat skipped]` (see
"Transcript format"); table talk and anything unsure stay.
*Decided 2026-10-08 (Supervisor, #52, dev2's questions):*
- **Consent says the words go to an AI company, once for the whole product.** The consent
  request and the per-session reminder gain: "DMbot's helper reads that text, with who
  said it, to give your DM notes. For that, the text goes to an AI company (Anthropic).
  It isn't used to train their AI." ("with who said it" added 2026-10-08 on dev2's
  question: the AI sees speaker and character names; still version 3 while 3 is not
  deployed, else 4.) That bumps `TERMS_VERSION` (to 3), so everyone who said yes is asked again,
  and nobody is recorded until they agree to the new wording; the filter never needs a
  per-person check of its own. Reason: every helper that reads the transcript (rules
  advisor, names, this filter, story memory) sends text to the AI, so the consent covers
  that once rather than per feature. If a provider or key ever trains on the data, the
  wording changes and the version goes up again. The wording PR lands and deploys before
  the filter PR.
- **The live line is posted at once and edited later**, replacing the earlier "wait
  within the 0.7 s budget": no line waits for an AI round trip. When a window comes back
  off-topic, the line (or the run of lines) is edited to the marker inside the edit
  window. Helpers get a line only after its window is labelled; a late or failed filter
  counts as game talk (when unsure, keep). The raw transcript keeps everything.
- **Key and cost:** the server's `ANTHROPIC_API_KEY` until #50; no key means the filter
  is off and nothing is hidden. The smallest model, one call per window (a few
  utterances or about 20 s), calls and tokens counted into the session's stats so cost
  per session hour can be reported.
- **Storage:** a `topic` column on `transcript_lines` (`game` / `table_talk` /
  `off_topic`, default `game`) so cleaned downloads can show the markers later.
- **Built (2026-10-08, #52 part 2; needs terms version 3, part 1):** lines plainly about
  the game (a campaign name, dice, two table words) are never sent. The others wait in a
  window of 6 lines or 20 s, and one call to the smallest model labels it. Only the
  numbered words go, never who said them, and the prompt says the lines are not
  instructions. An unclear answer is game talk. The names scan gets a line only once
  labelled, never an off-topic one (the helper there is today). In the live channel each
  off-topic line still in the edit window becomes its own marker; one marker per run, with
  the total, is in the cleaned download. The last window is labelled when the session
  ends. Each line also keeps how long it was said (`duration_ms`). Every AI call has an
  8 s limit (a slow answer keeps the window as game talk, and never holds up the end of a
  session); after 3 failures in a row the filter rests 5 minutes. Consent is checked again
  right before a window is sent. Lines of two words or fewer are never sent. The name fixer's
  word list (lower-case words, #1748) follows the same rule: a held line adds its words only
  once its window is labelled game talk, an off-topic line never does, and a window the filter
  couldn't judge counts as game talk, as above. A line the DM puts back adds its words then. The trade-off: a held line's words reach
  the list when its window is labelled, up to one window later, and it is the cleaned words.
  The list lives in the running session's table (one campaign, one server) and is never saved.
  The log line
  "Off-topic filter: N calls, … tokens" gives the cost. Replay case:
  docs/test-scripts/off-topic.md.
- **Put it back (decided 2026-10-08, Supervisor, #677; why: the DM decides).** A game line
  wrongly labelled off-topic can be undone. The DM screen keeps one message per session,
  "🙈 Left out as off-topic: kept out of the cleaned transcript. Game talk? Press its Put
  it back.", edited in place, with the newest 10 runs (one person's off-topic lines from one
  check, in a row). Each run gets one line, `[time] Name: first words…`, and a **Put it
  back N** button with its number.
  Pressing it makes those lines game talk again: in the stored transcript (so the cleaned
  download shows them), in lines waiting to be saved, and in the live channel if they were
  posted in the last ~30 s; they also go to the names scan, once (even if the session
  is stopped mid-press). The line then reads "↩️ Put back: [time] Name: first words…".
  With no stored transcript yet for lines already out of the waiting buffer, nothing is
  said to be back: the DM is asked to try again. Only the campaign's DM can press it, and the
  buttons go when the session ends. It shows at every DM-screen level, quiet included,
  because it's the DM's only chance to undo; one message edited in place never pings.
  Privacy: these lines are table chatter, already in the as-heard file anyone in the
  server can download, not DM-screen content. A speaker who stops being recorded has
  their runs taken down.

**Story memory: continuity, reputations, the shared story (decided 2026-10-06, #227).**
Full design and rationale: docs/STORY_MEMORY.md. In short:
- **Continuity warnings** in the DM screen, quiet by default, always with the source:
  "⚠️ Heads-up: **Ulfgar** died in Session 4. He was just voiced talking. [He's alive
  after all] [It's not really him] [Ignore]". Code checks first, AI only for close calls,
  the rest in the after-session report. A secret said out loud asks "Is it revealed
  now?" and who learned it. **None until the campaign has enough confirmed story**
  (unless the Shared story switch is on). Each warning is **possible**, **probable** or
  **likely**; DMbot interrupts at **probable** by default (the DM can change it). What
  the DM narrates is what the characters perceive, not canon ("the keep is abandoned"
  means it looks that way); the main catch is the DM contradicting an earlier
  description ("a temple in town" in Session 3, "no temple" in Session 6). Warnings may
  lag by several seconds.
- **Reputations, hierarchical:** standing (Hostile · Unfriendly · Wary · Friendly ·
  Devoted) is worked out when needed from deeds the DM confirmed, along two ladders:
  who is judged (character → party → groups → their people: elf, dwarf, tabaxi…) and
  who judges (NPC → their groups → their town → region). A stranger at the gate judges
  by the town's view of the party, of the character's people and its general mood;
  direct experience gradually outweighs that. Only existing impressions and attitudes
  are stored, so it scales. The DM can set standing at any rung.
  Built (#1732): **⚔️ Record a deed** on a player character's or the party's card (what happened, whom it
  mattered to, how it landed, one confirmation, Undo); a deed counts as evidence of weight 20, half after
  ten sessions, and the DM's own word still wins. Nothing is read from speech.
- **Plots:** story threads (hinted, open, active, stalled, resolved, abandoned),
  promises with due dates, clues, and cause and effect; a pre-session note of what's
  open and what could clash tonight.
- **Shared story switch** per campaign: **Off** (default; never suggests story),
  **Watch the story** (warns when the table breaks something the adventure still needs: built
  for a person the adventure needs alive or a thing it needs found, #1729; DM screen only, once, no AI),
  **Guide me** (says what the story has next, with the section; built without AI as a
  **🧭 What's next?** button, #1731: the chapter the table seems to be in, what is still ahead, the next chapter). The DM can also ask "Hey DMbot, what's next?" in the sidebar (#1739): the same answer, privately, never in a transcript (the transcript keeps only the question and "(answered privately: Guide me)", so a reader of the raw transcript can see that Guide me was asked). It needs the sidebar's answer engine to be on, and it is not given in a fight (there "what's next?" means whose turn). For any written
  adventure: published, homebrew or fan fiction. Turning it on warns: "Only give DMbot
  material you have the right to use. DMbot doesn't check this." The story plan comes
  from the DM, chapter by chapter; only after the DM confirms the right to use it may
  DMbot copy chunks of it into that campaign's storage and prompts to build the graph
  (IP rule, CLAUDE.md, 2026-10-06).
- **One retrieval path** for every helper: seed from the scene, 1–2 hops over
  confirmed facts true at the current story time, secrets filtered by code per audience,
  summaries plus quoted lines, every item cited.

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
- The DM can correct the clock with buttons ([+1 hour] [It's dawn] [Rests & time…]).

*Built, part 1: the clock and the DM's buttons (2026-10-10, #965):* no AI, and no guessing time
from narration. `game_clocks` (migration 0041): one row per campaign, in its own server scope,
deleted with the campaign and carried in backups (`ClockSection`, without message ids); none
until a DM sets it. **Game time** is minutes since the start of Day 1; dawn is 06:00, noon 12:00,
dusk 18:00 (`dmbot.timebot.clock`, pure). A DM presses ⚙️ Settings, then **Game clock**, gives a
day and an hour, and DMbot posts and pins one message, "🕰️ Day 4, afternoon (14:30)", edited in
place, with **+10 min**, **+1 hour**, **Short rest** (+1 h), **Long rest** (+8 h), **Skip to dawn**
and **Rests & time…**. Only the campaign's DMs can press them: checked on every press, in the same
database transaction as the change; the buttons survive a restart. **Said at the table:** a
DM's clear "we take a short rest" / "you take a long rest" (a strict phrase list: a question, a
wish, an "if", a "can't" or a long ramble never counts; the same rest twice in five minutes is
one) moves a clock the DM has set, with a one-line note and **Undo** (a rest from a button gets its note and Undo for the DM who
pressed it alone; it only undoes while the clock is still where the rest left it); a player's line never does. **Speaks up only for** dawn,
noon and dusk as a button or rest passes them (not when the DM sets the time), and **24 hours
without a long rest**, once per stretch, with its source ("Check: 24 hours since the last long rest… Optional rule, Xanathar's… Your call."); on by default, and off if the DM turns the "Going without a long rest" optional rule
off. The first time the clock is set the party counts as rested then. **Reading time from the DM's
narration is built for hours, the next morning and days (2026-10-16, #1117, #1594):** "an hour
later", "the next morning", "at dawn", and now a skip of days ("Three days later, you reach the
city", "A week later, the party…", "The next day, you…"; 1 to 30 days, a week as 7). It lands at the
same time of day that many days on and counts as a long rest for the party (a journey of days
includes nights' sleep) unless the DM said "without resting" or "no rest" in the same line; the
one-line note says which ("⏩ 3 days later (counted as rested)"), with **Undo**, which brings back
the clock and the rest record. Only a DM's line that opens the scene and is followed by the
narration's subject or ends the sentence; never "in three days", "for three days", "three days
ago", a question, a wish, a condition, quoted speech, more than 30 days, or a player's line; the
same skip twice in five minutes is one. A rested skip leaves no "24 hours without a long rest" note;
one without rest over 24 hours says it once. Split-party clocks are built (below). **The periodic "where are we" note is built (#1738):** during a session, for a campaign whose clock is set, never in combat, one line on the DM screen ("🕰️ Where we are: Day 4, afternoon (14:30).", day first as on the players' clock, or each group's time while the party is split: "the cellar group, Day 2, night (23:10); the tower group, Day 3, dawn (05:40)." A line too long for one message is cut between groups and ends "+N more") when the clock has not been shown or moved for half an hour of real time. A dawn, noon or dusk line, an effect line (and a timer that ended), a rest, a button or a skip count as shown, so it never repeats what the DM just saw. The DM screen's level governs it (it is a reminder, not a warning): none on Quiet, every 30 minutes on Normal, every 15 on Chatty. The last time is kept in memory per session (a restart just starts the wait again) and players never see it.

*Built, part 2: timed effects the DM starts (2026-10-10, #998):* `game_effects` (migration 0042): per
campaign, own server scope, deleted with the campaign, in backups (`EffectsSection`, validated),
at most 20 running. A DM starts one from **Start a timer** on the clock message (a form: spell or
effect, who it is on, how long) or **Time it** on a rules card for a spell that lasts a length of
time; nothing starts by itself. `dmbot.timebot.durations` reads the index's duration text ("1
minute", "Concentration, up to 1 hour", "8 hours") into game minutes, a round counting as 6 seconds
rounded up to a minute; "Instantaneous", "Until dispelled" and "Special" are not timed; the DM can
type a length instead, and a form with no length uses the spell's own (a test reads every spell in the
index). Concentration shows as 🧠. The clock message lists the soonest five, one line each. When a
button or rest passes an effect's end, the DM screen says once "⏳ Bless on Mira has likely
ended (1 minute)" with **Ended** and **Still going +10 min** (ten more game minutes from where the
clock is, then said again); nothing ends silently or by itself. Only the campaign's DMs, checked in
the transaction. Effects live on the game clock, so a restart keeps them. **Still going** is shown in the next "has likely ended" line as the total with "extended by the DM" (`game_effects.extended`, migration 0049, in backups; #1112), and the press says so on the DM screen only. Reading time from narration: only as a suggestion (#1758, below); the clock never moves from vague words by itself.
*Split-party clocks (Supervisor, 2026-10-16, #1607; migration 0109):* when the party splits, one clock
is wrong for half the table, so the DM can split it.
- **Split:** **Rests & time…** holds **✂️ Split the party** (DMs only, checked on every press). The DM
  names two or three groups (short names, like "Tower" and "Camp"; not during combat). Each group starts with the
  current time and last long rest. The pinned message shows one line for the group times ("Tower: Day 4,
  14:30 · Camp: Day 4, 16:10") and a group picker; the existing buttons, rests, combat and Undo act on the
  picked group. The pick is remembered per campaign across restarts. The players' game-time channel shows
  one line per group in the same words (the day and a rough time, never the exact time).
- **Spoken phrases move nothing while split** (a rest, a time or a round said aloud), because DMbot can't
  know which group the DM meant. The DM screen says once per split: "The party is split, so I didn't move
  the clock. Use the buttons for each group."
- **Notes** (dawn, noon, dusk, the 24-hour line, a timer that ended) are said per group, prefixed with the
  group's name. When one group's clock is more than 8 game hours ahead of another's the DM screen says so
  once per split ("Tower is 9 hours ahead of Camp."): a reminder, never a block.
- **Rejoin:** **🤝 Rejoin the party** asks which time to keep, offering each group's time, the latest first.
  It keeps that group's time and sets the last long rest to the latest any group had (never later than the
  kept time). Then it is one clock again. **Undo** works for the split (while no group has moved) and for the
  rejoin (while the clock is where the rejoin left it); a rest's Undo acts on the group that rested.
- **Timers** stay campaign-wide on the one clock when not split. Split, a timer counts on the group picked
  when it was started (one started before the split counts on the first group), is shown with its group's
  name and is judged on that group's time. On rejoin a group's timers move with its time to the kept time
  (the same time left).
- **Data:** `game_clock_groups` (campaign scope, isolated like `game_clocks`: position, name, minute,
  last_long_rest, tired_told_for, combat round) holds the groups while split; `game_clocks` keeps the clock
  from before the split and gets `picked_group`, two once-per-split flags and `split_undo` (what the last
  Undo needs); `game_effects.group_position` says which group a timer counts on. Backups carry the groups
  (`ClockSection`, validated) and each timer's group. Deleting the campaign deletes them.
*The game-time channel (owner, 2026-10-13, after Test F):*
- **Players see the clock in its own channel.** The players see the clock in a game-time channel,
  `dmb-gametime-<slug>`, without needing the DM screen. Like the transcript channel, everyone
  can read it and only the bot posts in it. `/dmbot start` creates or repairs it, and a failure
  never blocks the session.
- **The players' clock.** A message pinned there shows the day and an approximate time only:
  "🕰️ Day 5, morning". It has no buttons, and no timers (effects stay DM-screen content). It is
  edited in place when the clock moves. One short line is posted when a rest finishes or a new
  day begins, and nothing else.
- **The DM's clock.** The DM screen keeps the exact time ("Day 5, morning (6:20)") and the
  controls. **Short rest, Long rest and Skip to dawn leave the pinned DM-screen message** for the
  private **Rests & time…** panel (an ephemeral form only the DM sees). Rests are rare buttons,
  because DMbot hears them, and players who peek behind the DM screen could take rest *buttons* for
  D&D Beyond character-sheet links. The label **Rests & time…** itself is fine on the pinned message
  (Supervisor, #1197): it opens that private panel and is not a rest button.
- **Approximate times** (game time, fixed hours, no seasons yet):

  | Time | Word |
  |---|---|
  | 03:30–04:59 | pre-dawn |
  | 05:00–06:14 | dawn |
  | 06:15–09:59 | morning |
  | 10:00–11:29 | late morning |
  | 11:30–12:59 | about noon |
  | 13:00–14:59 | afternoon |
  | 15:00–16:59 | late afternoon |
  | 17:00–17:59 | early evening |
  | 18:00–18:59 | dusk |
  | 19:00–20:29 | evening |
  | 20:30–21:59 | late evening |
  | 22:00–23:14 | night |
  | 23:15–00:44 | near midnight |
  | 00:45–03:29 | late night |
- **The clock follows what is said** (voice transcripts of people who agreed, as today):
  - When **the DM** clearly says a rest starts or ends, or how much time passes, the clock moves
    by itself, with an Undo note on the DM screen. Examples: "you finish your long rest", "an
    hour later", "the next morning".
  - When **a player** suggests one ("let's take a short rest"), the DM screen asks the DM with one
    button. The DM decides.
  - Vague narration ("you walk for a while") is never guessed. At most it is a suggestion on the
    DM screen. **Built (#1758), off by default (`DMBOT_TIME_SUGGESTIONS=1`) until dev1 has run a live
    check:** a DM's line (said or typed) that the strict phrases don't match but that holds a time
    cue ("a while", "hours", "some time", "all day", "all night", "the afternoon", "evening falls",
    "by nightfall", "travel", "journey", "march", "rest", "camp") is sent to the smallest AI model
    alone with the game's time, asked for `{"minutes": N or null, "why": "..."}`. An answer of 10 to
    1,440 minutes (anything else is dropped, and the line is quoted data, never instructions) is
    rounded to a tidy step (10 or 30 minutes, then whole hours) and the DM screen offers
    `⏳ "you walk for a while": about 2 hours? [+2 hours] [Not now]`. Only a DM of the campaign can
    press it; it goes through the clock's own path (the note, the players' clock, an Undo). Ignored
    or "Not now" changes nothing, and the same cue is not offered again for 10 minutes. Only while
    a clock is set, not in a fight, not while the party is split; only for a DM recorded now; paid
    plans only (the check fails closed); at most one a minute and 20 AI calls a session for a
    campaign; a failure or an 8-second wait gives no suggestion, and three failures in a row rest it
    for 10 minutes. A line the off-topic filter holds for a label waits for it, and is offered only if it is labelled
    game talk (never an off-topic line). Players never see it and it is never in a transcript. For
    dev1's live check: "rest" and "camp" also match chatter ("the rest of you"), which the caps bound. One log line at the
    session's end, "Time suggestions: N calls, … tokens", counts and never words.

  **Typed chat counts too** (owner, 2026-10-13), as described under "Typed chat at the table".
- **Players see only the approximate time** (owner, 2026-10-13): never `h:mm`, in any message.

**Typed chat at the table (owner, 2026-10-13).** DMbot reads what is typed at the table as well
as what is said, so the clock and the other listeners hear "we take a long rest" either way.
- **Where:** only the table voice channel's own text chat, plus at most one text channel the DM
  picks in ⚙️ Settings ("Table chat"). Never any other channel, and never between campaigns or
  servers.
- **When and whose:** only while DMbot is listening, and only messages from people who agreed to
  be recorded. Bots are never read.
- **What happens to them:** a typed line goes to the same listeners as a spoken one (clock phrases,
  house rules, names heard, "Hey DMbot"). It goes into the transcript under the name of whoever wrote or
  pasted it, tagged `(typed)`: the raw one, and (owner, 2026-10-22) the cleaned one too, where off-topic
  typing is hidden like off-topic talk. What is typed there is **revealed** to the players, as anything said
  in the voice channel is. Commands and DMbot's own messages are skipped. Only people who agreed to be
  recorded are read (consent), while DMbot is listening.
- **Images posted there** (owner, 2026-10-22). An image someone posts in the table chat DMbot reads (same
  channels, same consent, while listening) is sent once to the AI to be described:
  - **The transcript gets a description** in place of the image, under the poster's name and tagged
    `(image)`: "(image) A torn letter with a wax seal." It counts as revealed, like anything typed there.
    The image itself is never stored; only the words are kept.
  - **A map:** the description names the places on it and says, in plain words, where each one is from the
    others (north of, across the river from, two days' ride east of, inside), with any scale, roads, rivers
    and labels shown. It is detailed enough that the layout could be drawn again from the words alone, and it
    only says what the map shows, nothing guessed. It is kept as a map description for **MapBot**, a later
    feature that draws simple local and regional maps from asserted facts (nothing is built for MapBot now).
  - **A picture of text** (a handout, a note, a page): DMbot replies in the channel with the text it read,
    "Text in this image:" and the words, so the table can check it. **✏️ Fix** lets the poster or a DM correct
    it, and the transcript keeps the corrected text.
  - **Limits:** images only (png, jpg, webp, gif's first frame), up to about 10 MB and a few per message;
    a campaign's images count toward its AI use, and past the day's limit an image is noted as "(image, not
    described)". The fast model describes, and a map uses the careful one. The DM's private channels are
    never read.
  - **Consent:** the consent message says DMbot also reads images posted in the table chat, so the consent
    terms version goes up and everyone is asked again.
- **Consent:** the consent message says DMbot also reads the table's typed chat while it listens.
  That changes what people agree to, so the consent terms version goes up and everyone is asked
  again.
- **Discord:** this needs the guild-messages intent and the privileged **Message Content** intent.
  The owner switches Message Content on in the Developer Portal (Bot, Privileged Gateway
  Intents). Once DMbot is in 100 or more servers, Discord must approve that intent, so it is part
  of the go-live checklist (#498).
*Built, part 5: typed chat at the table (2026-10-13, #1118):* the bot asks Discord for guild messages and the privileged
Message Content intent (`bot.py`), and `DMBot.on_message` sends every server message to `_hear_typed`, which drops
it unless it is in the table voice channel's own chat or the campaign's one extra "Table chat" channel (`table_chat.py`),
the session is listening, the campaign's Table chat switch is on, the author is not a bot, webhook, DMbot or a
slash command (only plain messages and replies are read), and the author has consent (`has_consent`, the same
check as a voice line). A typed line goes through `_act_on_line` like a spoken one (rules cards, house rules,
the game clock), and also into names heard, vocabulary, the sidebar's "Hey DMbot" and the raw transcript as a
`Line(typed=True)`: tagged `(typed)` in both files (2026-10-23, #2447: the cleaned one leaves out typing the
off-topic filter labels off-topic, through the same window as talk; its words reach the names scan and the word
list when typed, never again at the label or at "Put it back"), never in the live transcript channel; it has no
audio check and no cleaning. Typing over the rate limit skips the filter, so it always shows. ⚙️ Settings has **💬 Table chat** (campaign DMs and server managers): a
private panel that says which channels are read, picks the one extra text channel of this server, and turns it off.
Migration 0052: `campaigns.table_chat` (default on) and `table_chat_channel_id`, `transcript_lines.typed`, and a new
sidebar way-in `table-chat` (a typed "Hey DMbot" has no speech-to-text in its lineage). A backup carries the on/off
switch only (a channel ID means nothing in another server; a restore keeps the campaign's own extra channel). Messages
are read as Discord shows them (`clean_content`: mentions as names). A message deleted before its line is saved is taken
back; a saved line stays. No campaign's DM screen, transcript or game-time channel in the server can be the extra channel
(refused when picked, and again when a message arrives, from a one-minute cache). One line in the extra channel and
"and what they type in this chat" in the voice channel notice tell people it is read. The consent terms went to version 5
(one added sentence), so everyone is asked again. **Owner step after merge:** switch on Message Content Intent at
https://discord.com/developers/applications (the DMbot app, then Bot, then Privileged Gateway Intents, then Message
Content Intent, then Save Changes; not Reset Token). The privacy page and help wording are WebDev's follow-up.
An edited or deleted message is not read again, and a typed line the person deletes later stays in the raw transcript, as a
spoken line does (the raw transcript is unedited). DMbot keeps no message cache (`max_messages=None`), so the text of
messages it does not read is never held.

*Built, part 3: the players' game-time channel (2026-10-13, #1116):* `dmb-gametime-<short name>` is made
or repaired at every `/dmbot start` next to the DM screen (`dm_screen/gametime_channel.py`, on the
transcript channel's permissions: everyone reads, only DMbot posts; a failure is a note on the DM
screen and never stops the session; existing campaigns get it at their next start). Migration 0050
adds `campaigns.gametime_channel_id` and the players' message ids on `game_clocks`; none goes into a
backup. The players' pinned message is "🕰️ Day 5, morning": the day and one of 14 approximate words
(`timebot.clock.day_part`, PLAN's hour table, "near midnight" spanning midnight), never the exact
time, a timer or a button; it is edited in place wherever the DM's clock changes (a button, a rest
said at the table, Undo). One short line is posted there when a rest finishes ("Long rest done. Day
6, dawn.") or a new day begins, and nothing else (no tired line, no effect or dawn/noon/dusk lines).
The DM's pinned clock reads "Day 5, morning (6:20)" and keeps +10 min, +1 hour, Rests & time… and Start a
timer; **Short rest, Long rest and Skip to dawn** moved into the private panel **Rests & time…** opens
(an ephemeral message with them and "Type the time…").
*Built, part 4: the clock follows what is said (2026-10-13, #1117):* still phrases only, no AI, only consented
speakers' transcript lines, only while listening. **The DM's words that move the clock by themselves**,
each with the Undo note: a rest *finishing* ("you finish your long rest", "the long rest is over", "after
your short rest"; one rest said as starting and as finishing counts once), "the next morning" / "at dawn"
(to the next dawn, like Skip to dawn), and explicit amounts up to 24 hours ("an hour later", "ten minutes
later", "half an hour later", "forty-five minutes later"; words and digits; "an hour and ten minutes
later" is two amounts and is not guessed at). Vague narration ("you walk for a while", "a few hours later"; at most a suggestion, #1758)
and any question, wish, condition or long line are ignored. **A player's suggestion** ("let's take a short
rest", "can we long rest?") moves nothing: it posts one DM-screen line, "Mira suggests a short rest", with
**Short rest** and **Not now**, at most one per campaign every 5 minutes, and only once a clock is set; only
a DM of the campaign can press them. The players' clock and rest/new-day lines (#1116) follow every change.
**Never counted twice (Supervisor, 2026-10-13):** while the last clock change was a rest said at the table
and the clock is still where it left it, a finish of the same kind adds nothing (however long ago), and an
amount said soon after adds only what is more than the rest took ("you take a long rest", then "ten hours
later" is +2 h on top). A button, an Undo or any other change forgets it. "The next morning" and "at dawn"
count only as the narration's own cut ("At dawn, you…", "The next morning, the party…") or standing alone,
never an NPC's line ("At dawn, meet me at the gate"). A press from a suggestion that fails says so and leaves
the buttons up; the heard words are shown with Discord's markdown escaped.

**DM sidebar: quick answers for the DM (owner, 2026-10-09; replaces the 2026-10-04 note).**
DMbot is there to help the game move quickly, never to bog it down or distract. The
sidebar is the DM's shortcut to an AI that knows this campaign and knows DMbot, so the
table isn't left waiting while the DM looks something up.
- **Two ways in.**
  1. **A voice memo:** while the table plays, the DM mutes in the voice channel and holds
     the mic button in their private chat with DMbot (Discord's voice messages; on a
     computer, where Discord has no voice messages, they type the question there instead).
  2. **Said out loud at the table:** the DM says they need to look something up ("hold on,
     I need to find if you need line of sight for fireball"). Only the campaign's DM's own
     lines start this, and only a clear "I need to find / look up / check" request.
- **Heard like everything else:** a memo goes through the same speech-to-text and the same
  name fixing (the campaign's names list) as table speech. Only from a DM who has agreed to
  be recorded; a DM who hasn't is asked to agree first.
- **What the AI knows:** everything about this one campaign (campaign memory, transcripts,
  house rules, homebrew, the scene so far, content the DM shared with the right-to-use
  confirmation), the rules in the hierarchy below (house rules → homebrew → target →
  fallback), and how DMbot itself works (DMbot's public documentation from its repository,
  shipped with each build). Never another campaign's or server's data (isolation rule).
- **Answers are as short as possible.** If "yes" or "no" answers it accurately, that is
  the answer. No elaborating unless the DM asks. Example: "Fireball has no sight
  requirement: its origin is 'a point you choose within range'." The full spell text (and
  a link to read it outside Discord) comes only when asked for ("I need the spell
  description"). Rules answers still carry their source and how confident DMbot is, in a few
  words.
- **Only on topic:** the campaign, the game's rules and content, DMbot itself, and Discord
  mechanics. Anything else gets one short line saying it can't help with that here.
- **Where answers go:** privately, in the DM's chat with DMbot, never in a public
  channel. *Changed 2026-10-14 (owner):* this stays true for the **voice memo and the DM's
  private chat**. A question asked **at the table** ("Hey DMbot…", "hold on, I need to
  find…") or **typed in `dmb-chat`** is answered in `dmb-chat`, for everyone; see "Table
  questions: anyone may ask" below.
- **Transcripts:** the DM's sidebar lines are added to the **raw** transcript with the tag
  `[DM Sidebar]`, and DMbot's replies about in-game content are added under the speaker
  name `DMbot`. Both are always kept out of the **cleaned** transcript. **The raw transcript
  is unedited and unredacted, sidebar included, for everyone who may read transcripts**
  (owner, 2026-10-09, #933): a player who downloads the raw copy gets spoilers, and that is
  accepted. Its purpose is to be the complete data DMbot is improved from as the project
  evolves; the cleaned transcript is the one for reading. Sidebar lines are not posted
  live in the transcript channel (they would interrupt the table), only written to the
  raw record.
- **Data lineage on every sidebar line** (owner, 2026-10-09, #933): each line records
  where it came from, so later work knows what data came from where: `via` = `voice-memo`,
  `typed`, or `table-trigger` (said aloud at the table); for a memo or trigger, the
  speech-to-text engine and model; for a DMbot reply, the AI model, the prompt version,
  and the sources it used (rules entries, house rule numbers, memory facts, transcript
  span); and the id of the question a reply answers. In the raw text a line reads
  `[time] (DM name) [DM Sidebar via=voice-memo stt=<engine>]: …` and
  `[time] (DMbot) [DM Sidebar reply-to=<id> model=<model> sources=<…>]: …`, with the same
  fields stored as columns, not only text.
- **Cost:** every answer spends AI tokens, so it goes through `can_use_ai` (#919).
- **Built (#935, the ways in; the answers are #934):**
  - **In the DM's chat with DMbot** (`dmbot.sidebar.service`): a voice message or a typed
    message from a campaign's DM, while a session of theirs runs. A voice message is read in
    memory (PyAV, now a normal dependency), written down by the table's speech-to-text with
    the campaign's names as hints, and dropped at once. Names are fixed the way table speech
    is (only the sure ones). Any of the campaign's DMs (not just whoever pressed Start) may use it. A DM who has not agreed to be recorded is asked first, with the
    consent button; nothing is downloaded or kept. If the DM runs several games, a button asks
    which. One question at a time. The reply shows what was heard (voice only).
  - **Said at the table:** a DM's own line, in the live transcript, that has a hold-on
    lead-in, then "I need to / I have to / let me", then find, look up or check, then
    something (`dmbot.sidebar.ask`, with tests for lines that must not start one). At most one
    a minute per table. The answer goes to the DM's private chat, never a channel.
  - **Transcript lines:** the question and DMbot's in-game answer are saved with the line
    kind `question` / `answer` (migration 0036; answers are saved under the DM's id so a
    consent stop removes both), shown in the as-heard file for everyone who can read it as
    `(DM name) [DM Sidebar id=… via=… stt=…]:` and `(DMbot) [DM Sidebar reply-to=… model=…
    prompt=… sources=…]:`. Where each came from is stored as columns (how it came in, the
    speech-to-text, the question it answers, the AI model, the prompt version, the sources).
    Never in the cleaned file, the live channel, or the session counts. Backups hold no
    transcripts, so they hold no sidebar lines.
  - **Cost limits:** nothing is downloaded, written down or asked while the answer engine (#934) is missing. A voice message may be up to 30 seconds. Questions are one at a time, and at most **6 a minute and 120 per listening session**
    per table, spoken, typed and voice-noted alike (owner, #1040, 2026-10-10; the old
    one-a-minute limit for spoken questions is gone because a clear wake phrase makes an accidental
    question unlikely, and this limit keeps a looping line from running up AI cost). Past it, one short note:
    "That's a lot of questions at once. Ask again in a minute." A voice message's speech
    counts in the end-of-session "sent" line for outside engines, not in the hours meter.
  - **Switch:** the ways in answer only when `DMBOT_SIDEBAR=1` (default 0, and it needs
    `ANTHROPIC_API_KEY`); off, a DM gets "Quick answers aren't switched on yet." and the table
    trigger stays quiet. Turn it on after the sidebar's test answers have been read (#954). One
    question at a time per campaign.
  - **A line said as an NPC never asks DMbot (2026-10-16, #1624):** the spoken triggers ("hold on, I
    need to find…" and "Hey DMbot") fire only when the line's `entity` is empty or narrating; a line
    said as a named NPC never does. The label comes from the speaker-label AI call over a window of
    the DM's lines, so it is not known when a line is first handled: for a line that clearly asks
    DMbot something, the window is closed at once and the question waits for that one call (bounded
    by its timeout), then is asked unless the line was said as an NPC. With no label (no AI, no names
    to choose from, a "maybe", a failed or slow call) it is asked as before. Typed questions and voice
    memos are always out of character and unaffected.
  - **Limits:** DMs reach only the first shard's process, so a table on another shard's process
    is not found (single process today).

*Built, part 1: the answer engine (#934, CloudDev, 2026-10-09):* `dmbot.sidebar` (no Discord, no
voice). `bot.sidebar_answers.answer(campaign, question, asker_id=..., scene=...)` returns an `Answer`:
`text` (short, with its source and "confident" or "uncertain"), `in_game`, `refused`, `model`, `prompt_version`,
`sources` (everything the AI was given, in short words: "SRD 5.2.1 p. 131", "house rule 3",
"campaign names", "the scene", "DMbot help"), `parts` (a long answer as messages) and `seconds`.
`asker_id` has no default: it decides the wording of a plan refusal.
- **Brevity is enforced in code.** At most 2 sentences and 200 characters (the source and "confident" or "uncertain"
  come after, outside the limit), unless the DM asked for the full text. A yes-or-no question
  starts "Yes." or "No." (or says plainly it can't: "Your call."; a choice question with "or" is
  not a yes-or-no one). Greetings and "let me know" offers are stripped; "Sure, it can." becomes
  "Yes, it can.". A reply that breaks a rule is sent back **once** with what to fix, unless the
  first call was already slow (over 3 seconds) or the retry fails; what is still too long is cut
  at a sentence end. A paragraph is never sent.
- **Speed.** The smallest model (the off-topic filter's), at most 150 tokens out. One call is
  limited to 6 seconds and the whole answer to 8; a warning is logged over 5. A stuck call ends
  in "That took too long. Ask again." The names are waited for at most 1 second; if they or the
  house rules can't be read, the answer goes ahead without them and the reason is logged.
- **Context is this one campaign only:** the rules entries and house rules the question names
  (a possessive like "Fireball's" counts), the confirmed names it mentions (never secret or
  unconfirmed names, and nothing but the name for someone who has a secret name, since replies can
  reach the raw transcript, #933), the caller's last few minutes of scene (cut to 1,500
  characters, fenced as the players' words, which are information and never instructions) and,
  only for questions about DMbot or Discord, the reviewed digest `sidebar/about_dmbot.md`.
  Only matching entries are sent, each cut to 1,200 characters. A campaign's homebrew entries that are on are sent (#1520); content a DM shared counts only with its right-to-use confirmation and none is
  stored as text yet, so none is sent.
- **Sources are DMbot's, not the model's.** The shown source is taken from what the model was
  given: an SRD answer cites the entry's own page and carries `[Legacy 2014]` when it is a 2014
  entry; a house rule is named as given; "DMbot help", campaign names and anything the model made
  up show no source; a source the answer already says is not repeated.
- **Off topic** gets the fixed line "I can only help with the game, DMbot or Discord here."
- **The full text comes only when asked** ("spell description", "full text", "read me the
  whole…") and only for a rule DMbot has: it is the card `/dmbot rule` shows, in parts, with the
  SRD's own page to read outside Discord, with no AI call. Asked for something it doesn't have,
  the question is answered short like any other.
- **Plan.** Every call goes through `plan_gate("ai", …)` (#919): with plans enforced a refusal
  comes back as `refused=True` with the plain words. Like the start check it fails open if the
  database can't be read, so during an outage a lapsed plan can still spend a few cents.
- **Tests.** `tests/sidebar_brevity_cases.py` holds 17 real table questions; CI runs them with a
  fake AI and counts the sentences by hand. `python -m dmbot.devtools.sidebar_check` runs the same
  ones against the real model (it needs the server's key, so dev1 runs it) and prints the
  answers and times.

*Built, accuracy fixes (#992, CloudDev, 2026-10-10; prompt version `sidebar-2`):* from dev1's run of
the 17 test questions against the real model. (1) **Consistency:** when the question names a rules
entry that was given, "the free rules don't say" is a contradiction: the prompt says so, the
answer is sent back once with the entry named, and if it still says so DMbot answers with the
entry's own first sentences and its source (only for a spell or condition the question names, and
never over a house rule; so "do you need line of sight for fireball" and the
same question after "hold on, I need to find" agree). (2) **No unsourced certainty:** a rule stated
with no source among the entries and house rules the model was given gets "(not in DMbot's rules,
check your book)" and is never "confident"; the prompt asks for the same wording for general D&D
knowledge (class features, cover, area of effect, which the index doesn't hold yet); the code adds the
note itself and the answer from the scene or the campaign's names is not marked. (3)
**Editions:** a question that says "2014", "2024", "legacy", "old", "new" or compares them gets
both editions' entries, the older tagged `[Legacy 2014]` ("is the 2014 goblin different" sees
Goblin and Goblin Warrior). `sidebar_check` now also checks each case's must-say and must-not-say
words. Not done: adding general rules or class features to the index.

*Built, accuracy part 3 (#1005, CloudDev, 2026-10-10; prompt version `sidebar-3`):* (1) **Each fact cites
its own source:** a house rule is cited only when it says what the answer says (the answer's real
words, bar one, must be in the rule); otherwise the cite is dropped and the answer gets "(not in
DMbot's rules, check your book)
and is never "confident" ("a spell attack can crit on a 20" is not house rule 3).
(2) **Older edition always tagged and named:** when the AI was given a `[Legacy 2014]` entry, the source names
both entries by their own names and pages ("Goblin Warrior SRD 5.2.1 p. 290; Goblin SRD 5.1 p. 315
[Legacy 2014]"). Brevity is unchanged; tags and sources are added after the cut.

*Built, confident / uncertain (#1071, CloudDev, 2026-10-11; prompt version `sidebar-5`):* the DM sees
"confident" or "uncertain" after the source, never "sure" / "not sure" (owner, 2026-10-11): "Range 150
feet. (SRD 5.2.1 p. 131, confident)". Wording only: when an answer is confident is unchanged (a given
entry or house rule must say it). The AI's reply field is `CONFIDENCE: <confident or uncertain>`; the
old `SURE:` form is still read and mapped across, and `devtools/recheck.py` reads saved answers with
either wording.

*Built, header facts (#1039, CloudDev, 2026-10-10; prompt version `sidebar-4`):* each rules entry sent to
the sidebar's AI now starts with its facts (a spell's level, casting time, range, components and duration; a
creature's size and type, AC, HP, speed, CR, senses and initiative), which the entries keep apart from their text.
Range, casting-time and duration questions were answered "not stated". The facts are outside the text cut and count
as the entry's own words for the SRD-citation check (#1015). A condition has none.

*Built, an entry's metadata is part of the entry (#1063, CloudDev, 2026-10-11; owner):* an entry's metadata (a spell's
level, casting time, range, components and duration; a creature's size and type, AC, HP, speed, CR, senses and initiative) is part
of the entry wherever DMbot reads, searches, quotes or checks it. One place, `rules/facts.py`, knows the fields; an entry gives
`facts` (the header line) and `full_text` (the facts, then the text). The sidebar's prompt and its citation check, its fallback
answer (a question that names a field gets that field: "what's the range of fireball" gets "Range 150 feet") and the rule card
all use it, and a test fails when a new reader of a rules entry's text skips it.

*Built, wake phrase (#1040, CloudDev, 2026-10-10; corrected by the owner the same day):* besides "Hold on, I
need to find…", the campaign's DM can call **"Hey DMbot, …" anywhere in a line** (people talking fast run it into
what came before: "…so they're in the cave, hey DMbot what's the range of fireball"); the text after it, to the
end of the line, is the question. A **bare "DMbot"** also calls it, but only when a pause or comma follows or the
next word asks ("DMbot, what…", "DMbot is fireball…", "DMbot check…"); "…and DMbot said earlier…" or "the DMbot
screen" do not. With two calls in a line, the last real one asks. Only the DM (a player's line does nothing), never a
line tagged in character. *Changed 2026-10-14 (owner): any consented player may now call "Hey DMbot" too, and the
answer goes to `dmb-chat`; see "Table questions: anyone may ask".* Speech-to-text spellings, matched on word boundaries: DMbot, DM bot, D.M. bot, D M bot,
DM-bot, Dee em bot, the M bot (a constant in `sidebar/ask.py`). The answer stays private (the DM's chat only) and
the raw transcript keeps its `[DM Sidebar]` lines.

**Table questions: anyone may ask (owner, 2026-10-14).** Until now only the DM could ask DMbot, and
only the DM saw the answer. Now **anyone at the table** may ask, and a question asked at the table gets its
answer **where the whole table can read it**, so nobody waits while someone looks something up.
- **Two ways to ask:**
  1. **Say "Hey DMbot, …"** at the table (the wake phrase above, now from anyone who agreed to be recorded,
     not only the DM). "Hold on, I need to find…" stays the DM's alone: a player saying "I need to check my
     sheet" is not a question to DMbot.
  2. **Type the question in `dmb-chat-<short name>`**, the campaign's DMbot chat channel. Every message there
     is a question; no "Hey DMbot" needed. A typed "Hey DMbot, …" in the table chat (#1152) also asks, and its
     answer goes to `dmb-chat` too.
- **The channel:** DMbot makes `dmb-chat-<short name>` at `/dmbot start`, next to the game-time channel, the
  same way (repaired if missing; a failure is a note on the DM screen and never stops the session). Everyone in
  the server can read it and type in it. Its topic says, in plain words: "Ask DMbot a quick rules question.
  Your DM has the final say."
- **The answer, as short as possible** (CLAUDE.md, "Quick, short and on topic"):
  ```
  Q (Mira): Does fireball need line of sight?
  Answer: No. You pick a point within 150 feet. (PHB 2024, sure)
  ```
  - **Q:** who asked (their name at the table: the character name when one is linked, else their server
    nickname) and the question **paraphrased and shortened** to one line.
  - **Answer:** the briefest true answer ("Yes." when yes answers it), with its source and how sure DMbot is in
    a few words (CLAUDE.md, "Citations"). Elaborates only when asked again.
  - **At most one link**, and only to an official source on **dndbeyond.com**, or to a **public link shared
    with this campaign** (the house-rules file of record above, or another link the DM gave DMbot for this
    campaign). That is the preferred link when the answer comes from the house rules. Never any other site,
    never a link from another campaign.
- **Nothing secret goes into `dmb-chat`.** Its answers use only what the whole table may know: the rules
  (house rules → homebrew → target → fallback), how DMbot and Discord work, and what was said at the table
  (transcripts are already shared). **Never DM-screen content:** secret names, DM notes, NPC and plot notes,
  PlotBot/NPCBot facts the players haven't heard, or a shared adventure's text.
  - A **player's** question that needs any of that, or asks DMbot to decide something ("can I sneak past
    them?", "what's behind the door?"), gets: "That's one for your DM." DMbot never makes the DM's call, never
    invents story.
  - A **DM's** question that needs it is answered **privately**, in the DM's chat with DMbot, and `dmb-chat`
    shows "Answer: Sent to the DM privately."
- **Rulings stay the DM's:** an answer in `dmb-chat` is rules information with its source, not a ruling.
  DMbot still never posts the DM screen's alerts or house-rule prompts to players (CLAUDE.md, "DM authority").
- **Who and when:** only while DMbot is listening to that campaign; only from people who agreed to be recorded
  (anyone else who types in `dmb-chat` gets one short reply with the agree button, once per session); bots
  are never answered. Only the campaign's own channels count, never another campaign's or server's.
- **Limits:** the sidebar's per-table limit stands (one at a time, at most 6 a minute and 120 per listening
  session, spoken and typed alike), and **at most 2 a minute per person**, so one player can't use up the
  table's questions. Past either: "That's a lot of questions at once. Ask again in a minute." Every answer goes
  through `can_use_ai`. Still behind `DMBOT_SIDEBAR=1`.
- **Off topic** (not the campaign, the game's rules and content, DMbot or Discord): one short line saying DMbot
  can't help with that here.
- **Transcripts:** like the DM sidebar, the question and answer go into the **raw** transcript only, with
  their data lineage: a player's as `[Table Q via=table-trigger|chat …]`, a DM's keeps `[DM Sidebar …]`;
  DMbot's answer under `DMbot` with `reply-to`, model, prompt version and sources. Never in the cleaned
  transcript or the live transcript channel.
- **Consent:** the consent message adds that questions typed in `dmb-chat` are read, so the consent terms
  version goes up (everyone is asked again once).
- **Built** (#1187, CloudDev; the site's help and privacy pages with #1155, WebDev).

**Who pays for AI and speech (decided 2026-10-04, replaced 2026-10-07).** Bring-your-own
keys is dropped: it asked ordinary DMs to open developer accounts, fund them and paste
keys. DMbot runs on the operator's keys and bills **by hours and campaigns** through the
customer website (`web/`, #431–#435); the plan rules in the bot are #437. The "who pays
for this call" seam stays, pointing at the operator's keys.

*Built, out of funds (2026-10-10, #972):* when the AI account has no credit or reaches its
spend limit, every AI call (Find names, the off-topic filter, the sidebar, the cleaner) raises
`AIOutOfFunds`, and the DM hears "DMbot's AI is paused right now (its account needs topping
up). Nothing was changed. You can keep playing; the AI features come back once it's fixed."
Two named admins, `DMBOT_ADMIN_PRIMARY_ID` (the owner) and `DMBOT_ADMIN_SECONDARY_ID` (a
backup), each a Discord user ID of 17 to 20 digits or empty (anything else stops start-up,
naming the setting and never the value), get one private message with full context, at most
once a day each, remembered in a small file in the data folder (`ai_notice.json`) so a restart
doesn't repeat it; a failed send to one doesn't stop the other, and with neither set it is
only logged. The error line is logged at most once an hour, and once a day one INFO line gives
the last 24 hours' AI calls and tokens (counts only). In `dmbot.ai_watch`.

**Plans and pricing (owner decisions, 2026-10-07).** The plan belongs to one Discord user
(the DM); every campaign has one owner whose hours and campaign count it uses; co-DMs
need no plan; "Hand over this campaign" offers ownership to another member of the
server, who needs a working plan only to accept. Hours are
DMbot's listening time, start to stop, rounded up to the minute, pooled per month, no
roll-over. Every plan has every feature; only hours and campaigns differ, except that
Try It has no backups or downloads. The site keeps the words in one place; the bot's
messages use the same ones.

| Plan | Hours a month | Campaigns | Price |
|---|---|---|---|
| Try It | 8, for 30 days, one per Discord account | 1 | free |
| Table | 18 ("about 4 hours a week") | 1 | $8.99 (no $1.99 first month since 2026-10-14) |
| Two Tables | 43 ("about 10 hours a week") | 2 | $17.99 |
| Guild | 87 ("about 20 hours a week") | 5 | $34.99 |
| Pro | 217 ("about 50 hours a week"); needs the bigger server | 20 | coming soon |
| Extra hours | +10 this month | — | $4.99 |

*The hours rules, replayed offline (2026-10-11, #437):* `python -m dmbot.devtools.replay --session
FOLDER --plan PLAN --hours-used N [--grace] [--session-minutes N] [--hours-only]` puts a made-up owner
on a plan with some hours used, in a throwaway schema (never the live one) with enforcement on, and
runs the session through the bot's own hours rules and meter. It shows the 80% and 90% warnings, the
cap finish (the 2-hour grace, once), the stop and the refusal at the next start, in the bot's exact
words; Try It at the cap is refused at start. dev1 runs it once before the go-live switch (#498). How
to run it: docs/test-scripts/README.md.

*How extra hours are bought (2026-10-11, #1089):* an **Add 10 hours ($4.99)** button on the account
page, under the hours left, shown only over a paid plan that works now (active, or in the 7-day
grace). It calls `POST /billing/extra-hours`, which answers `{url}` like checkout, from the payment
provider's `extra_hours_checkout_url` (the same billing rate-limit group); Try It, a lapsed plan and
no plan get `no_paid_plan`, free access and grants get `has_free_access`. The purchase arrives as
the signed `extra_hours_bought` event, which the writer applies to this period only (a new period
starts at zero). The page says so in one line: "You pay once, on our payment company's page. These 10 hours
are for this month only and don't roll over." The bot's "hours are used up" message to the owner already points to the account page.

**Free access and the admin page (owner decision, 2026-10-08).** The owner's own Discord
account never needs a plan: it is listed in the server's settings (`DMBOT_FREE_USERS`, Discord
ids, never in the repository) and counts as having every feature with no hours or campaign
cap. The owner can also give other Discord users free access by hand from an admin page on
the website: a *grant* names the Discord id, a level ("like Guild": 87 hours and 5 campaigns,
the default, or "no limits"), an optional end date and a short note. Reason for the default:
every hour spends AI and speech-to-text money, so unlimited access for others is a choice the
admin makes, not the default. Grants live in their own table, never in `entitlements`, which
stays the payment company's truth; wherever DMbot asks "does this person's plan work, and
with what caps", a grant or the free list counts, and the better of a grant and a paid plan
wins. A grant ends on its end date or when revoked, and the person falls back to whatever
they pay for. Grants and revocations are logged (who, what, when: Discord ids and the
admin's own email). Nobody sees a
price or a payment button while a grant covers them; the bot and the account page say "Free
access". Deleting an account deletes its grant. *Built, part 1 (#771, dev2):* the free
list is read at start by the bot and the web API (only its count is logged); grants live
in `access_grants` (migration 0030; one row per Discord id, no link to `web_users`) with
an add-only `access_log` (Discord ids, and the admin's own email: the one email it
holds), both written only through `Database.grant_writer()`, which only `dmbot.web.grants`
opens (a test checks); the one exception is deleting an account, which deletes the
person's own grant (`own_delete`). A person reads only their own grant.
`entitlements.effective()` is the one answer every plan rule asks (`plan_works` today;
the hours meter and campaign cap when they come). `/me` gains `access: {kind, endsOn?,
stillPaying?, paidPlan?}` with one kind for people, "free", whether from the list or a
grant (people see "Free access", never why); `stillPaying` when a paid plan still works
alongside, so the page can offer to stop paying. Checkout and Try It answer
`has_free_access` for covered people (a covered person's one trial isn't used up). *The
hours meter (#437 part 2), decided:* the free list has no meter; a Guild-level grant has
Guild's hours, its month running from the day the grant started (as a paid plan's runs
from its billing date), and a grant that ends mid-month just stops; a grant overlapping a
paid plan uses the larger caps and the paid plan's month.
*Free-access follow-ups (2026-10-11, #824):* (1) **A cancelled plan says so.** The payment
company's cancel event (Lemon Squeezy: `subscription_updated` or `subscription_cancelled` with
status `cancelled`, and `ends_at`) is stored as `entitlements.ends_at` (migration 0048); the plan
keeps working to that date; a renewal or a resume (status `active`) clears it. `/me` then sends
`plan.endsOn`, and under free access `paidPlan` with `paidPlanEndsOn` ("Your Table plan ends on
Oct 14.") instead of `stillPaying`. (2) **`stillPaying` only when the billing page can open:** a
plan with no payment-company customer (set by hand) is not offered "Stop paying"; the line reads
"Tap Stop paying, then cancel on the payment page, if you don't need it." (3) **Hours on the
account page come from the meter now.** `/me` read `hoursUsed: 0` always; it asks the database
function `dmbot_hours_used(month_start)` (0048: the signed-in person's own minutes in one month,
nothing else, the table stays closed to the website's role). A capped grant ("like Guild") shows an
hours line and bar (`access.hours`), a no-limits person none. (4) **Words:** a no-limits free
person's campaigns are tagged "You run it", a hand-over offer reads "If you accept, it becomes
yours and <name> stays on as a DM", and nothing says hours or slots; a capped grant keeps the hours
wording.
**Admin sign-in:** only addresses in `ADMIN_EMAILS` (server settings) may sign in, either with
Google ("Sign in with Google", verified email only) or with that email and an admin password
whose hash (argon2id) is in the server settings, never in the database or the repository; a
helper script sets it. Five wrong tries in 15 minutes lock that address and that connection
for 15 minutes. The admin session is its own cookie (HttpOnly, Secure, SameSite=Strict), ends
after an hour idle and 12 hours at most, and every admin form carries a CSRF token. The
admin page is never linked from the site and tells search engines not to index it.

**The admin page goes online first, alone (decided 2026-10-08, #833).** The web API reaches
the internet through a Cloudflare Tunnel (`cloudflared` in compose, outbound only, no
published ports, so `CF-Connecting-IP` can be trusted), and until the customer website
goes live (#498) the tunnel opens only `^/admin(/|$)`: Discord sign-in, `/me`, billing and
the webhook stay closed. The admin page is served from the development branch's build at
`dev.getdmbot.com` (noindex), because the admin cookie is only sent when the page and
`api.getdmbot.com` share a site. *Corrected 2026-10-09 (#833):* the Cloudflare Pages
project's production branch is `development`, so until go-live getdmbot.com, www and
dev.getdmbot.com all serve that one build (noindex, the real API, admin paths only);
`PUBLIC_DEV_API_BASE` is set for Production too. At go-live (#498) production moves to
`main` after a promotion and `dev.getdmbot.com` moves to the `development` preview (with
a bypass for Pages' preview login). When the site goes live,
`WEB_SITE_URL` becomes `getdmbot.com` and the path limit comes off. Built in #836 (tunnel)
and #837 (the dev site's API address).

**The API limits its own routes (2026-10-11, #1082, #498 item 1).** The proxy rules (Cloudflare or
Caddy) are the first line and are dev1's at go-live; the API holds a second line so it is safe
even if the proxy is set up wrong. `dmbot.web.limits` is an in-memory sliding count per address
(the proxy's header only; IPv6 is counted by its /64 block; a missing or garbage header falls back to the connection's own address, which behind the proxy is shared by everyone like that; nothing stored or
logged). Starting numbers, all in `limits.LIMITS` for dev1 to tune: sign-in
(`/auth/discord/start`, `/callback`) and install (`/install`, `/install/callback`) **20 per 10
minutes** each group; billing (`/billing/checkout`, `/billing/portal`) **10 per 10 minutes**
together; `/account/delete/request` **5 per hour**. The payment webhook has **no** per-address
limit (the provider sends from a few addresses and retries): its signature is the gate and its body
is capped at 64 KB. The refusal sets the same no-store headers as any answer, and a cross-site POST is refused before it is counted. The API's port must be reachable only through the proxy (otherwise anyone could invent the header). Over the limit: a browser visit goes back to the account page with
`?signin=slow` or `?install=slow` ("Too many tries. Wait a few minutes and try again.") and a
`Retry-After` header; calls from the page get 429 `slow_down` with `Retry-After`. Each limiter holds
at most 20,000 addresses; past that new addresses get 503 `busy`. `/feedback` uses the same limiter
with a count of one per 10 minutes.

**The hours meter (#437 part 2), decided 2026-10-09 with Supervisor, built in slices.**
*2a, recording:* listening minutes are written once a minute while a session runs and once
more, rounded up, when it stops (a restart carries on from what is stored and counts none
twice). Two tables, so no query crosses servers: `session_usage` (per campaign and
session, server-isolated, deleted with the campaign; one row per owner the session had)
and `owner_hours` (`owner_user_id`, `month_start`, `minutes`: numbers only, scoped to the
owner like `entitlements`, readable by that owner across servers, written only through
`Database.meter()`, which sets the server and the owner for that one write). Minutes count
toward whoever owns the campaign when each is recorded, so a hand-over mid-session moves
the later minutes; a campaign with no owner records nothing. `owner_hours` is not a
campaign's data and never goes into a backup. Only time DMbot is actually listening counts: a restart gap is never billed (a resumed session adds to the minutes already stored, from the moment it is picked up again), and minutes while a campaign has no owner are kept in its session record under "nobody" and billed to no one, so whoever takes it on is billed only from then. The month: a paid plan's billing period;
Try It its 30 days; a grant that is Guild-level, calendar months from the day it started
(a 29th to 31st start falls on the month's last day); a grant overlapping a paid plan, the
paid plan's month; the free list and "no limits" grants have no limit but are
recorded (never limited), in UTC calendar months. *2b, the checks, built in two steps:* (i) `/dmbot start` refuses when the plan has
ended or the hours are used up (a campaign with no owner is asked to be taken on first), and
the DM screen warns when the hours pass 80% and 90% (said once each, "About 4 hours left
this month"); a database hiccup lets the start go ahead rather than lock a table out;
(ii) the cap finish: when the owner's hours reach the cap a running session may carry on
for up to 2 more hours, once a month, given to the first session to reach it (recorded as
`owner_hours.grace_session`, so a restart neither repeats nor removes it). The DM screen says
so once, with the time it runs to (a Discord time, shown in each reader's own time zone);
about 15 minutes before the stop it says so again, once; when the grace is spent, or for any
other session that reaches the cap later that month, DMbot stops listening, forgets the saved
session and says why. These notices name only "the campaign's owner" and the site, never a
plan or payment, since co-DMs read the screen. The grace is the owner's: it is spent from
their total across all their campaigns, and a campaign handed over mid-grace gives the new
owner their own grace on their own hours. A stop that comes in between the minutes being
written and the grace being given never spends the grace. All of it only when
`DMBOT_ENFORCE_PLANS` is on. *2c, the campaign cap (decided with Supervisor, 2026-10-09):* the count is
read from `owner_campaigns` (`owner_user_id` and `campaign_id` only: no server, no name;
scoped to the owner like `owner_hours`; no grant on the table for the website's role), because a person's campaigns span servers and `campaigns` is isolated per
server. A `SECURITY DEFINER` trigger on `campaigns` (fixed `search_path`, nothing but one
insert, delete or move) keeps it in step through create, restore, hand-over, delete and a
server's data being removed; the migration backfills it, opening the tables it touches for
itself. It is owner-level data, so it is not in a backup: a restore rebuilds it through the
trigger (which refuses `TRUNCATE campaigns`, since a row trigger would not see it: bulk
removal uses `DELETE`). The only way to read the count is `dmbot_owned_campaigns()`, a
`SECURITY DEFINER` function (fixed `search_path`, no argument, EXECUTE for the bot's role and
the website's only) that returns the number for the person set in the transaction and nothing
else; the bot sets that person for one read only, through `dmbot.campaign_cap`, and the person
switch itself is private to `dmbot.entitlements`. One function (`dmbot.campaign_cap`) counts
for every check: `/dmbot start` refuses an owner who owns more campaigns than the plan covers
("Your plan covers 2 campaigns, and you have 3. To start this one, pause another one or change
your plan", the cap from `plans.json`; the change-plan offer only for a plan that can change, and
anyone but the owner only hears "ask the owner"); **making a new campaign past the cap is
refused too** (same words, "To make a new one", so a third campaign never locks the first two
out; someone with no plan yet may still make one, to start once they pick a plan); a hand-over,
a take-over and a restore each need room for one more, **on the website as well** (the web
API reads `DMBOT_ENFORCE_PLANS` like the bot, and its accept counts through the same
function). Every unpaused owned
campaign counts (pausing is built, part 4 below). All of 2b acts only when
`DMBOT_ENFORCE_PLANS` is on (default off; the meter records either way), which dev1 turns
on with the website's go-live (#498) and notes in the testing log. The refusal for a plan
that has ended says "Your plan has ended. Pick one here: <WEB_SITE_URL>/account" (or "Pick
one on DMbot's website" while it isn't set); one for used-up hours says so and offers what
the owner's plan allows: a paid plan "add N hours or change your plan" (N is `extraHours` in
`plans.json`, never typed in code) and says the hours "start again when your plan
renews"; Try It "change your plan" only; a grant or the free list nothing to buy. It never
names a date (Try It ends rather than renews, a renewal can be days late, and the day depends
on the owner's time zone). Refusals are shown only to the person starting, never in public.
Anyone but the owner is told only to ask the owner, so they never learn about the owner's plan
or hours. A campaign with no owner can't start: a DM of it is given a **Take it on** button
right under the refusal (the DM screen's card may not exist yet), anyone else is told one of
its DMs must take it on. The DM-screen warnings (decided with Supervisor, 2026-10-09) stay on
the DM screen because co-DMs need to know the table may stop; they name only the hours left,
and the 90% one says "The campaign's owner can add more at <WEB_SITE_URL>/account", so it fits
everyone who reads the screen and never says whose plan it is.

*Built, part 3: AI and copies by plan (2026-10-09, #919):* `dmbot.plan_rules` holds the two
rules, pure over the owner's `Access`, and the refusal words, next to `hours.py`'s so the bot
and the site never disagree. **`can_use_ai`:** true for a working plan of any kind (paid, Try It
within its period, a grant, the free list), false for an ended plan or a campaign with no owner.
It guards the one place a person's button press spends AI tokens (🤖 Find names; the AI that
labels lines inside a session is covered by the start check); the story-memory and
rules-advisor AI calls will call it when they exist. **`can_backup`:** true for a paid plan other
than Try It, a grant or the free list (`backups` in `plans.json`), false for Try It, an ended plan
and no owner. It guards `/dmbot backup`, every transcript download (`/transcript`, the
end-of-session button, "as heard too") and **restore**. Restore is judged on whose campaign it
makes: a copy loaded as a *new* campaign makes the restorer its owner, so it is the *restorer's
own* plan that has to include copies (and the free slot is the store's check, `restore_needs_slot`);
a copy loaded *over* a campaign keeps that campaign's owner (#609), so it is the *owner's* plan
that counts, and a co-DM with no plan may restore their paid owner's campaign. The check is made
when the choice is made, since that is when it is known, after "Restoring…" has answered Discord.
Both rules are read through the meter door (the owner's plan, scoped to the owner), so the whole
table stops or goes together. A refusal is private to the person who pressed, and names what they
pressed. The owner (or the restorer) hears, for an ended plan, "Your plan has ended, so DMbot
can't <make copies | send transcripts | find names with its AI | load copies>. Pick one here:
<WEB_SITE_URL>/account" (the same first words as the hours refusal; the data cannot tell an ended
plan from one never had); for Try It, "Try It campaigns can't make copies or transcripts. A paid
plan can. See plans here: <WEB_SITE_URL>/account", or when loading a copy "Nothing was loaded.
Loading a copy needs a paid plan. Pick one here: ...". Anyone else, a co-DM included, is told
only "<Copies of this campaign aren't available | Transcripts aren't available for this
campaign | Finding names with DMbot's AI isn't available for this campaign | Loading a copy isn't
available>. Ask the campaign's owner to take a look.", never anything about the plan. A campaign
with no owner is told "This campaign has no owner yet. One of its DMs needs to press **Take it
on** on the campaign's card in the DM screen first." When the plan has no downloads (#938), the end-of-session private message is not sent to
players at all (they could not act on it, and a download button would be refused on every
press): only the owner is messaged, once, "The session for X has ended. Its transcript
is kept, but this campaign's plan doesn't include downloads." plus the owner's line above; with no owner, the campaign's DMs get the
no-owner line instead. If the plan check fails the buttons go out as usual. A refused Find names answers Discord first,
leaves the menu in place (so "Add the lines that fit" still works), does not count against the
day's reads, and records no right-to-use confirmation, because nothing was read. Only when
`DMBOT_ENFORCE_PLANS` is on; a database hiccup, or one slower than 2 seconds, lets the action through, like the start check. A campaign with no owner has no plan, so it
has no copies or AI until a DM takes it on (the issue's rule; a player is told a DM must).

*Built, part 4: pause and unpause (2026-10-09, #957), the last go-live blocker for plan
rules:* `campaigns.paused` (migration 0037) with `owner_campaigns.paused` kept in step by the
same trigger; the count function counts only unpaused campaigns. A paused campaign keeps all
its data, stays downloadable while the plan allows downloads, **can't start** (even with
plans not enforced: "This campaign is paused, so it can't start. Open ⚙️ Settings and press
▶️ **Unpause** to use it again. Everything in it is kept."; anyone but the owner is told to
ask the owner) and takes no place on the owner's plan. The owner, and only the owner, gets
⏸️ **Pause this campaign** / ▶️ **Unpause** on the ⚙️ Settings card (co-DMs see the state line
and are told only the owner can change it); the store checks the owner again, and a campaign
DMbot is listening to can't be paused until it is stopped. An unpause that would go over the
cap is refused in the cap words ("... To unpause this one, pause another one (⚙️ Settings,
then ⏸️ **Pause this campaign**) or change your plan ..."; every refusal that offers "pause
one" now names that button). **A plan that shrinks or ends pauses what is over its cap:**
`dmbot_pause_over_cap(keep)` pauses the owner's campaigns beyond the `keep` most recently
played (ties by newest created), in every server, for the person set only, under the
per-owner lock, and returns what it paused. The cap kept to is the plan's, or Try It's one
campaign once the plan has ended. **Where it runs (chosen):** in the bot when it next reads
the plan (a start, a new campaign, an unpause), not in the website's webhook path: the bot
is the one that can message the owner, a change that lands while the bot is down is still
settled on the next read, and the webhook stays free of bot logic. The owner gets one private
message naming the paused campaigns and how to change it (pause others, or change the
plan); because the function pauses each campaign once, there is no second message. If the
private message can't be sent, the start refusal still says the campaign is paused. Fails
open like the other plan checks. **Enforcement (`DMBOT_ENFORCE_PLANS`) may go on only after
this is live** (migration 0037, core and web-api).

*Built, game masters (2026-10-16, #1600, item 2 of #618; decided by Supervisor):* a campaign's DMs add and
remove each other from ⚙️ Settings → **👑 Game masters** (DMs only, checked on every press; a server manager
who is not a DM does not get the button). It lists the game masters and marks the one whose plan the campaign
uses ("plan"). **➕ Add a game master** picks a member of the server: never a bot, never someone who already
is one, at most 5 (the store enforces it under the campaign's lock). The new game master gets one private
message ("<name> made you a game master of <campaign> in <server>. You can now see its DM screen and change
its settings."); if it cannot be sent the adder is told. Adding never touches anyone's consent to be recorded.
**➖ Remove** picks one, then asks once ("<name> will no longer see the DM screen or change this campaign.
Remove?"): never the plan holder (it says to hand the campaign over first: ⚙️ Settings → Hand over), never the
last game master; a game master may remove themselves. **Access follows at once:** after either change the DM
screen's permissions (and an existing game-time channel's) are applied again and a running session's DM set is
updated, so a removed game master loses the screen mid-session and a new one gets it; if the screen cannot be
updated just then the change stays and the DM is told. Every change is logged with who made it (ids in the log
only, never in a channel).

*Built, part 5: retention (2026-10-10, #964):* `dmbot.retention`, a daily job at 03:00 UTC (the
bot's own loop; safe to run twice). A campaign's **delete date** is its last session plus its
owner's plan's `keepAfterLastSession` (Try It 60 days, Table 6 months, the others a year), or
the plan's lapse plus 120 days, whichever is first; free access and grants keep like the top
plan; a campaign with **no owner** is kept by Try It's rule (DMbot doesn't know a last owner's
plan), and its DMs get the warnings. A paused campaign follows the same rules, and a campaign
in a running session is skipped. The owner gets a private message 14 days and 3 days before
(`deletionWarningDaysBefore`), each once: the stage and the date it was sent for are kept on the
campaign (`retention_warned_stage`, `retention_warned_for`, migration 0039), so a restart doesn't
repeat one and playing a session (which moves the date) starts them over. Deletion is the
existing `CampaignStore.delete`, then the owner is told once; the log has counts only. **Its Discord channels
(2026-10-16, #1598):** after a successful delete, its **DM screen and game-time channels are deleted**, while they
are still named like DMbot's (a renamed, reused one is skipped; they hold only DMbot's own messages and the DM-only notes), and its **transcript and `dmb-chat` channels are kept**
with one closing note, DMbot's own buttons taken off (they hold what people wrote or said, which is not
DMbot's to delete; a server admin can). Only the channels recorded on that campaign, found in its own server;
a missing one, one DMbot may not manage or a slow one is skipped, counted in the log line
("channels: 2 deleted, 2 closed, 0 skipped") and never stops the job. The job
runs per server (the bot's own servers), reading across servers only the owner's plan, through
`usage.retention_standing`'s owner-scoped door; a plan that can't be read means that campaign is
left alone. **A campaign is only deleted after its last warning:** one already past its date when
first seen (the first run after deploy, a long outage) gets a warning and is deleted three days
later, and playing a session, or restoring a backup (which starts the clock from now), cancels
it; the job looks again right before each delete. **Safety:** behind `DMBOT_ENFORCE_PLANS` (off: no warnings, no deletion, one dry-run
log line with counts); a run that would delete more than 10% of all campaigns (and more than
3, so a small deployment isn't stuck on one old campaign) stops and logs an error instead, before any warning goes out; at most 300 warnings go out per run (the rest wait a day).

*Retention follow-ups (2026-10-11, #989):* (1) **Servers the bot has left:** the job takes its
server list from the database (`campaign_guilds`, migration 0045, server IDs only, filled by a
trigger on `campaigns` and backfilled; a server with no campaigns left is dropped from it), not
from the bot's current servers, so a left server's campaigns are kept and deleted by the same
rules. The warn-before-delete rule is unchanged: if no one can be reached by private message,
nothing is deleted. (2) **One message per person:** a person with several campaigns past a
warning gets one private message listing them (six to a message at most, to stay under Discord's
limit); each campaign still has its own remembered warning. A single campaign reads as before.
(3) **A missed run is caught up:** the finish time of each run is kept in `retention_last_run`
in the data directory; at start-up, if the last run was over a day ago, the job runs once two
minutes after the bot is connected. A bot with no recorded run waits for the next 03:00.

Rules: checks at `/dmbot start` (plan active or in the 7-day payment grace, hours left,
campaign active, under the campaign cap) and at anything that spends tokens (AI Find
names, later story memory and rules lookups), plus backup, restore and transcript
download (paid campaigns only; restoring needs a subscriber with a free slot, who becomes
the owner). **Campaign cap (decided 2026-10-09, #927):** with `DMBOT_ENFORCE_PLANS` on, making
a campaign beyond the plan's cap is refused (same plain words and offers as the start
refusal, "Your plan covers N campaigns, and you have M"), so one extra campaign can never
lock the owner out of the ones that fit; the count is one owner-scoped table
(`owner_campaigns`) read through one narrow function, by the bot and the website alike.
Two different waits: the 7-day payment grace is a plan rule (a failed payment
leaves 7 days to fix it), **and only for an account that has paid successfully before**
(owner, 2026-10-09, #498, #922): a failed first payment gets no grace, the plan simply
doesn't start; separately, a paid plan keeps working up to 3 days past its
period end while the payment company's renewal arrives (a technical guard against a late
webhook, not a plan rule; Try It ends exactly at its 30 days). Warnings on the DM screen
at 80% and 90% of the hours (decided 2026-10-09, #897: they name only the hours left,
never the plan or anything about payment, because co-DMs, and players who may peek, read
the DM screen; the 90% one says the campaign's owner can add more); at the cap DMbot
finishes the session (up to 2 hours of grace, once a month), then refuses to start until
renewal or a top-up. On a downgrade or lapse the first N campaigns started afterwards
are active (N = new cap), the rest are paused with their data kept. Retention: 60 days
after the last session on Try It, 6 months on Table, 1 year on the other plans, and 120
days after a plan stops paying; the DM is warned at 14 and 3 days; deletion on request is
immediate. **Deleting an account (decided 2026-10-08, #435, #552; changed by the owner
2026-10-11, #922):** the account goes at once, **but the campaigns it owns stay**, because
the players may still want to read or download them. They are treated like a campaign
whose plan stopped paying (kept 120 days, then deleted, with the usual warnings), and any
DM of the campaign, or anyone who restores a backup, can take it over as its new owner
(#437, #1059); the paid subscription is
cancelled at the end of the period already paid for, so the person is never charged
again, and the rest of that period is not paid back unless they ask before deleting (the
refunds page says so, and so does the delete screen); after deletion DMbot keeps only
the Discord account number, if that account used Try It (one trial per person), and the
reference numbers of its payments with the account number (so no payment is counted
twice). **Campaign ownership and hand-over (decided 2026-10-08, #437):** every campaign
has one owner (`campaigns.owner_user_id`): the DM who created it, or whoever restored it
from a backup as a new campaign (replacing an existing campaign from a backup keeps that
campaign's owner, so a restore can never be used as a hand-over; #609). Campaigns from
before this get their only DM as owner; one with several DMs
has no owner until the first `/dmbot start` asks the DM who started it to take it on
("Take it on / Not now"; never guessed, since ownership spends someone's hours; once the
plan checks are live, no owner means no start). A hand-over is an offer, never immediate:
the new owner (any member of that Discord server; their plan is checked only when they
accept, and never shown to the owner) gets a private
message with Accept / No thanks, also shown on their account page; ownership moves only
on acceptance, and only if they still have a free campaign slot at that moment; the offer
expires after 7 days and the old owner can withdraw it. On accepting they become a DM of
the campaign; the old owner stays a co-DM. Only the owner can offer; removing the owner as
a DM is refused ("Hand the campaign over first"); a campaign whose owner has vanished is
saved by a restore. *Details (decided 2026-10-08, #644, #614):* offers live in one table
(`campaign_handover_offers`: server, campaign, from, to, both display names as they were
when offered, created, status open / accepted / declined / withdrawn / expired) and every
write goes through `CampaignStore` (`offer_handover`, `accept_handover`,
`decline_handover`, `withdraw_handover`, `take_ownership`), used by the bot and the
website alike so the rules can't drift; the website may offer only to the campaign's
other DMs (never a cross-person read of who in a server pays); **an offer is never
refused for "no plan" (decided 2026-10-08, #437 point 2): the owner must not learn
whether a member pays, so the plan and free-slot check happens only at Accept and is
shown only to the person offered ("you need a DMbot plan with a free campaign slot;
pick one at the site, then press Accept again; the offer stays open for 7 days"), and
the owner sees only that the offer was sent and nothing changes until they accept;**
the campaign's #dm-screen notes an *accepted* offer (whose plan it uses now changed) and
never a declined one, which under peek or open visibility would tell players who refused
what; a campaign with no owner shows its DMs "Take it on", not a Hand over button that
can only refuse (decided 2026-10-08 on #765);
the bot checks membership when it delivers the private message and withdraws an offer it
can't deliver, telling the owner; until the campaign count exists (part 3) "a free slot"
means "a plan that works". *Offers made on the website (#690):* `offer_handover(...,
delivered=False)` saves the offer with no `delivered_at` and, in the same transaction,
sends `NOTIFY dmbot_handover_offers, '<server id>:<offer id>'` (IDs only: notifications
skip row-level security). The bot process that serves that server claims the offer for
10 minutes (`claimed_at`, set only if it's unsent, unclaimed or its claim lapsed, open
and unexpired, so it's sent once), sends the same private message as the Discord
button, then sets `delivered_at`. Only "not in the server" or "doesn't take messages"
takes the offer back (and tells the owner); any other Discord error lets the claim go
for a later try, and a claim left by a process that stopped lapses. It sweeps for unsent
offers each time it starts listening, when a server becomes available or is joined, and
hourly. Offers made in Discord are saved as delivered. The website's role, when it
offers, must only be able to insert unsent, unclaimed offers from the signed-in owner. *Built (2026-10-11, #728):* the rule is in the database now, ahead of the route: a restrictive policy `web_new_offer` on the website's role requires an inserted offer to be the signed-in person's own, open, undecided and with no `delivered_at`, `claimed_at`, `message_id` or `end_told_at`; and `schema.OFFER_INSERT_COLUMNS` lists the only columns a future `INSERT` grant may name. The role has no INSERT on offers today; a test grants it to prove both the column limit and the rule hold, and that a mistaken whole-table grant is still held by the rule.
*When an offer's 7 days are up (#690, part 2):* the same hourly sweep marks it expired
(also catching ones already marked expired quietly) and announces it once
(`end_told_at`): the owner hears "they didn't answer within 7 days … the campaign stays
yours", and the person's private message (`message_id`, kept by both paths) loses its
buttons. Best effort, at most an hour late. *Answers on the website (#737):* accepting,
declining or taking back an offer on the account page sends `NOTIFY dmbot_handover_decided`
(ids only) in the same transaction (`announce=True`; the bot's own buttons tell people
themselves). The bot process serving that server sends the same private messages the
Discord buttons send: the owner hears of an accept or a no thanks, and the person's offer
message says what happened and loses its buttons; an accept also notes #dm-screen, as the
Discord button does. An offer never sent to the person tells them nothing; a take-back
whose message can't be changed is told in a new message; one answered while its message
was still going out has that message changed as soon as it's sent. The bot waits until
Discord has listed its servers before acting on one, so an answer made while it starts
isn't lost. One answered while no bot listened isn't told (the account page shows it),
and a rolling deploy may tell the owner (and note #dm-screen) twice (no told-at mark,
#797). A Try It plan may receive a hand-over if its one slot is free;
the campaign then follows that plan (so, while on Try It, no backups or downloads). The
website's database role gets only the narrow extra rights the account page needs, under
restrictive policies (read and answer offers where the signed-in person is sender or
recipient; move ownership and add the DM only for a campaign with an open offer to that
person), never a wider grant; if that can't be written cleanly the API asks the bot over
the internal link instead. Campaigns are counted across servers through a `campaign_owners`
table (campaign, server, owner, active or paused) readable by the owner's user id like
`entitlements`, never through a function that sees every server: row-level security stays
the one rule. Cost basis for these prices: about $0.30 per table-hour (Deepgram Nova-3 clip
pricing on roughly 36–60 speech-minutes per hour, Claude Haiku for the AI features,
hosting); to be measured with the twin and run 8 before the promotion to `main`.
Discord servers cost nothing and are not counted.

*Built, account deletion keeps campaigns (2026-10-11, #1059):* deleting an account calls the
database function `dmbot_orphan_owned_campaigns(at)` (migration 0047), the one door across servers
like the pause function: it runs with its owner's rights, opens `campaigns` to itself for the
signed-in person through a setting it sets and puts back, and only turns that person's campaigns
ownerless (`owner_user_id` NULL, `owner_left_at` = the deletion time); the website's role may call
it and nothing else about campaigns. The cap table follows by its trigger, so a deleted account
holds no slots. **Retention:** an ownerless campaign is deleted exactly 120 days after
`owner_left_at` (`retention.delete_date`), whatever its last plan was and however recently it was
played (a session doesn't restart it); the usual 14- and 3-day warnings go as private messages to
the campaign's DMs (there is no owner), naming the **Take it on** button. **Taking it over** is the
existing ⚙️ Settings **Take it on** button (needs a free campaign slot), which clears
`owner_left_at`; replacing the campaign with a backup does the same for whoever restores it. Stays
dormant with `DMBOT_ENFORCE_PLANS` off, like all retention. If the person who left was the campaign's only DM, nobody can take it on and it simply expires after the 120 days (players can still read and download it until then). The delete screen and the legal pages
say it in plain words. *Not done:* the warnings are private messages to the DMs, not posts on the
DM screen as #1059 asked: the retention job has no Discord channel to post to, and a private
message reaches the same people and keeps DM-screen content out of any shared place.

**D&D Beyond character sheets (plan, 2026-10-08; phase 7 part A can start now).**
Each player links their own sheet, privately: the consent message gains a "Link my
character" button that opens a form for the link, and the DM's "Add a player's
character" form gets the same optional field. Only `dndbeyond.com/characters/<number>`
links are accepted and the sheet must be set to Public on D&D Beyond (if it isn't, DMbot
tells the player how, in three plain steps). The link hangs off the player character
DMbot already stores (the entity with "played by"); a player can unlink it with a
button; the campaign's DMs see which characters have a sheet. DMbot keeps a small
snapshot per character per campaign: name, species, classes and levels, ability scores,
hit points, armour class, speed, saves, skills, senses, languages, and the *names* of
spells, features and items; never descriptions or rules text (IP rule), enforced by an
explicit list of allowed fields in the parser. The snapshot refreshes once at `/dmbot
start` and on a DM's "Refresh sheets" button (one request per character per session);
it is deleted with the campaign, goes into backups, and is never shared between
campaigns even when two campaigns link the same sheet. Uses, in order of value: spell,
item and feature names into the speech-to-text hints; the rules advisor (phase 3)
citing the sheet in alerts (DM screen only); a who's-who card per character on the DM
screen and facts for the story memory. Risk, stated: D&D Beyond has no official API;
public sheets are read through an unofficial address that may change or close, so DMbot
never scrapes pages, reads only sheets players made public, fetches rarely, and keeps a
manual fallback ("Tell DMbot about your character": class, level, key features) so
nothing else breaks if the feed does. Campaign-level access needs the DM's own login
and stays a later browser extension in the DM's own session; never a password or cookie
on our side. Order: part A (link, snapshot, hints, unlink, fallback form; one developer,
no table work), part B with phase 3 (the rules advisor reads the snapshot), part C later
(the extension).
*The D&D Beyond campaign invite (owner, 2026-10-13):* DMbot can't create a D&D Beyond campaign or
add people to one (there is no official API), so it passes on the DM's own invite.
- **The DM** pastes the campaign's invite link from D&D Beyond (Campaigns, then the campaign, then
  the invite link) when creating the DMbot campaign, or later in ⚙️ Settings. Only
  `dndbeyond.com/campaigns/join/<code>` links are accepted.
- **Players** see a **Join on D&D Beyond** link button on the consent message and on each
  session's reminder, and in the transcript channel's pinned card. Nothing more is asked of them
  there.
- **Where it shows:** the link stays inside that Discord server and goes into the campaign's
  backup. The DM can change or remove it.
- **A character from the sheet.** When a player links a sheet and the DM hasn't added their
  character yet, DMbot adds it from the sheet's name, as that player's character, and tells the
  DM on the DM screen. The DM can rename, merge or remove it there.
- **Part C (the browser extension in the DM's own D&D Beyond session) stays later.**
*Built (2026-10-13, #1119):* the DM's invite is `campaigns.dndbeyond_invite` (migration 0051, a CHECK as
well as `dmbot.campaigns.invite.parse_invite`: only `https://[www.]dndbeyond.com/campaigns/join/<code>`,
stored in the `www` form; an empty box removes it), set in an optional field on the new-campaign form or
by ⚙️ Settings, **🔗 D&D Beyond invite** (campaign DMs only), and in the campaign's backup. The
**Join on D&D Beyond** link button is on the consent request, on each session's reminder and on the
transcript channel's pinned card (posted again when the link changes), only in that server. A player with
no character who presses 📜 My character sheet on a session's reminder gets **Link my D&D Beyond sheet**:
a sheet DMbot can read makes the character from the sheet's name (played by that player, once, even for two
quick links), and the DM screen gets "Mira's player linked a D&D Beyond sheet; DMbot added Mira." with
**Open character card** (rename, merge, remove). A sheet it can't read makes nothing and shows the Public steps. The invite is in the backup file, but a *new* campaign
made from a backup gets none (it is the original DM's join link for the original table), and replacing
a campaign with a backup keeps the campaign's own link and ignores the backup's (a backup doesn't record
its server, so its link could belong to another server's table). Only the `/campaigns/join/<code>` shape
is accepted; if a real invite has another shape, adding it is a one-line change. The Join button stays on a
consent or reminder message when the player answers it, using the campaign's *current* link while a
session runs. When the DM changes or removes the link, the running session and the pinned card follow at
once. The new-campaign form's link is saved in the same step as the campaign.

**Character sheets, part D: any sheet, an import status, live checks; combat and FightBot (owner, 2026-10-13).**

- **Any sheet a player has.** There are three ways in, all reaching the same allow-listed snapshot:
  - a D&D Beyond link, as today;
  - a public document link (Google Docs or Drive, or a PDF address);
  - an uploaded file (Word, PDF or text, at most 5 MB).

  For a document or a file, the AI (CAREFUL tier, once per import) reads the character into the
  same fields. The file itself is never kept: only the snapshot is. It is the player's own sheet,
  so there is no IP question, but only the allowed fields are kept, never rules text.
- **The fields.**
  - **Basic:** name, species, class(es) and level(s), AC, and HP (current / max + temporary).
    "Species" is the 2024 word, so DMbot says it instead of "race".
  - **Full:** also ability scores, active conditions, defenses and immunities, skills
    (proficient / expertise), proficiencies, features, feats, background, speed, senses,
    languages, tools, weapon and armor proficiencies, and inventory (count, weight, rarity
    counts).
- **The import status.** After every import, and after a re-read that changed something, the
  player gets one private card, one line per part:
  - ✅ read and checked;
  - ⚠️ read, but not in DMbot's rules list (content outside the SRD, a homebrew feat: it may be
    fine, and DMbot says so plainly);
  - ⛔ couldn't be read or doesn't add up (a level total over 20, HP over max + temp, an
    unrecognized name with no close match).

  For example "⛔ Feats: 'clowning' not recognized; 2 others checked". Checks use DMbot's rules
  index and simple arithmetic, never a guess.
- **The player's sheet view.** **📜 My character sheet**, then **Show my sheet**, shows the
  sheet privately, one button per section, which expands it.
  - A typed or document sheet has **Edit** on each section.
  - A D&D Beyond sheet has no Edit: a **Open on D&D Beyond** link button instead, since D&D
    Beyond is the record.
- **Re-reading D&D Beyond.**
  - **Change heard:** about **1 minute** after something said or typed changes a character
    (damage, healing, a condition, a spell slot), DMbot re-reads that sheet once. Several
    changes within that minute share one read.
  - **Schedule:** while listening and **out of combat**, every sheet is also read every
    **15 minutes**. During combat only the change-heard reads run.
  - **Limits:** at most one read per sheet per minute, and a pause with back-off when D&D Beyond
    refuses. D&D Beyond has no official API, and more reads raise the risk that the unofficial
    address is limited.
  - **A mismatch:** if a sheet doesn't show what was said (the DM said 8 damage, and HP didn't
    move), DMbot asks the player privately and politely, at most once per change: "The DM
    mentioned 8 damage to Belleros. Your D&D Beyond sheet still shows 26 HP. Want to update it?"
    It never changes a D&D Beyond sheet itself, and never tells the DM or the table.
  - **How it runs (built in #1128):**
    - Only sheets linked from D&D Beyond are read again; a typed or document sheet never is.
    - Whose change it is comes from the character's name standing right before the words
      ("Belleros takes 8 damage") or after "to" ("8 damage to Belleros"), or from "I" for the
      speaker's own character. Two names, a monster, or no name: nothing, never a guess.
    - Phrases are strict (no questions, wishes, "if" or "maybe"); damage, healing, a condition
      or a spell slot. A slot count isn't kept, so a slot only causes the read.
    - Backing off after a refusal or an error: 2 minutes, doubling, at most 30 minutes; the
      changes wait. A 5-player table is read about 20 times an hour on the schedule, plus one
      read a minute at most per change.
    - A question is asked once per change heard, only if the sheet shows nothing of it (damage
      that didn't lower HP, healing that didn't raise it unless already full, a condition not
      listed). Hit points and temporary hit points count together. The player's switch is
      **Turn sheet questions off/on** on 📜 My character sheet (per character).
    - The status card is sent again only when a part that was ✅ became ⚠️ or ⛔, and not in
      combat (it waits for the fight to end).
    - **Questions must be rare** (decided by Supervisor, 2026-10-13, #1171), because the game
      has to stay quick: at most **one message per player every 15 minutes**, with all their
      waiting questions merged into it (at most three) and the campaign named in it; **none in
      combat**: they are held and sent after the fight; **never twice** about the same value
      while the sheet still shows the same number; and one the sheet already answers (the
      player updated it meanwhile) is dropped. Damage and healing of the same size within the
      minute cancel out, and "Mira heals 5" is not used (Mira may be the healer): only the
      one who is healed as the subject counts ("Mira regains 5 hit points").
    - **A person who stops being recorded** takes their words with them: nothing waiting that
      they said is read for, asked about, or used.
    - **One limit for the whole bot** on D&D Beyond: a bucket of **20 reads a minute** across
      all servers, and a pause for every sheet after a 429 or a 5xx for as long as
      `Retry-After` says (a minute if it says nothing; never more than 15). A read held by the
      limit waits and nobody is penalised; a 429 is logged apart from other failures.
- **Combat state.**
  - DMbot knows whether the table is **in combat** and which **round** it is.
  - From what the DM says ("roll initiative", "round three", "the fight's over") and from DM-screen
    buttons (**Start combat**, **Next round**, **End combat**) that correct it.
  - It tracks rounds only, never turn order. (The old campaign field `side_initiative` of #1127
    had its Settings button removed, #1769; migration 0130 folds it into the kept initiative style
    as "side", #1791, and a backup with the old switch on restores the same way. The column stays,
    unread, so an older DMbot can still read a new backup.)
  - Combat state goes on the DM screen. Players see "⚔️ Combat, round 3" in the game-time channel.
  - **How it runs (built in #1127):**
    - **Kept with the game clock** (`combat_round`, plus the seconds already spent toward the
      next minute), so it survives a restart. A backup does not carry it: a restored campaign
      starts out of combat. The old side-initiative field stays in the table and in backups, unused (#1769).
    - **Heard from a DM only** (voice or typed), whole lines only: "roll initiative" or "combat
      starts" begins round 1; "round two" or "next round" sets or moves the round; "combat's
      over" or "the fight ends" ends it. Questions, wishes, conditions and stories ("the goblin
      dies next round") never count, and a player's words never change it. Each change posts one
      DM-screen line with Undo.
    - **Time:** every round is 6 seconds. The clock counts whole minutes, so it moves every
      tenth round, and **End combat** counts the last round and rounds up to the next minute
      (at least one). Going back a round is a correction and spends nothing.
    - **Buttons** on the pinned clock: **Start combat** out of a fight, **Next round** and
      **End combat** in one. Only the campaign's DMs.
    - **One question for other parts:** `ClockStore.in_combat(guild, campaign)`.
    - **A forgotten fight ends** (decided 2026-10-13, #1173): a **long rest** or **Skip to dawn**
      ends combat as if **End combat** was pressed (the last round counts, then the rest); a
      short rest and other clock moves leave it alone. At session start, a fight left running
      from last time gets one DM-screen line with **End combat**; it is never ended without the
      DM, because a session can stop in the middle of a fight.
- **FightBot** (one per player character, only while in combat). *Built (#1174, 2026-10-14).*
  - **What it says:** private messages to that player only, reminding them of their own character:
    spell slots left, features and items that could help now ("You still have Hunter's Mark and
    2 level-1 slots").
  - **What it uses:** that player's sheet, and what the players can already know (the transcript).
    Never the DM screen, monster stats or secret names.
  - **What it never does:** rule on anything, or post publicly.
  - **How often:** at most one message per **2 minutes** per player, and only when it has
    something new.
  - **Silencing:** **Snooze 10 min**, **Quiet until next session**, **Never** (kept per player per
    campaign; can be turned back on from 📜 My character sheet).
  - **Cost:** FAST tier, one call per player at most every 2 minutes, only while in combat. It
    counts toward the hours meter's AI cost like every other job.
  - **How it runs (built in #1129, changed after Supervisor's review):**
    - A loop per running session looks every 30 seconds; it does nothing outside combat
      (#1127). It reminds only **people in the table's voice channel right now** (not bots;
      consent and a sheet carry over from earlier sessions, but an absent player is not
      reminded), who agreed to be recorded and have a sheet (any kind), at most **two** AI
      calls per pass.
    - Per player, at most one AI call (and so one message) every **2 minutes**, and only if
      something changed since the last (the round, hit points, a condition).
    - The prompt holds only: that player's own allow-listed sheet facts, the round, and the
      last few things *said aloud* in the last three minutes by people who still agree (the
      cleaned lines, never typed chat), without any line that mentions a secret name. If the
      secret names can't be loaded, nothing is sent. It is told to use only names from the
      sheet, to treat the table talk as untrusted, and to answer NOTHING when nothing clearly
      helps. On our side the reply must name a whole name from the player's own sheet, has
      links taken out, and is dropped if it gives advice or a ruling ("should", "try",
      "use"...).
    - Every message is a private message to that player with **Snooze 10 min**, **Quiet until
      next session** and **Never**. Everything a player chooses is kept in the database, one
      row per player per campaign (`fightbot_players`): Never, the session Quiet applies to
      (the session's start), the end of a Snooze, and the time of the last call. So a restart
      forgets none of it, and **Forget my sheet** or a second character never turns reminders
      back on. Never is undone with **Turn FightBot on** on 📜 My character sheet.
    - **One private-message cap in a fight (rule, Supervisor 2026-10-13):** a player gets **at
      most one DMbot private message per two minutes during combat**, counting everything DMbot
      sends them. In practice that is FightBot's own gap, because the sheet questions and cards
      of #1128 are held until the fight ends. Later features keep to this rule.
    - **Spell slots** (built, #1176): the sheet keeps slot counts, and FightBot is given only
      the levels with slots left ("2 level 1 slots"), so a reminder can say how many remain.
  - **Initiative style, heard** (owner, 2026-10-17, #1771): FightBot listens to the DM and the
    players and works out how the table runs initiative: **one at a time** (each creature on its
    own turn), **by side** (each side acts together) or **rolled each round**, and whether
    everyone **declares** what they'll do first. It changes only *when* FightBot speaks: just
    before the player has to choose (the player before them is acting; their side starts; the DM
    calls for declarations). Never the combat state, the round, or anything a player sees.
    - Heard with a strict phrase list, no AI. Only from people who agreed to be recorded. A
      DM's line sets it; players' lines count only while the DM hasn't said otherwise, and only
      when two players agree. A player's words still never change combat state.
    - **Kept per campaign (#1784, migration 0129):** the last style a **DM's words** set (the
      order, and whether actions are declared first) is kept on the campaign (`initiative_style`:
      `each`, `side`, `reroll`, `declare` or a pair like `side+declare`), written only when it
      changes. A style two players agreed on counts for that session only and is not kept; nor is
      the "no declarations" FightBot works out for itself. The next session **starts from the kept
      style** instead of "not heard yet", so the first fight is timed too. The same rules then
      apply: a DM's line overrides it, players still need two who agree and only while no DM line
      this session has said otherwise. It is in a campaign's backup (an old backup restores as
      nothing kept). No button, no setting; the DM corrects it by saying it.
    - Until something is heard (or kept), FightBot works as before. The DM screen's combat line
      shows what was heard, or "as heard last time" until something is heard this session. The
      two-minute private-message cap in combat stands; a reminder whose moment has passed is
      dropped.
*Built, part D1: the fields and the import status (2026-10-13, #1124):* the D&D Beyond snapshot (still version 1; a
kept snapshot without the new fields reads them as unknown) now also holds hit points now (`hp`: the maximum minus the
damage taken, never below 0) and temporary hit points, active conditions with the exhaustion level, resistances,
immunities and vulnerabilities, skill expertise, tool, weapon and armor proficiencies, the background, and the
inventory's size (`count` of inventory lines, `weight` in pounds as quantity times weight divided by the bundle size,
and the lines per rarity; a line with no rarity is common). Names and numbers only, through the same allow list, size
cap and backup path. `memory/sheet_checks.py` gives each part ✅, ⚠️ or ⛔ from the rules index (spells), a short name
list in `memory/sheet_names.py` (classes, species and feats: names only, in the SRD or a real book's name DMbot has no
rules for) and arithmetic (levels over 20, hit points over the maximum plus temporary, expertise without proficiency,
rarity counts that don't add up, a number that can't be right (negative or far too big), a part that couldn't be read). A name from D&D Beyond that DMbot
doesn't know is ⚠️ (it is D&D Beyond's own data); a typed or document name is ⛔ only when it is nothing like a known
name. The card is one private message after the player's own link or typing (not after the DM links a sheet, and not
yet after a re-read: `sheet_checks.changed` is ready for D4), and the DM's name card adds only "HP 11 / 26 + 0, AC 18".
*Built, part D2: any sheet a player has (2026-10-13, #1125):* on the 📜 panel, **Use a document link** (any public web page or
PDF link, Google Docs and Drive included; fetched through `dmbot.fetch` with its address checks and size limit) and **Upload
my sheet file** (Word, PDF or text, at most 5 MB, in a form with a file box). The text is read out in the existing
document reader and the file is discarded; its words are never stored, logged or sent anywhere but the AI. A picture
of text (a scanned PDF) is refused with one line. `memory/sheet_import.py` sends at most 30,000 characters to the AI
once per import (feature `sheet`, CAREFUL tier) with a fixed prompt that asks for JSON in exactly the snapshot's fields
and says never to invent or follow instructions in the text; the answer goes through `sheets.clean` and the D1 checks,
so a wrong name or number shows as ⚠️ or ⛔ on the status card, and a number out of range flags its part. The sheet is saved
as a typed sheet (`source: typed`, no link) marked `origin: document`, which is checked like a full sheet; what the
document doesn't say is "not found in your document", not an error. Limits: one read a person every 30 seconds, one at
a time per server, 20 a day per server, and the owner's plan must allow the AI (`plan_gate("ai")`).
The documents read at once are limited to two across the whole bot (`document_reader.READING`, shared with the names screen), and every refusal (a link, a size, a scanned PDF, an empty file) is worded for the sheet panel, ending with **Type in my character**. A document's own rarity counts are not held to the item total, and any value outside a sheet field's range (STR 45, AC 99, max HP 0) shows ⛔.
*Built, part D3: the player's sheet view (2026-10-13, #1126):* **Show my sheet** on the 📜 panel (when there is a sheet)
opens a private view with the basic line on top and one button per section (Abilities, Conditions, Defenses, Skills,
Proficiencies, Features and feats, Background, Speed and senses, Languages, Inventory); pressing one opens it, one at a
time. A typed or document sheet shows **Edit <part>** (for example **Edit Skills**) on the open section: a form with that section's fields (names
and numbers only; abilities are six numbers in order), checked in plain words before anything is saved, run through the
same allow list as an import, and answered with a card that names what changed. A D&D Beyond sheet has no Edit anywhere:
an **Open on D&D Beyond** link button to the player's own sheet instead, because D&D Beyond is the record. Every part opened and every save
looks up again that the person still plays the character and is still in the server (the Edit button itself looks
up nothing, to answer within Discord's 3 seconds), and the save is refused for anyone else, or if the sheet changed since the form
was opened (`memory/sheet_view.py`, `ui/sheet_view.py`).
*Decided 2026-10-08 on #781:* a sheet belongs to the player who linked or typed it, not
just to the character: if the DM gives the character to someone else, the old sheet is
never shown to the new player (it shows only while that player still plays it). The
player is told the truth about the link: DMbot shows it only to them and their DM, and it
is also in the campaign's backup file. The 📜 button on the consent confirmation and the
reminder is explained as optional and "doesn't change recording"; that sentence adds
nothing anyone agrees to, so the consent terms version stays as it is.
*Built, part A first half (#723, dev2):* the `character_sheets` table (one row per player
character per campaign, its link and snapshot; its own table rather than columns on the
entity, so a refresh is never in the undo log or the in-memory names), the allow-list
parser and the one-GET fetch (`dmbot.memory.sheets`), the background refresh at `/dmbot
start` (kept names at once, fresh ones when read; not again after a restart), and
backups (`sheet` rows; a restored snapshot goes through the same allow list). Up to 15
sheet names join the hints right after the characters, taking turns between characters,
never one that is also a secret name. *Second half:* one **📜 My character sheet** button
on the player's consent confirmation and each session's reminder opens a private panel
for their character (picking one if they play several in the server): Link my D&D Beyond
sheet (read at once; "set to Public" if refused), Type in my character (the typed
fallback), Forget my sheet. A sheet records who linked or typed it (migration 0029) and is
shown and used only while that person plays the character, so a character given to
someone else never shows the last player's sheet. The DM's "Add a player's character"
form takes an optional link; a player character's card shows "📜 Sheet: linked to D&D
Beyond, read …" (the address only to the campaign's DMs) with 📜 Forget sheet; the names
panel's 📜 Refresh sheets reads them all again (at most once a minute per campaign). A
merge moves the sheet to the kept character unless it has its own (then the merged one's
is forgotten). The panel and forms act only while that person still plays the character
and is still in the server. The log lists only names read from D&D Beyond, never typed
ones.

**Website (decided 2026-10-07).** `web/` in this repo, Astro + TypeScript, static pages
with one signed-in area; Cloudflare Pages; sign-in with Discord (scopes `identify
email guilds`) or, since 2026-10-14, an emailed sign-in link (see "Accounts" below); payments through a hosted checkout, **Stripe** (owner's
choice, 2026-10-13, replacing Lemon Squeezy, chosen 2026-10-09, #498) with its customer portal
for plan changes; the API is FastAPI in
`core/src/dmbot/web/`, its own container, the only writer of the `entitlements` table via
the provider's webhook. No D&D or Wizards trademarks or art: "for 5e-compatible tabletop
games". Terms, privacy and refund pages before launch: **signed off by the owner on
2026-10-09, which is their effective date** (#498). Also approved for go-live that day:
rate limits at the proxy, the separate `dmbot_web` database role, and Cloudflare in front
of the API. **Legal values (owner, 2026-10-10, #922):** DMbot LLC, email-only contact (no
postal address on the site or in this repo), minimum age 16, OVH US hosting. **Final legal
text signed off by the owner on 2026-10-11 (#922, #1077):** campaigns outlive a deleted
account (120 days unless another DM takes them over), rights requests by email answered
within 30 days, no claim about how the database is encrypted, and the owner's own "Our
responsibility" wording; no `{{CHECK}}` markers remain. That closes #498 item 4. The Discord app
stays private until the rest of #498 is done and the owner says go. Settings stay in Discord for now;
the site is account, plan, campaigns, invite and marketing.
**What's new page** (Supervisor, 2026-10-15, #1321/#1322). The site has a short What's new page
(`/whats-new`, one file per entry in `web/src/content/whats-new/` since #2469, 2026-10-23), newest first, in plain words. An entry
goes there only when its feature is **live: deployed on the server and not known to fail** for an
ordinary user. Untried is not broken; a failed live check takes the entry off. An entry that is
deployed but blocked (for example the account move, whose code goes to the payment email while
payments are off, #1096) is marked `held` in its file, with its reason. A PR that ships a
feature people can see adds its entry, or a held one, in the same PR.

**Session recaps** (Supervisor, 2026-10-15 and 2026-10-16; built in #1392, #1397, #1412, #1415,
#1417, #1420, #1422, #1428, #1430, #1437, #1445, #1450, #1455, #1457). After a session DMbot can
write a short story-style recap for the DM to read at the start of the next one. Recaps are for
paid plans and are **off on the server** (`DMBOT_RECAPS`) until dev1 has checked a real one
(#1382); the site's words about them show only when `RECAPS_LIVE` is on (web, `recap-words.ts`).
- **Written only from consented speech**, and every detail is checked against it; anything the
  DM keeps secret is left out, and the bot checks again at post time (a player who stopped
  being recorded since, a secret name) and refuses with a plain reason. **The bot never posts
  a recap by itself**: the DM reads it first, may change the words (on the website) or try
  again (twice), and presses **Post to the table**, which puts it in the campaign's `dmb-chat`.
  **How strict the beat check is** (Supervisor, 2026-10-19, #1997, after the owner's first real
  Craig transcript failed it twice): the DM is not one of the "players who spoke a lot" who must
  appear in a beat (the DM narrates), and a name in a beat counts as said when it was said
  anywhere in the session, not only in that beat's own lines, because speech-to-text spells
  names several ways and the lines often say "you" or "he". An invented name still fails, and
  the secret, prose and post-time checks and the DM's review are unchanged. If tests show names
  put on the wrong beat, the next step is a near-spelling match against the beat's own lines first.
  **How long a recap is** (owner, 2026-10-19, #2013, after the owner's real 652-line session came
  out at nearly 900 words against a fixed 350 to 550): the length follows the session. The target is
  **100 words for every hour played, plus 100**, rounded to the nearest 10, at most 700 (1 hour 200,
  3 hours 400, 4 hours 500). A draft passes from 50% under the target to 20% over it (owner,
  2026-10-19, after recap test 9 lost a 218-word draft against a 224-word minimum: a short recap is
  better than none); the last try may run up to 35% over, since length is a quality target and not
  a grounding rule. The writer is still asked for the target itself. Names, copying and secrets stay
  strict on every try; a name counts as said when anyone said it in the session's kept lines (as for
  the beat sheet), and an ordinary word that only starts a sentence is not a name. The hours come
  from the session's first and last kept lines. A test file with no times (Craig's plain text)
  estimates them from the words spoken, and `recap_run --hours` sets them.
  **Shorter recaps** (owner, 2026-10-22, after Test L): the target is now **75 words for every hour
  played, plus 75**, rounded to the nearest 10, **at most 450** (1 hour 150, 3 hours 300, 5 hours or
  more 450; under three minutes read aloud). This replaces the 100/100/700 rule above; the 50% under
  and 20% (35% on the last try) over margins stay. Why: a recap is read or played at the start of
  the next game, which must stay quick, and a shorter recap costs less to voice, gives the checks
  less to read and leaves less room for an answer to be cut off.
  **A draft of the wrong length is steered, not just retried** (owner, 2026-10-22, after Test L's
  rerun failed its checks). When a draft fails only because of its length, the next try tells the writer
  which way and by how much, N being the distance to the target: for a 1-hour session (target 150, range
  75 to 180), "Your draft is 60 words; it must be 75 to 180. Make it longer by about 90 words" (or shorter). The next draft must move the right way and land in the range. If it moves the
  wrong way, does not move, or overshoots past the other end of the range, the job stops there and
  fails as our own failure (the DM's Try again is given back), rather than spending more tries. The
  same applies to the section count when the fix-up changes the sections: the length is checked again
  after it, and steered once if it left the range.
  **Every claim is checked against the beats** (Supervisor, 2026-10-19, after the owner read recap
  test 10: it passed every check yet said the party travelled south (never said at the table) and
  had its weapons taken at the tavern, when that happened later at the keep's gate and one weapon
  was hidden). Grounding is the recap's first rule, ahead of length and style:
  - **Each beat carries where it happened** (`where`, a place name from the lines, or "" when the
    lines do not say) and the writer is told never to move an event to another place, never to add
    a direction, distance, time or detail the beats do not state, and never to say "all" or "every"
    of the party unless the beat does.
  - **A fact check follows the draft checks:** one careful-model call reads the beat sheet and the
    draft and lists every sentence that states something the beats do not support (wrong place,
    wrong person, invented event or detail, contradiction), as a fixed-shape answer. A flagged draft
    is revised once with the list; a sentence still flagged after that is cut in code, and the
    recap stands only if it still meets the minimum length. The grader's style scores never
    outrank this check.
  - **Coverage:** the fact check also names the beats the draft leaves out that change the story
    (a new place, a new person met, a decision, a reveal). Those are given to the revision; the
    length band, not coverage, decides when it stops.
  - Knowing the adventure's map (where a town lies, which way the party travels) needs the Shared
    story reader (docs/STORY_MEMORY.md); until a DM shares the adventure, the recap states no
    direction at all.
  **Two models, and style kept apart from content** (owner, 2026-10-20; details Supervisor's).
  - **A creative model writes, a small model formats.** The recap is written as plain text by the
    creative model (`AI_MODEL_CREATIVE`, default Opus 5.5, `claude-opus-5-5`). The formatting model
    (`AI_MODEL_FORMATTING`, default Haiku 5.5, `claude-haiku-5-5`) then puts that text into the
    recap's fixed shape with the structured-output schema: sections, moods, cues and music prompts.
    - **The formatter changes no word of the story.** Code compares the narration's words before and
      after. A mismatch re-runs the formatter once; a second mismatch fails that try.
    - **Every check still runs on the formatted recap,** unchanged: names, secrets, copying, length,
      the fact check and compass directions.
    - Both settings live in `ai.py` with the other model settings. No other file names a model.
  - **The prompt has two separate parts.** Code keeps them apart, and the content part always wins.
    - **The content guide** holds the hard rules: only what happened at the table, the beats with
      their places, no invented fact, name, place or direction, the secret and name rules, and the
      length. It is fixed in code. Nothing a DM writes changes it.
    - **The style guide** is how the recap sounds: voice, tone, pacing, sentence length, tense and
      humour. It is given to the writer in its own tagged block as style only. A fact, a name or an
      instruction to break a content rule inside it is ignored.
  - **Style comes from two sources.**
    1. **Examples of recaps the DM likes.** The writer copies their style and never their content.
       Code enforces this: a name from an example that isn't from the session fails the draft, and
       so does a run of `COPY_WORDS` words taken from an example. DMbot ships a few default
       examples, which go once a DM gives their own. Up to 3 per campaign, kept with the campaign
       and travelling in its backup. **The defaults are the owner's own writing**, with every name
       invented before they enter the public repo. No campaign names, and no published text.
    2. **The DM's feedback.** Once the recap is shown, the campaign's DMs can tell DMbot what they
       think, typed (a button on the recap) or spoken to DMbot (the DM sidebar). DMbot sorts each
       point:
       - **Style** ("shorter sentences", "more humour") becomes a style note. DMbot shows the note
         before keeping it: [Save] [Edit] [Don't keep]. Notes are listed in Settings → Recaps,
         where each can be removed. When two notes disagree, the newer one wins.
       - **Accuracy** (a detail is wrong) and **content** (put this in, leave that out) never go
         into the style guide. They are the DM's corrections for this recap. A Try again uses them
         under the content guide, and a correction the DM gives counts as a fact the DM confirmed,
         for this recap only.
       - **Asking DMbot to help build the story** ("make up what happens next", "write a better
         ending") gets a plain answer and is not kept: "DMbot only retells what happened at your
         table. The story is yours to write."
     A style note can't loosen a content rule: "make things up for drama" gets the same answer.
     Feedback never goes to players and never goes into a transcript, except that spoken feedback
     sits in the raw transcript as a DM sidebar line.
  - **Order:** first the two models (measured on the owner's test transcript with `recap_run`),
    then the style guide and examples, then the feedback.
  **The DM approves the text before any audio** (owner, 2026-10-20; details Supervisor's).
  - **No audio starts by itself.** When the text is ready, the DM reads it and may edit it on the
    website or in Discord, then presses **✅ Approve the text**. Only an approved text goes on to
    narration or music. An edit after approval asks for approval again.
  - **Cues follow a fixed list, checked before anything is sent.**
    - **Moods** (`《…》`) are one of the 18 mood words (`audio/script.py` `EMOTION_VOICE`: tense,
      urgent, battle, somber, sorrowful, melancholy, hopeful, tender, triumphant, defiant,
      mysterious, eerie, ominous, dread, playful, wonder, calm, grim), or a synonym `music.SYNONYMS`
      already maps to one ("tension" counts as tense).
    - **Pacing cues** (`((…))`) are one of the performance cues (pause, short pause, beat, long
      pause, hushed, whisper, whispered, sigh).
    - **Today an unknown word is silently ignored.** From now on, **✅ Approve the text** checks
      every cue first. That covers an unknown word, a bracket left open or mismatched, a mood with no
      words after it, and a cue inside a word. Each problem is shown in plain words with where it is,
      the list of allowed words, and the closest one ("《tensed》: did you mean 《tense》?").
      Nothing goes on until it is fixed. The same check runs on a website edit when it is saved.
    - The name and secret checks run again on an edited text, as at post time.
  - **The DM chooses what to make.** After approval, four buttons: **🎙️ Voice-over**,
    **🎵 Background music**, **Both** and **Text only**. Only what the plan includes is offered:
    narration is Guild and Pro, or the add-on, as now. Any audio counts once against the month's
    narrated-recap allowance. Background music alone is a music bed the length the voice-over
    would have had, with the same mood sections.
  - **The music's style: described, never borrowed.**
    - The music prompts never name a composer, a game, a film or a soundtrack, as now. The owner's
      taste (the instrumental style of classic fantasy MMO scores) is written as a house sound
      palette in plain musical terms, in `audio/music.py`'s style table and the writer's
      music-prompt rule: sweeping orchestral strings, warm horns, a wordless choir, Celtic harp and
      flute, frame drums, slow tempos and long reverb.
    - No recording from those works is ever uploaded, as a reference or otherwise.
    - ElevenLabs' Music v2 can take a short reference track, and it screens each one for copyright.
      Using one is a later, separate decision, and only with audio we own or that is public domain
      or CC0.
  **A secret name is matched as a whole name only** (owner, 2026-10-20; this replaces the
  word-by-word rule, under which any word of three letters or more of a secret name blocked a text).
  One shared matcher is used everywhere a secret name is looked for in text DMbot writes or sends:
  the recap's checks, the narration's second lock, the transcript cleaner's and line fixes'
  checks, and any later one. The voice lock and the text check can never disagree.
  - **Blocked:** the whole name with these differences:
    - **Case.**
    - **A leading article** added, dropped or swapped (a, an, the).
    - **A possessive or plural on any of its words** ('s, s', s, es).
    - **A verb ending on any of its words** (-ed, -d, -ing).
    - **Common title abbreviations**, with or without the full stop: Mr/Mister, Mrs/Missus,
      Ms, Dr/Doctor, St/Saint, Mt/Mount, Capt/Captain, Lt/Lieutenant, Sgt/Sergeant,
      Gen/General, Prof/Professor.
    - **Accents, apostrophes and hyphens** ignored (`name_key`).
    - **The name run together** ("WhiteDeath").
  - **Not blocked:** any part of the name on its own. Each owner example:

    | Secret | Blocks | Does not block |
    |---|---|---|
    | "The White Death" | "a white death", "the white death" | "the white lady", "an icy death" |
    | "Mr. White" | "Mr. White's", "Mister White" | "Mr. Weaton" |
    | "Arcane Brotherhood" | "the arcane brotherhood", "arcane's brotherhood", "arcane brotherhood's" | "the brotherhood" |
  - **A one-word secret** blocks that word with the same differences ("Wit" still blocks "wits";
    the DM can keep it or not). A one-word secret under three letters is not looked for, as now.
  - **An allowed name that contains the secret:** a match that lies inside a longer allowed name
    doesn't count. "Arcane Brotherhood" inside an allowed "Arcane Brotherhood Hall" is the
    hall's name.
  **When a secret is revealed** (owner question, 2026-10-20). Three ways, all the DM's choice:
  1. **On the name's card, any time:** the DM presses 🤫 on that name to turn the secret off.
     This is built, and it can be undone from the card.
  2. **A player says it at the table:** after the session the DM is asked privately: "Keep it
     secret" or "Not secret any more" (#1407, built). Up to five names a session.
  3. **The DM says it at the table (new):** when the DM's own line holds a secret name, by the
     whole-name matcher, DMbot asks the DM at once on the DM screen: "You just said **<name>**
     out loud. Is it revealed now?" with [Yes, players know now] and [No, keep it secret].
     - Yes turns the secret off, through the same change path as the card, so it can be undone.
     - No keeps it, and DMbot doesn't ask about that name again this session.
     - It asks once per name per session, never on Quiet (the after-session question covers it
       then), and only the campaign's DMs can answer.
     - STORY_MEMORY's "who learned it" step (`knows` for some characters) comes with the
       knowledge slice later. For now, Yes means everyone at the table.

     DMbot never assumes a reveal: a DM may say a secret name in an aside.
  **Names the recap uses that DMbot doesn't know yet** (owner, 2026-10-19, #2027, after recap test 3
  refused place names from the owner's own names file): the names list can miss a name, most often a
  new one. So an unknown name in a draft is looked at before it fails the draft:
  - **Said in the session** (in the kept lines, whatever the spelling there): the recap may use it.
  - **Close to a known name** (speech to text misheard it: "care Dinval" for a known "Caer-Dineval"):
    the recap uses the known spelling.
  - **Neither:** it is made up, and the draft fails as before.
  - **Secret names** stay refused in every case.

  Each new name the recap kept goes into the after-session name check as a suggestion (the same queue
  as "📝 Check new names", stored as proposed; the DM still decides). The recap never writes to story
  memory itself: PlotBot and NPCBot record only what the DM confirmed.

  The DM's copy of the recap ends with a **Facts to be confirmed** footer listing them. The narration
  stops at the end of the story: it never reads the housekeeping or the footer. The footer is
  DM-screen content, so it is never posted to the table. As soon as the recap is ready, the same list
  goes to the DM in two places: the DM screen, as "📝 N names from the recap to check" with the
  check's button, and a private Discord message to the DM with the same button (owner, 2026-10-19).
  A DM who isn't online sees both the next time they open Discord. If the DM's private messages are
  closed, the DM-screen note is enough and nothing fails. If the list is still unanswered when the
  next session starts, the DM screen reminds them once. DMbot doesn't watch who is online, so it
  needs no presence permission.
  *Built, recap name fixes (2026-10-19, #2026):* a name passes when every word of it is known, so a hyphenated name from the names file ("Dun-Varra's") is no longer refused; a retry sends the rejected draft (`<previous_draft>`, `<previous_answer>` for the title) with the target and the form and asks for a revision, never resending a draft that names a secret; an answer that only lost its SUMMARY or TITLE label is read leniently and still checked.
  *Built, names the recap kept (2026-10-19, #2027):* `recaps/unknown_names.py` respells a near miss to the known name (same word count and first letter, one letter off, two for names over eight letters, plain English words never), and refuses a word whose closest name is secret; `draft.kept_names` finds the names said in the session that no list has; they are stored in `recap_jobs.to_confirm` (migration 0131, at most 10), shown as **Facts to be confirmed** on the DM's recap page and in `recap_run`'s files, queued as proposed suggestions (source `scan`), and told to the DM once on the DM screen and in a private message with the check's button; a reminder follows once at the next session start.
  *Built, structured outputs for the recap (2026-10-19, #2039):* the recap's prose and title calls ask the model for an answer in a fixed shape (`ai.py`'s `schema`, sent as the Messages API's `output_config`), so the sections, moods and title can no longer arrive as loose text; a refused or cut-off answer is retried with more room, and the draft checks (length, names, quotes, secrets) are unchanged.
- **Recaps go on after every session** (owner, 2026-10-20, after recap run 12 on the owner's own
  session worked end to end: text, voice-over and music). That run is the real-recap check #1382
  waited for.
  - **What dev1 does:** turns `DMBOT_RECAPS` on in the deploy that carries the approval gate
    (#2083), never before it, so no audio is made without the DM's approval. The site's
    `RECAPS_LIVE` turns on with that deploy.
  - **What happens at the end of a session:** DMbot queues the recap (`recap_session`, built). It
    writes from the cleaned transcript only: the Cleaner's text, never DM-sidebar or table-question
    lines, never what the off-topic filter left out, and only people who agreed.
  - **Narration** stays behind `RECAP_AUDIO_LIVE` and the owner's steps on #1385, which include the
    add-on's price.
  - **A short session asks first.** DMbot asks the DM before writing when either of these is
    under **30 minutes**:
    - the session ran from its first to its last line;
    - the game talk left after the off-topic filter, its kept lines' spoken time.

    The DM screen says, for example: "This session was short (22 minutes, 9 minutes of game talk
    after the chat was left out). Write a recap anyway?" with [Write a recap] and [No recap].
    "No recap" spends nothing and counts nothing. "Write a recap" queues it as usual. Only the
    campaign's DMs can answer, and an unanswered question expires when the next session starts.
- **Importing older sessions** (owner, 2026-10-20; details Supervisor's). A DM can load transcripts
  of sessions DMbot didn't record, for example a Craig session written out as text. DMbot cleans
  them and builds the campaign's memory from that history.
  - **Where:** the website, on a new **Import sessions** page for the campaign's DMs. It takes up to
    20 files at once, each up to 2 MB of text. The formats:
    - Craig's plain-text export, as `recap_run` reads it today (`devtools/recap_run/transcript_file.py`
      moves into core as the importer);
    - `[hh:mm:ss] Name: words` lines;
    - DMbot's own transcript download.

    Others are added on request.
  - **Or a Google Doc link** (owner, 2026-10-20): instead of uploading, the DM may paste the share
    link of a Google Doc that holds a transcript, one link per session.
    - **How it's read:** DMbot reads it through the guarded link reader `dmbot.fetch` that house
      rules and name lists already use: Google Docs only, the plain-text export, the same size
      limit and time-out. It reads the doc again at Preview, Check and Import (Supervisor,
      2026-10-20, #2143), so the DM keeps it shared until Import has started, and the page says
      why a doc that stopped opening between steps failed. Pinning the text read at Preview is a
      follow-up.
    - **From there,** it is exactly like an uploaded file: the same formats, consent mapping and
      checks, and the text isn't kept.
    - **Who can read the doc:** it must be shared as "Anyone with the link can view". DMbot never
      asks for access to the DM's Google account for this. If the doc isn't shared that way, the
      page says so plainly and how to change it. Reading private docs through Google's narrowest
      `drive.file` access waits for Phase 7.
  - **From Discord** (owner, 2026-10-20, #2154): an **📥 Import sessions** button under the DM
    screen's ⚙️ Settings, and `/dmbot import`, both open the website's Import sessions page with a
    link button, for the campaign's DMs only. Nothing is imported in Discord: matching speakers,
    dates and checks need the page.
  - **Consent is the hard rule, as everywhere.** Before anything is stored, the DM matches each
    speaker name in a file to a member of the server.
    - A speaker who matches a member who has agreed to be recorded (the current consent wording)
      is kept.
    - Every other speaker's lines are dropped before saving and are never stored: a person not
      matched, a person who hasn't agreed, and bots.
    - The page says how many lines each choice keeps or drops.
    - The DM's word that "everyone agreed" isn't enough. A player who wants their lines kept agrees
      in Discord first, as for a live session.
    - Imported sessions are read by the whole server like any transcript. The consent request
      already says so.
  - **Each file becomes a stored session.** The DM sets each one's date (the page suggests one from
    the file name) and its place in order. It is marked *imported*, with the file name, who
    imported it and when.
    - Lines without times get times estimated from their words.
    - The Cleaner and the off-topic filter run on it like a live session, after the fact.
    - Craig's `{non-game context}` placeholders count as left-out talk.
  - **Building the campaign:** after the import, DMbot runs the after-session steps on the imported
    sessions in date order:
    - the name scan, which asks the DM about new names as usual;
    - the story-memory steps that are on (the extractor stays behind its own switch).

    Nothing becomes a confirmed fact without the DM.
  - **Recaps of imported sessions** are optional and per session (a **Write recaps** choice on the
    page). Each counts against the monthly allowance. The newest imported session's recap becomes
    the "last time" bridge for the next live session.
  - **Plans:** importing is part of the paid plans, like recaps. Costs are the Cleaner's and the
    name scan's fast-tier calls, so a batch is capped and its estimated cost is shown before the
    DM starts it. Try It campaigns have no import.
  - **Isolation:** everything stays in that one campaign of that one server. The files themselves
    aren't kept after the import, only the stored sessions.
  - **Order:** the importer and its storage with the consent mapping (core and API), then the
    website page, then building the campaign and the optional recaps.
- **Three plain ways to approve a recap** (owner, 2026-10-22, replacing "Post to the table" and "Don't post",
  which read as unclear). On the recap page, and the same words on Discord:
  - **✅ Approve and post text only:** DMbot posts the text to the table, after its last check, as Post did.
  - **🎙️ Approve and make audio to review:** asks which (voice-over, background music, or both; only what the
    plan includes), makes it, and shows it on the page to play. Nothing is posted until the DM presses
    **Post to the table** or **Keep it, don't post** there. Where audio isn't available (the plan, the
    month's allowance, or narration not set up on the server), the button shows greyed out with the reason.
  - **📋 Approve but don't post:** the recap is approved and kept, and nothing goes to the table. The page
    then offers **Copy the text**, the share link and the download, so the DM can share it their own way.
    It can still be posted later.
  - **Try again** stays. "Don't post" (setting a recap aside unread) is gone; a recap the DM doesn't approve
    simply stays a draft.
  - **While it goes out, the page shows it working** (owner, 2026-10-22): after any button that posts or makes
    audio, the page shows "Getting it ready for Discord…" (or "Making the audio…") with a running clock (0:42)
    and the step it's on (the last check, then posting). It updates by itself until it ends: "Posted to your
    table" with a link to the message in Discord, or what went wrong and what to do next. Leaving and coming
    back shows the same clock, counted from when it started.
- **Every recap says which session it is** (owner, 2026-10-22). Wherever a recap is read (the DM's recap
  page, the list of recaps, and the shared recap page), its heading line names the campaign, the session
  number, the day it was played and how long it lasted: "**<Campaign>** · Session 4 · Played Tuesday 21
  October 2026 · 2 h 35 min". The session number is the one DMbot uses everywhere else (the transcripts'
  "Session N"). The length runs from when DMbot started listening to when it stopped; an imported session
  uses its transcript's length. The day is shown in the reader's own time zone.
- **The recap page: three ways to fix a recap, and the names to check** (owner, 2026-10-20, after
  liking recap run 12's review page; details Supervisor's). The website's recap page (My Account →
  recaps) is where the DM reviews a recap before any voice-over or music. **Discord opens it** (owner,
  2026-10-20): the recap's line on the DM screen and its private message carry a **📝 Review the
  recap** link button that opens this page. The richer tools live on the website, not in Discord
  forms; editing inside Discord was dropped (#2110, closed). The page already has the text,
  the edit box with the cue check, and Approve with the four choices. It gains:
  1. **Edit it directly** (built): the edit box, as now.
  2. **Tell DMbot what to change, by typing:** a box, "Tell DMbot what to fix", for example: "The
     weapons were taken at the keep's gate, not the tavern. Shorter opening."
     - **The sort:** it goes through the same sort as the Discord feedback (#2076).
       - **Accuracy and content** become this recap's corrections, and the DM gets **Rewrite with
         these**, which uses one of the recap's tries.
       - **Style** becomes a note the DM approves.
       - **Asking DMbot to build the story** gets the plain no.
     - **The new draft** replaces the old one only when it passes every check, and it needs
       approval again. The DM sees each point as DMbot understood it before anything runs.
  3. **Tell DMbot by voice:** a 🎤 button records the DM in the browser. The site asks the browser
     for the microphone only on this page, only when pressed, and only for this site.
     - **On release:** the clip goes to DMbot's speech-to-text and comes back as text in the same
       box, "Here's what I heard", for the DM to read, correct and send. It's the typed path from
       then on.
     - **Privacy:** the clip is the DM's own voice, given by pressing. It is transcribed and thrown
       away, never kept, never in any transcript, and only the campaign's DMs can use it.
       - **Consent first** (Supervisor, 2026-10-20, #2159): the DM must have agreed to recording
         (the current terms, and the speech company in use) before the website accepts a clip. A
         clip waits in the database only until the bot turns it into words, and at most about 10
         minutes, swept across every server.
       - **Length:** the page records up to **a minute** (Supervisor, 2026-10-20, #2160), because
         the note box holds 1,000 letters. The server accepts up to 2 minutes as a ceiling.
       - **Listening time** counts the clip's real length; a clip never costs a minute it didn't
         use.
       - The privacy page names the speech company.
     - **The security header** `Permissions-Policy: microphone=()` changes to `microphone=(self)`
       for the recap page only.
     - **Names help, as in a live session** (owner, 2026-10-20): the clip is transcribed with the
       same campaign name hints a live session sends (Deepgram's keyterms, under the same rules about
       which names may go), and the text goes through the Cleaner's name fixing, as a DM's live line
       does. So "care Dinval" comes back as the known name. This costs almost nothing extra.
     - **It counts as listening time** (owner, 2026-10-20). Each clip's length is added to the
       campaign's listening minutes for the month, the same hours meter as a session in Discord.
       The site's words about hours say DMbot listens both in Discord and on the website: "the time
       DMbot is listening to you, in Discord or on the website".
  4. **Names to check, answered on the page:** the recap's **Facts to be confirmed** list (#2027) is
     shown under the recap with an answer for each name, the same answers as Discord's
     📝 Check new names:
     - **Yes, it's right**: confirm it, picking what it is (a person, a place, a thing…);
     - **It's actually…**: pick a known name it should be, or fix the spelling;
     - **Not a name**: deny it;
     - **Explain**: the DM types a short note on who or what it is, kept with the name.

     Answering on either the page or Discord answers both.
     - **How** (#2113): the page lists the recap's waiting suggestions and sends each answer to a
       small queue; the bot applies it through the same memory paths as Discord's check (one change
       log, one Undo), so the name leaves both lists. The queue holds the DM's spelling or note for an
       hour at most and never logs it. A name that is a secret one is never listed or offered.
- **Recaps on the website, and only two buttons in Discord** (owner, 2026-10-20; this replaces
  the buttons below).
  - **[Recaps]**, one button, opens the campaign's recap list on the website, newest session
    first. Each entry opens that recap's page, where the DM views, listens, edits, fixes and
    approves it.
  - **[Share a recap]**, for the campaign's DMs, picks a recap and offers **Copy the link** or
    **Send the link to the players**, which posts it in the campaign's `dmb-chat`.
  - **Signing in:** editing and approving need the website sign-in, by Discord or by email, as now.
  - **Viewing and listening need no sign-in** (owner): anyone with a recap's link can read it and
    play its audio. The rules that keep this safe:
    - **Only posted recaps:** a link works only after the DM has approved the text and posted or
      shared the recap. A draft stays visible to the campaign's signed-in DMs only.
    - **Only the table's version:** the shared page shows the posted text and audio and nothing
      else. No "Facts to be confirmed", no housekeeping, no secret name (checked again at share
      time, as at post time).
    - **An unguessable link:** each recap has its own random address, never the campaign's or
      the server's ids. The DM can turn a link off, or make a new one, which ends the old one.
    - **Not in search engines:** shared pages carry `noindex`.
    - **The privacy page** says that a shared recap can be read by anyone who has its link.
  - **Disk:** reserve 1 GB of recap audio per campaign. That covers 100 sessions of recaps up to
    3 minutes in today's encoding. See docs/costs.md, "Disk space for recap audio".
- **PlotBot checks every recap before it is accepted** (owner, 2026-10-20; details Supervisor's;
  the first piece of Phase 5c, docs/STORY_MEMORY.md).
  - **When:** a plot check runs when a recap's text is ready, and again after every change to it,
    including a change to an older recap.
  - **What it reads:** the recap against the running campaign:
    - the earlier accepted recaps;
    - the confirmed story memory (facts, conditions, who carries what, threads and promises);
    - and, with the Shared story switch on, the adventure's plan.
  - **What it finds:**
    - contradictions, such as someone dead who acts, an item in two hands, or a place described
      differently;
    - dropped threads and broken promises;
    - timeline slips;
    - a secret that the recap would reveal.
  - **How it works:** the code checks of section 4 first, then one careful-tier call for judgment.
    Each issue gets a level (possible, probable, likely), the recap sentence it is about, and the
    earlier source it conflicts with, quoted with its session.
  - **DM-only.** The issues show only on the signed-in DM's recap page. They never appear on a
    shared link, to players, or in a transcript.
  - **Talking it through.** For each issue the DM can chat with PlotBot on the page: "It's a
    different Ulfgar", "he came back as a ghost", "my mistake". Each issue ends in one of four ways:
    1. **Fix the recap:** the point becomes a correction, through the typed-instructions path.
    2. **The story changed:** PlotBot proposes the plot-tracking update, a new dated fact or
       condition, a thread moved, or a link, and **saves it only when the DM presses Save** (PlotBot
       records only DM-confirmed facts, as the hard rule says).
    3. **Not a problem:** the DM's explanation is kept with the issue, so it isn't raised again.
    4. **Leave it as is.**
  - **Accept.** Accepting the recap (the approval of the text) waits until every issue has an
    answer. **The DM always decides:** "Leave it as is" is always one of the answers, so PlotBot can
    advise but never block.
  - **An accepted recap becomes part of the campaign notes.** It is kept as that session's summary,
    which PlotBot, the pre-session note, recall and later plot checks read. Each accepted version is
    kept, so the history is never lost.
  - **Changing a recap later,** even an old one: PlotBot is given the before and after, checks the
    changed parts against the campaign and against the later recaps that may depend on them, and
    Accept waits for the new issues' answers.
  - **Cost and limits:** about one careful-tier call per check, plus the chat. A rate limit per
    campaign applies, and it is counted in docs/costs.md.
  - **Early campaigns:** with little confirmed memory yet (the extractor is off until #1282), the
    check leans on the earlier accepted recaps. It grows more useful as memory fills.
- **A recap knows what was last time's recap, and PlotBot decides what matters** (owner,
  2026-10-21: "The PlotBot and Recap are the two most important features of the entire DMbot
  project"). The owner's problem: session 16 opened with a spoken recap of session 15, so the recap
  of 16 retold 15, and session 17's spoken recap of 16 then carried 15 again. Details Supervisor's.
  - **How a session opens.** Most transcripts start with some of these, usually in this order, any of
    them missing:
    1. table talk ("sorry I'm late", "what did you do last weekend");
    2. catching up on mechanics ("did we level up?", "did she still have exhaustion?", "did we get
       loot from him?");
    3. the recap of last time, read by the DM or told by the players;
    4. the new session itself.
  - **Every recap DMbot writes has fixed opening and closing lines**, so it can be found when it is
    read aloud next time. It opens with "Previously, on <campaign name>…" and closes with one of ten
    endings (owner, 2026-10-21), all "And so we continue, the <word> <kind> of <campaign name>.":
    1. And so we continue, the epic adventure of <campaign name>.
    2. And so we continue, the great adventure of <campaign name>.
    3. And so we continue, the legendary adventure of <campaign name>.
    4. And so we continue, the grand adventure of <campaign name>.
    5. And so we continue, the heroic adventure of <campaign name>.
    6. And so we continue, the epic tale of <campaign name>.
    7. And so we continue, the epic saga of <campaign name>.
    8. And so we continue, the epic journey of <campaign name>.
    9. And so we continue, the perilous adventure of <campaign name>.
    10. And so we continue, the epic quest of <campaign name>.

    The ending rotates: each recap takes the next one in the list after the campaign's previous recap,
    so two sessions in a row never end the same way. The finder looks for the shared shape ("and so we
    continue … of <campaign name>"), not one exact sentence. The narration says both lines. Only the
    campaign's display name is used, never a secret one.
  - **Step 1: split the session (before the beats).** DMbot finds where each part begins and ends.
    - **The opening and closing lines,** matched loosely, since speech-to-text garbles names.
      The closing line is often not read, so it is a strong sign, never required.
    - **Lines that retell earlier sessions,** compared with the **previous session's own transcript**
      (its play part) and its beats, and with the previous recap when there is one. This is the main
      signal, not the fixed lines (owner, 2026-10-21): a table may skip the ending, skip DMbot's recap
      entirely, or tell its own recap in its own words, and DMbot must still tell the recap from the
      new story. Fixed lines and an accepted recap only make it surer.
    - **One fast-tier call** labels the opening lines as table talk, catch-up, recap or play. Code
      checks that the parts are in order, and that the "recap" part really overlaps the earlier
      session (its transcript, beats or recap). When it does not, the split is kept but marked unsure.
    - **Backstory is never the recap** (owner, 2026-10-21). "Recap" means only a retelling of
      **earlier sessions of this campaign**, so it needs an earlier session to retell. A first
      session has no recap part. A character's backstory, a flashback, or a player telling where
      their character comes from is **play and character development**, wherever it comes in the
      session (the opening included), and it is never cut as last time's recap. When in doubt
      between recap and backstory, it is play.
    - **What each part is used for:**
      - *Table talk* is left out.
      - *Catch-up* feeds the housekeeping bullets, and only what the table settled.
      - *The spoken recap* is **context, not news**: its mood and its reminders of what matters may
        shape this recap, but no event in it is told as something that happened this session.
      - *Play* is where this recap's beats come from.
    - **Stored with the session** (the line ranges and how sure), so the beats, the story extractor
      and the import read the same split, and a test run can show it.
  - **Step 2: PlotBot marks what matters (after the beats).**
    - **What it reads:** the story so far, meaning the accepted recaps and the confirmed story memory
      (threads, factions, who carries what, promises).
    - **What it adds:** a note to each beat, and to any small detail the beats left out, saying why it
      matters and which thread or fact it ties to. For example, "he wears an amulet with a shard of
      obsidian" ties to the obsidian faction, so it must stay in the recap.
    - **Secrets:** significance decides **what is kept and stressed**, never what is revealed.
      The recap may say why something matters only when the players already know it: said at the
      table in a consented line, in a posted recap, or shown in the **players' view** (what the NPC
      list itself shows the table: confirmed, never secret, from `RecallService`'s players audience;
      Supervisor, 2026-10-21, #2270, since the players can already read it there). When only the DM knows (the faction is secret), the
      recap keeps the detail and does not explain it. DM-only knowledge never reaches the text,
      as today's secret checks already ensure.
    - **Early campaigns:** with little confirmed memory, PlotBot leans on the earlier accepted
      recaps, as the plot check does.
  - **Step 3: the writer** (the creative model, as today) writes from the beats and PlotBot's notes,
    with the spoken recap as context. Its three jobs, in order:
    1. plot development;
    2. character development, including backstory and flashbacks told at the table;
    3. where the characters came from, where they are, and where they are going next.

    Plot and character development are DMbot's two most important jobs (owner, 2026-10-21).
  - **"Where next" and motives only as said.** A character's plan, goal or reason appears only when a
    player or the DM said it in a consented line of this session or an earlier posted recap, and it
    cites that line. **DMbot never speaks for a player character** and never guesses a motive. If
    nobody said why, the recap says what they did, not why.
  - **Step 4: the critics, until they agree.**
    - **Who reviews each draft:**
      - PlotBot: anything that matters left out or wrongly weighted, contradictions, continuity, a
        secret;
      - the grounding check (today's beat and claim checks);
      - a motive check: every intention, plan or reason cites a line where it was said;
      - the style grader.
    - **How each answers:** pass, or a short list of specific changes.
    - **The loop:** the writer revises with the changes, and the critics read the new draft. This
      repeats until all of them pass, **at most four rounds**.
    - **If they still disagree,** the best draft goes to the DM, and the remaining notes show on the
      DM's recap page. The DM decides, as always.
    - **This is DMbot's own loop,** before the DM sees anything. PlotBot's DM-facing check before
      Accept (above) still runs on the result.
  - **Cost:** the owner accepts **two to five times** today's recap cost for this. The per-recap call
    ceiling and the campaign's daily ceiling rise to match. docs/costs.md is updated in its own PR,
    which needs the owner's label.
  - **Order:** first the split and the fixed lines (they fix the owner's bug on their own), then
    PlotBot's notes, then the critic loop. #2122 (the DM-facing plot check) builds on the same reading
    of the campaign. Each step is measured with `recap_run` on the owner's own transcripts, run
    locally and never put in the repository, before and after: the split's accuracy, and the owner's
    read of the recap.
- **Where the DM hears about it:** on the DM screen, always, with three buttons that survive a
  restart: **Read it**, **Post to the table** (asks once: "Post it to the table now? The players
  will all see it.") and **Not this one** (#1455, #1457); and by email unless the DM turned the
  email off. The DM-screen **Recaps** panel on ⚙️ Settings holds the three choices (#1421,
  #1422): recaps on or off, the email on or off, the narration on or off. DM only.
  Once a recap is posted or skipped, its DM-screen line loses its buttons and says "Posted." or
  "Skipped."; so does the other copy of the line (the re-post at a session start) while the bot still
  holds it in memory (#1509). The same for a player's suggested fix ("Accepted." / "Kept as is."). **Read
  it** keeps its buttons. Nothing is stored, so after a restart a copy is found again only when
  someone presses it.
- **"How to say it", with a suggestion and the DM's own voice** (owner, 2026-10-21). Wherever a DM sets
  how a name is said (the website's "said as" field, #1451, and the name card's "Say it this way?",
  #2195):
  - **A suggestion first:** DMbot offers a respelling from the written name ("Ka'zeth" → "KAH-zeth"),
    one fast-tier call with the name and its kind only, which the DM may take, change or ignore.
  - **🎤 Say it:** on the website, the DM records the name in the browser, can **▶️ Play it back** (in the
    browser, before anything is sent) and re-record, then **Use this**. DMbot turns the clip into a
    respelling in plain letters with the stressed part in capitals ("kah-ZETH"). The DM sees it, edits it
    if needed and saves. Discord has no microphone for buttons, so the name card gets a **🎤 Say it**
    link button straight to that name on the website (one tap, the link-button rule).
  - **🔊 Hear it:** the website reads the respelling aloud with the browser's own voice, so the DM can
    check it. No cost, and it isn't the narrator's voice.
  - **How the clip becomes letters:** a small speech-sound recogniser on our own server turns the clip
    into speech sounds (IPA). Plain code turns those into the respelling. No outside service hears the
    clip. If the recogniser isn't installed or fails, the DM is told plainly and types it instead.
  - **Privacy and consent:** pressing 🎤 is the DM agreeing to that one clip. It is held only while it is
    turned into letters, then deleted, and is never in a transcript, a backup or a log (counts only). At
    most 10 seconds and 1 MB. DMs only; per campaign.
- **Narration and music** (#1383, #1415, #1417): a recap can come with a narrator's voice and a
  music bed as two audio files, made with ElevenLabs from the cleaned recap with the campaign's
  "said as" spellings and no secret names, kept on a named volume (`recap-audio`) that the
  website's API reads read-only. It needs the owner's ElevenLabs account (#1385; Creator plan or
  higher, `pcm_24000`), and `RECAP_AUDIO_LIVE` on the site. Narration comes with Guild and Pro
  (`RECAP_AUDIO_PLANS`); on the other paid plans it is an add-on priced from one constant
  (`RECAP_AUDIO_PRICE_CENTS`, web `recap-audio.ts`; the price is the owner's, #1385). A DM may
  ask for the audio again once every 10 minutes (#1418). **A posted recap's narration can be made
  again** (Supervisor, 2026-10-21, #2183) when its text changed after the narration was made (a
  spelling fix, #2177), or when an earlier remake failed: the recap page gets the new narration,
  and the message already in Discord keeps the old one, which the page says. The 15,000-credit cap
  is per making, so a remake starts its own count; the monthly meter still counts a recap's audio
  once, in the recap's own month. A recap posted while the audio the DM asked for is still queued
  gets that audio afterwards, for its page. Recaps left in that state before this change (when they
  never got it) are marked failed by migration 0149, so nothing old is narrated without a new press.
- **Monthly allowance** (Supervisor decision 2026-10-16, #1428, #1437, #1445): each campaign gets
  **10 recaps a month** and **8 narrated ones** (`DMBOT_RECAPS_PER_MONTH`,
  `DMBOT_RECAP_AUDIO_PER_MONTH`; the site mirrors them in `recap-words.ts`, change both
  together). Free access and grants are not limited. When the allowance is used the DM screen
  says so, with the day the next one comes. Reason: one recap per weekly session with room for
  a second table, and narration costs real money per minute (docs/costs.md, ElevenLabs).
- **Deletion:** a recap goes with its campaign, and the DM may drop any draft; the raw
  transcript is not changed by a recap. A recap is never in a shared ruleset.

**Page changes: magic dust, and runes on the banner** (owner, 2026-10-15; Supervisor's details).
Moving between the site's pages gets a short magic-dust dissolve: the old page breaks up into
sparkling dust while the new one fades in, about 700 ms (owner, 2026-10-15: doubled so the
runes can be seen), built with the browser's own page transitions (CSS `@view-transition`, no
script, no library, nothing new in the security headers). Browsers without it change pages as
before. Only the page's banner (its main heading, the hero on the home page) gets the rune
effect: it breaks into arcane runes as the page leaves, and the new banner forms out of runes as
it arrives. The runes are decoration, hidden from screen readers, with a small rune font served
from our own site. **No dice** (owner: not for now).
**The legal pages stay plain:** terms, privacy and refunds get no dust and no runes. Anyone whose
device asks for less motion gets a plain page change everywhere. It must never slow a click or move
the page's layout, and it adds at most about 15 KB.
**Stripe replaces Lemon Squeezy (owner, 2026-10-13).** The owner moved the payment company
from Lemon Squeezy to Stripe. The Lemon Squeezy adapter (#1083, #1086: `dmbot.web.lemonsqueezy`)
is replaced by a Stripe one, `dmbot.web.stripe` (`PAYMENT_PROVIDER=stripe`; keys and ids in
`.env.example`, set on the server by dev1 through `scripts/set-key`). Every rule in `payments.py`
stays, and so does the meaning of each event kind:
- **Signature:** the `Stripe-Signature` header, HMAC-SHA256 of `"<t>.<raw body>"` with the
  endpoint's signing secret, compared as bytes; an event older than 5 minutes is refused.
- **Events:** `invoice.paid` with `billing_reason` `subscription_create` is `subscription_started`
  (a Stripe subscription exists before it is paid; DMbot starts it only on payment);
  `subscription_cycle` is `subscription_renewed`. A plan change is `customer.subscription.updated`
  whose price has changed, sent when it takes effect, never when it is scheduled.
  `invoice.payment_failed` is `payment_failed` (the writer gives grace only after an earlier
  payment). `customer.subscription.updated` that sets `cancel_at_period_end` is
  `subscription_cancelled` (#824), and clearing it is a resume. `customer.subscription.deleted` is
  `subscription_ended`. A paid `checkout.session.completed` in payment mode for the +10 hours
  price is `extra_hours_bought`. `customer.updated` is `email_updated`: it only keeps the payer's address as the account's
  payment email (#1242). The account is found by the customer id stored with its plan (a Customer
  object carries no id of ours), so a customer DMbot has no plan for is ignored, and an event
  older than the address kept never replaces it. The Stripe dashboard's webhook endpoint (https://dashboard.stripe.com/webhooks) must
  list `customer.updated` too, or a changed address arrives with the next invoice instead.
  A payment email is whatever the payer typed into Stripe: if one address belongs to two accounts,
  sign-in by email finds neither (by design), so the person signs in with Discord instead.
  Everything else is ignored.
- **Ids, times and periods:** an event's id is Stripe's own event `id`, and `occurred_at` is its
  `created`. The billing period is the subscription's current period start and end.
- **Who it is:** the person is the `user_id` in the checkout's metadata, copied to the
  subscription's metadata.
- **Test and live:** an event whose `livemode` does not match the key's mode is ignored. Test mode
  is Stripe's test keys, so no separate test switch.
- **Checkout** is a Checkout Session made by an API call (email server-side, never in the
  address), with the plan's Price id. (The $1.99 first month of Table and its once-only coupon were removed by the
  owner on 2026-10-14; see "Accounts" below.) Each checkout request carries an **idempotency key** (a hash of the request) and the same
  expiry for every request in a five-minute window, so a double-click or a retry after a timeout
  gets the same session instead of a second live one (two completed checkouts would be two
  subscriptions). The session expires 32 to 37 minutes after it is made (#1122).
- **A plan change while a cancel is pending** (decided on #1122, not two events): Stripe sends
  one update, which DMbot treats as the cancel. Only the end date is stored, and the new plan
  applies if they resume or resubscribe. It is logged ("only the end date is stored") so the
  owner can see it happened. Whether Stripe's portal offers a plan change on a subscription set
  to cancel is for the sandbox run (#1096) to show.
- **Billing and deletion:** the billing page is a Customer Portal session. Account deletion sets
  `cancel_at_period_end` (a cancel that is already done succeeds). A refunded extra-hours order
  stays credited (manual for now).

**Stripe is the seller (owner, 2026-10-13, #1096: answer A).** DMbot sells through Stripe's
merchant-of-record option, so Stripe stays the seller of record, as Lemon Squeezy was. It handles
sales tax, invoices and EU/UK withdrawal rights. The signed-off legal pages keep their wording,
with the payment company's name changed to Stripe (#1111). If Stripe doesn't offer that option
to this account, this comes back to the owner before anything is sold: a plain Stripe account
would make DMbot LLC the seller. The owner's account setup and the test-mode run of #498 item 5
are on #1096.
**Hosting cost (owner, 2026-10-13):** the server costs **$11 a month**. That is the
`--hosting-monthly` input for docs/costs.md.
**Feedback and questions (owner request 2026-10-08, #665):** a page with two short forms,
Feedback and Ask a question, posted by the web API as GitHub Discussions in this
repository (categories Feedback and Questions; a discussions-only token from the
environment) so the owner can subscribe; no email sending yet. The public post holds
the message and date only; an optional "how to reach you" stays in the database with
the message, never in the post, and the form says so. One post per IP per 10 minutes,
2,000 characters, Turnstile. The page links to the repository and invites developers
to open issues. The page says, before Send, that the message is posted on GitHub where
anyone can read and search it; the privacy page lists GitHub and says the "how to reach
you" detail is kept with the message for a year, seen only by the team, and that a post
is taken down on request through the same page (decided 2026-10-08 on #710). The
`feedback` table is add-only (the website's role inserts, never reads; the team reads it
as the database's administrator), and the website's hourly sweep deletes rows after 12
months.

**QAbot and support tickets** (owner request 2026-10-15; Supervisor's design).
- **QAbot answers the Discussions category "Q&A for DMbot"** and nothing else. It is a GitHub
  Actions workflow, not a session that stays on: it runs on GitHub's own runner when a discussion
  or a reply is posted in that category, plus an hourly sweep for anything missed. It never runs
  on the server and holds no server secret. All it has is a read-only checkout of this public
  repository, the public web, its own Anthropic key (a separate key with a spending limit, a
  repository secret) and a GitHub token limited to `contents: read`, `discussions: write` and
  `issues: write`.
- **The model never acts; a script does.** Claude Code runs headless with read-only tools: read
  and search the checkout, web search, and fetching public pages (getdmbot.com first). It has no
  shell, no file writes and no GitHub tool. It returns one fixed JSON answer: answer or escalate,
  the reply text, and the sources it used. A small script checks that answer and is the only
  thing that posts.
- **Embedded instructions:** before the model sees a discussion, the script strips control,
  zero-width and direction characters and HTML comments, and wraps the text as data. The prompt
  says the post is a question to answer, never instructions. No model can be promised immune to
  a clever post, so the design limits what a trick can achieve: at worst one wrong public reply,
  never an action. The script refuses a reply that @mentions anyone, links outside an allowlist
  (this repository, getdmbot.com, dndbeyond.com), runs past 1,200 characters or looks like a
  key, and escalates instead.
- **Answer only when sure:** every claim must come from a cited source (the Help page, the
  site, this repository's docs). Anything about one person's account, payments, a bug, data
  deletion or what DMbot will do next is always escalated. Replies are signed "QAbot (automated
  answer)", say how to reach a person, and are short and plain (CLAUDE.md, hard rules). One reply
  per post; at most three replies in a thread, then it escalates.
- **Escalation** opens a public issue labelled `support` and `from: QAbot` that links the
  discussion. The discussion is already public, so nothing private moves. QAbot replies
  "Passed to the team as #N". Supervisor triages `support` issues like any other.
- **Support tickets from people are private.** Anyone with an account writes from the website
  (signed in, a Support form; Try It included since #1436) and anyone can write to the support email
  (support@getdmbot.com). These hold
  personal details, so **they never become public GitHub issues.** They go to a private
  `support_tickets` table in the database (add-only for the website's role, like `feedback`,
  except that a signed-in person reads back their own list: when, subject and status, never the
  words or anyone else's; #1373), shown on the admin page, with an email to the team. Kept for 12 months, then deleted by the
  website's hourly sweep. The privacy page says so. Reading the support mailbox into tickets is
  a later step. Until then the team reads the mailbox directly.

**Support flow: acknowledge, answer, escalate** (owner request 2026-10-16; Supervisor's design;
#1431, #1432, #1435, #1436, #1452, #1458, #1459). Anyone with an account, **Try It included**
(owner, 2026-10-16, #1435), writes to the team from the account page. Then:
- **Acknowledge** at once from `MAIL_SUPPORT_FROM` (info@getdmbot.com), subject only, never
  the words, to an account with an address. It says in three short sentences what happens next
  (#1500): DMbot's helper (a program, not a person) answers first by email, usually within a few
  minutes; replying to that email reaches it; a person takes over if it can't help or you ask.
  When the helper is off (no model or key) it says a person will reply and does not mention it.
- **Answer** by email from DMbot's **AI helper**, the strongest tier's current Claude model
  (`SUPPORT_AI_MODEL` empty means the tier default), with fixed on-topic instructions; it says
  it is a program, not a person. It answers at most 5 times per ticket. Replies by email come
  back through the mail service's inbound webhook (`POST /mail/inbound`, signed with
  `MAIL_INBOUND_SECRET`; 404 while unset) to `info+ticket-<id>-<token>@`. **Replies go to a
  subdomain** (Supervisor, 2026-10-17, #1717): the bare domain's mail goes to the team's inboxes
  through Cloudflare Email Routing, so the mail service receives only `reply.getdmbot.com`
  (`MAIL_REPLY_DOMAIN`; empty keeps the sender's own domain). The token proves the ticket, not the
  domain, so either domain reads.
- **Escalate** to a person when the helper's sentiment check says the person is unhappy, they
  ask for a person (every helper answer ends "Want to talk to a person instead? Reply with just the word PERSON
  and someone on the DMbot team will write back to you."; the whole word *person* or *human*, any case, hands over as
  `asked` with no AI call, #1518), the 5-answer limit is reached, the account has no address, or the helper
  was down for a day. The alert goes from `MAIL_ALERTS_FROM` (the `<dmbot private>` email, docs/EMAIL.md) to
  `SUPPORT_TEAM_EMAIL` (support@getdmbot.com) with the reason; the admin page shows the thread,
  "Needs the team", the reason (#1459) and a reply box; the team's reply is emailed from info@
  and the ticket becomes answered.
- **Closing:** the website's hourly sweep closes an *answered* ticket the person has not written to
  for 14 days (`SUPPORT_AUTO_CLOSE_DAYS`, #1505); new and escalated ones are never closed that way.
- **Owner alerts and the reserved sender (2026-10-18, #1907, from #1425, approved by the owner):** when the
  AI service is out of credit or refuses its key, Deepgram refuses its key or is silent for 10 minutes,
  Stripe refuses its key, mail fails or the database is down at start-up, the owner gets one short email
  (fixed sentence, once an hour per problem, 5 a day, slots kept back from `MAIL_DAILY_CAP`) at
  `OWNER_ALERT_EMAIL`, or a private Discord message when mail is what failed. `admin@getdmbot.com` is
  refused as any From setting. See docs/EMAIL.md, section C.
  An emailed reply to a closed ticket reopens it (back to waiting, or to the team if a person had
  a hand in it).
- **Weekly email to the team** (#1514): the first sweep after Monday 08:00 UTC sends `SUPPORT_TEAM_EMAIL`
  the admin page's "this week" sentence (counts only, never a ticket's words) from
  `MAIL_ALERTS_FROM`, subject "DMbot support this week"; nothing for a week with no questions, nothing
  without a mailer or team address, and never twice (one row, `support_week_mail`, migration 0096). The first send after this ships can
  come on any day of that week.
- **What the person sees** on their account page: subject, date, status ("Answered by DMbot's AI
  helper", "Answered by the team", "With the team") and the first line of the last answer, never
  anyone else's (#1458). **Opening a ticket shows its whole conversation** (owner, 2026-10-21,
  replacing #1458's "never their own words back"): their question, each of the helper's answers,
  each of their emailed replies and each of the team's replies, oldest first, each labelled
  "You", "DMbot's helper (a program)" or "DMbot team" with its date. Only the ticket's own account
  sees it. The privacy page says all of this; the
  site's words show only when `SUPPORT_AI_LIVE` is on (`support-words.ts`).
- **The email chain is kept** (owner, 2026-10-21). A ticket's history is the conversation: the
  question, the helper's answers, the person's emailed replies and the team's replies, all kept
  in `support_tickets` and `support_replies` for the ticket's life (12 months), never trimmed to
  the latest. Every email about a ticket to the person (the helper's answer, the team's reply, the
  "a person will reply" note) carries the conversation so far below the new message, newest first,
  each part labelled and dated as on the account page, like an ordinary email reply. The history
  starts with one fixed line ("Earlier in this conversation:") that `support_mail` cuts at, so a
  reply from a mail program that adds no ">" still stores only the new words. All of a
  ticket's emails share the subject `Re: <subject>` and thread together in the person's mail
  (`In-Reply-To` and `References` naming the ticket's earlier emails), and the team's "Needs the
  team" email carries the whole conversation too. An emailed reply is stored without the quoted
  text below it (`support_mail`), since the earlier parts are already kept; a reply that is only
  quoted text is not stored. The 30-reply flood limit per ticket stays. Mail the team answers
  straight from the team inbox, outside the admin page, is not part of the ticket.
- **Spend** is bounded by the 5 answers per ticket, the Try It limit of three tickets a day and
  one waiting, and the owner's spend limit on the Anthropic console. The web API holds
  `ANTHROPIC_API_KEY` for this one use (a compose pass-through, never a web setting).

**Deploys and CI (Supervisor, 2026-10-16).** dev1 deploys only a `development` head whose CI is
green. One exception was granted once: `e49cdb00` (2026-10-16 04:50 UTC), whose only CI failure
was the test-only role-creation race (`pg_authid_rolname_index`, fixed by #1442, which merged
next); the deployed code was unchanged by the failing test. Any further exception is a
Supervisor decision recorded here with the commit and the reason, never dev1's own call.

**Data, ownership and deletion (owner, 2026-10-16).** Replaces "store and process them only to run
DMbot for you" in the terms. Decided after Supervisor advised against "DMbot owns all data as public
art": privacy law lets people withdraw consent and erase their personal data whatever a contract
says, and DMs would not pay for a service that claims their campaigns.
- **Users keep ownership; DMbot gets a broad licence.** People grant DMbot a worldwide, royalty-free
  licence to store, process and show what they and their table provide, to run DMbot and to improve it.
  Content a DM shares from a book stays the rights holder's (the "I have the right" step is unchanged).
- **Generated content belongs to the campaign** (#1381 decision 1): recaps, story memory, notes and
  anything else DMbot writes from a session stay with the campaign when a player leaves, and are
  deleted only when the campaign is deleted.
- **Who may delete a campaign:** the game master whose plan it uses (`owner_user_id`; for a free
  campaign, the DM who started it). Other game masters manage it but cannot delete it. Built (#1608, PR #1618):
  ⚙️ Settings → 🗑 Delete this campaign, the plan holder only (checked again under the campaign
  lock), not during a session, one warning and a typed name; its channels go as retention's do.
- **Severable personal data is deleted on request, best effort:** a person's own lines and what DMbot
  learned about them (Delete my transcripts, Stop recording me, account deletion). Generated content
  and copies other people already downloaded stay, and the pages say so.
- **Account data stays protected:** Discord ids, emails, payment and record-keeping data are never
  published or shared beyond the processors the privacy page names.
- **Improving DMbot:** DMbot uses transcripts and typed table chat to improve itself (the raw transcript
  exists for this, #933). It is disclosed on the privacy page, and a person **opts out on the website**
  (My Account), after which nothing they said or typed is used for improvement; running their table is
  unaffected. Built in #1609 (store and honour the choice) and #1610 (the switch and the wording). The
  choice applies from when it is made, at the source (the test recorder, session exports); test
  recordings kept earlier name people only by made-up voice codes, so they are not re-filtered, and
  **Stop saving my voice** remains the way to delete one's kept voice (decided 2026-10-16, PR #1621).
- **Raw audio:** DMbot does not store voice in the base product. The test library's voice saving stays
  a separate yes (🗂️ Menu → Stop saving my voice); keeping audio for troubleshooting is decided later.
- The terms and privacy changes are one WebDev PR (#1610), held for the owner's `approved: owner` label and
  recommended for a lawyer's read before it goes live; it merges only after the delete button and the
  opt-out exist.

**Retention.** Configurable auto-delete of transcripts per server (audio is never
stored), and a "Delete my past transcripts" action for each player. Deleting a person's
lines covers both versions, the fixes list, the mentions that point at those lines, and
that person in any alias's "who uses it" field. When someone withdraws, recording stops
at once and they're told: "🛑 Stopped. DMbot won't record you anymore in <server>. What
DMbot already wrote down stays, and anyone in this server can still read it." (When
"Delete my past transcripts" ships, that message must also say how to use it.)

*Delete my past transcripts, built (2026-10-16, #1532).* The stop message now ends "Want it gone? Press
🗂️ Menu, then 🗑 Delete my transcripts." (its buttons carry the Menu; the button is short so it fits a phone).
The action covers one server and the pressing person only, in one transaction (nothing deleted if it
fails); a running session's waiting lines of theirs are dropped under its save lock, and if they are still
recorded, new lines are written from then on. **Goes:** their lines in both versions (including their `[DM Sidebar]` / `[Table Q]` questions and
DMbot's replies, which carry their id), their fix suggestions in any state, the story-memory mentions of
those lines and their undo-log rows, the name counts kept per speaker, and the story claims that cite them
and were never confirmed; each session's line count and speaker list are recomputed; and they are taken
out of any alias's "who uses it" (the alias stays). **Stays:** other people's lines, the facts the DM
confirmed and the claims they came from, recaps already built, and the session rows. A mention names its
line by `transcript.models.line_ref(session, started_ms, user)`; anything that writes mentions uses that
form (nothing writes them yet).

*Keep transcripts for, built (2026-10-16, #1533; migration 0101).* ⚙️ Settings → 🗂️ Campaign management → 🗄️ Transcript retention (#2281): Forever
(the default) / 1 year / 90 days / 30 days, DMs only; a shorter choice shows one warning ("Sessions older
than 90 days will be deleted every night, with their downloads on the website. Recaps and what you
confirmed into story memory stay. Players can't get the deleted transcripts back.") and saves only on its
button. The daily retention run (03:00 UTC, catch-up as for the rest) then deletes, per campaign with a
limit and oldest first, every session that **ended** more than that long ago: its lines (both versions),
its fix suggestions and the mentions of those lines, one transaction per session (a failing session rolls
back alone and is tried again tomorrow; at most 200 a night per campaign). A session still running, or
saved without an end, is never deleted. The session row stays with its number, start and end
(`transcripts_deleted_at` marks it, `line_count` 0), so numbers never shift (the bot and the website door
count it); recaps and confirmed facts stay. The DM screen says "🗑 Deleted 3 old transcripts (kept 90
days)." once (the first night something goes; changing the choice lets it say so again); players are told
nothing. The setting is in campaign backups (a restore into a new campaign brings it; a restore into an existing
campaign leaves that campaign's own limit, so a restore never deletes anything). The plan job and this one
run side by side: one failing never stops the other. Not done here: marking the session "transcript deleted" in
the website's Recaps list (WebDev: `transcripts_deleted_at` is on the session row).

**Bots are never transcribed** (music bots etc.) — enforced in ears by an allowlist.

**Accounts: the DMbot ID, email sign-in, moving to a new Discord account, trial emails,
experience points and the trial extension (owner, 2026-10-14; Supervisor's details marked).**
- **DMbot ID.** Every account gets a DMbot ID: its Discord user ID written in **base 36**
  (digits and capital letters, `0-9A-Z`). It is the Stripe customer's `invoice_prefix`, so
  invoices read `<DMbot ID>-0001`. *Why base 36 and not base 56 (Supervisor):* Stripe's
  invoice prefix must be 3 to 12 **capital letters or digits**, and base 56 needs lower case.
  Base 36 fits today's Discord IDs (about 1.5×10^18) in 12 characters until about 2050
  (36^12 ≈ 4.7×10^18), and a test pins that today's largest IDs fit. **The ID is fixed when the
  account is made and kept for life**, stored, not worked out again: after a move to a new
  Discord account (below) the account keeps its DMbot ID and its invoice history. It is an
  account number, not a secret. Anyone who knows a Discord ID can work it out, so it is never
  enough on its own to see anything about the account.
- **Personal links (`uid`).** Links DMbot sends to one person (the trial-end message and the
  emails below) carry `?uid=<DMbot ID>&t=<token>`, where the token is a signed, expiring
  (30 days) code made by the web API. *Why the token (Supervisor):* the pricing and hello pages
  greet the person by Discord name and show their experience points, and a bare `uid` would let
  anyone show anyone's name and minutes by guessing IDs. With no token, or an expired or wrong
  one, the page shows its plain version and nothing personal.
- **Experience points:** the account's total DMbot minutes, all time, on every plan (the hours
  meter already counts them). Shown as "Experience points: <minutes>" on the account page, and on
  the pricing and hello pages when a valid personal link is used.
- **Signing in to the website:** with Discord (as before), **or by typing an email address**.
  If an account's email matches, a sign-in link is emailed (one use, 15 minutes); the page
  always says "If that email belongs to a DMbot account, we've sent a link", so it never tells
  anyone whether an address has an account. Wrong or repeated requests are rate-limited per
  address and per connection. No passwords, ever. *The account's email* is the one Stripe has
  for its payments (Checkout and the customer portal keep it up to date); an account that never
  paid has the email Discord gave the website, if it signed in there.
- **Changing the email DMbot uses** (Supervisor, 2026-10-22, #2349: the recap page names the
  address, so people need a way to fix it). The account page shows the address. A **payment
  email** changes only in Stripe (the customer portal; `customer.updated` brings it back). Any
  other email, or none, is changed on the account page: DMbot emails a link to the new address
  and nothing changes until it is opened. The link is `/account#email-confirm=<token>` (in the
  hash, so no server log holds it and a mail scanner opening it confirms nothing); the page
  sends it back by POST, and it works only while signed in to **the same account** that asked.
  One use, 15 minutes, like sign-in links; a new request replaces a waiting one, and a waiting
  one can be cancelled. An address another account already uses is refused (sign-in by email
  needs one address per account). Once changed, the **old** address gets a short note, so a
  stolen session can't quietly move email sign-in. API shape: the comment on #2349.
  *Two more rules (Supervisor, 2026-10-22, #2359 review):* an email the person chose **outlives
  later Discord sign-ins** (a Discord sign-in no longer overwrites it), and confirming deletes any
  unused sign-in links sent to the old address. "That address is already used by another account"
  is the one place DMbot says whether an address has an account: only to a signed-in person, at
  most 6 tries an hour, because two accounts sharing an address would lock both out of email
  sign-in.
- **Moving to a new Discord account ("migrate").** Only for an **active paid subscription**
  (not Try It, a trial extension, a grant or the free list).
  1. Get a move code: `/dmbot migrate` in Discord (from the old account), or My Account on the
     website (signed in with the old Discord account or by email link, so it works when the old
     Discord account is gone). The code is **emailed to the account's payment email**, never
     shown on screen. It is 8 letters and digits, works once, and lasts 30 minutes.
  2. On the new Discord account: `/dmbot migrate`, then **Import from another Discord account**,
     and type the code.
  3. Then, in one database change: the plan, its hours, the Stripe customer (its metadata
     updated to the new Discord ID), every campaign the old account owned, and its DM place in
     those campaigns (and its `played_by` and person entries there) move to the new Discord ID.
     The old account is left as a **moved** marker: who it moved to and when. Transcripts keep
     the names they were written with.
  - *History (owner, 2026-10-14):* the current Discord ID and the DMbot ID are separate columns; the DMbot ID
    never changes. Every move also adds a row to a permanent, append-only history (`account_id_history`: the
    old and new Discord IDs, when, and the payment email the code went to), kept for the life of the account,
    shown in the person's data export and deleted with the account.
  4. **Recording consent does not move.** The new account says yes for itself the first time
     it joins a table. Consent belongs to the Discord account whose voice is recorded.
  - *Limits (Supervisor):* one move per 30 days. A moved account can never start a new Try It
    (one trial per person). Codes are rate-limited like sign-in links. The confirmation names
    the plan and the campaigns that will move, and the old account is told by private message if
    it still exists.
  - **If the old Discord account shows up again** where DMbot is installed (owner's question):
    it is an ordinary Discord user with no plan and no campaigns. The first time it uses DMbot
    it is told once, privately: "Your DMbot account moved to another Discord account on <date>."
    It can't run the moved campaigns, take them on or restore them, because ownership is a
    Discord ID check and that ID no longer owns anything. To move back, it needs a new code from
    the payment email, like any move. Players who never moved are unaffected.
  - **Giving campaigns to a different person (Supervisor):** a move takes the whole account,
    *including the subscription and the card that pays for it*. A retired DM handing campaigns to
    a new DM should use **Hand over** (#614) for each campaign. The new DM then pays with their
    own plan. Help says so next to `/dmbot migrate`.
- **When neither Discord nor the email can be reached** (owner's question; Stripe's answer):
  Stripe has no way for a customer to stop a subscription without the customer portal, which
  needs the website sign-in or a link sent to the payment email. Such a person writes to us at
  the hello page or the contact email. The admin checks who they are (the card's brand and last
  four digits, the billing name and postal code, the invoice numbers), then cancels the
  subscription in the Stripe Dashboard. Their bank can also stop the payments, which Stripe
  sees as a failed payment or a dispute, and the plan ends by the usual unpaid rules.
- **Plan change: no $1.99 first month** (owner, 2026-10-14). Table is $8.99 from its first
  month; the once-only coupon and its checkout check are removed.
- **The last day of Try It:** a private message on Discord and, when DMbot has an email for the
  person, an email, both with this text: "Hi <Discord name>! Thanks for trying DMbot. We hope you
  love it. Free samples are nice, but we do have to pay other companies for the AI models
  DMbot uses. If you'd like to keep using DMbot, please look at the plans at
  https://getdmbot.com/pricing. If you're still unsure, please tell us how we can make DMbot
  better at https://getdmbot.com/hello." Both links are personal links (above), so the pages
  greet the person ("Hello, <Discord name>") and show their experience points. *Note:* the bot
  never sees a Discord user's email; DMbot has one only if the person signed in to the website
  or paid.
- **Trial extension** (owner, 2026-10-14): a person who **has never paid**, has used DMbot for
  **less than 4 hours in all**, and whose Try It (or last extension) **ended 3 days ago** gets
  **30 more days** of Try It, and an email that says so and why: "we want you to have the chance
  to really try DMbot at your table." It keeps every Try It limit: 8 hours of DMbot time in all
  (not 8 more), one campaign, no backups or downloads, no hand-overs, no moves. It repeats each
  time an extension ends while the total stays under 4 hours. It happens on its own: nothing to
  click. Only people DMbot has an email for get the email, but the extension applies to everyone
  who qualifies, and the bot says so privately at their next `/dmbot start`.
  *Details (Supervisor, 2026-10-14, #1240):* the 8 hours in all are counted in minutes and enforced
  by the database itself (480 minus the minutes used). The daily job looks back 40 days: at its
  first run it reaches people whose trial ended up to 40 days ago, who may still come back,
  and never emails people long gone. The last-day message goes again at the end of each
  extension. Experience points are the minutes on campaigns the account owns.
- **Sending email:** for now from the owner's `dmbot.admin@gmail.com` (SMTP with a Google app
  password, kept in the server's `.env`, never in the repository; dev1 adds it). Later from the
  owner's own email account. One small sending module in core with a daily cap (Gmail allows
  about 500 a day) and a log of what was sent to whom (never the email's text), so a person
  gets each email once. Sign-in links and move codes are account emails. The trial-end and
  extension emails are offers, so each has an unsubscribe link (honoured at once, kept in the
  database) and **a postal address** (US CAN-SPAM requires one in commercial email): **that
  address is the owner's call** (`needs: owner`; a PO box or a mail service is fine), and the
  offer emails don't go out until it is set. The privacy policy and terms get a line each on
  these emails, which is legal wording for the owner to sign off.
- **Email records are not kept for ever** (Supervisor, 2026-10-15, #1287, for the privacy page #1283):
  (1) a Try It's sent notes (the last-day and 30-more-days emails) are deleted 1 year after they
  were sent, because a Try It can't be taken twice (the DMbot ID marks it used) so the record
  has no job left; (2) deleting an account deletes that person's `sent_emails` rows in the same
  transaction; (3) their `email_unsubscribed` row stays (only the Discord id, the kind and the
  time), so a returning account is never emailed against its wish.
- **An owner gone from Discord** (Supervisor, 2026-10-14, #1185, built in #1214): when DMbot sees a
  campaign's owner isn't in the server (asked only at a start, on opening Settings and once a day;
  no new intent), the campaign becomes ownerless after 7 days, or at once if Discord says the
  account was deleted. Its DMs then get **Take it on**, and the 120 days apply as for a deleted
  DMbot account.
- **Pause from the website: no** (Supervisor, 2026-10-14, #435): pausing a campaign stays in
  Discord (⏸️ on ⚙️ Settings), where the bot holds the session lock and knows whether it is
  listening.
- **Build:** #1228 (DMbot ID, link tokens, experience points, no $1.99), #1229 (email, trial-end
  message, extension), #1230 (moves), all CloudDev; #1231 (website sign-in by email, My Account,
  personal pages, WebDev); #1232 (owner: postal address, Gmail app password).

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
- Story memory: models and the per-session cost cap for the AI roles, after measuring
  tokens per call and the "how it was said" accuracy (the rest was decided 2026-10-06,
  #227)

*Settings menus (#2281, 2026-10-21).* Settings opens from the DM Bot channel's **👤 DMbot Main Menu** (it was "My buttons"). The Settings card keeps the daily choices and has two buttons for the rest: **👁️ Who can see the DM screen** (the three choices) and **🗂️ Campaign management** (hand over, pause, transcript retention, game masters, delete; drawn only for someone who can use at least one). Each submenu has **↩️ Back to Settings**. Every press re-checks the presser, as every settings button does.

### Players don't know this one yet (#2321, 2026-10-22)
Any entry (person, group, place, thing) can be kept from the players until the DM says they
know it: the 🙈 switch on the kind step of Add a name, and 🙈 Hide from players / 👀 Players know
it now on the name card (DM only). It is the clue's `audience_limited` treatment without an
audience to track: `memory_entities.hidden_from_players`, read everywhere as
`Entity.unseen_by_players`. Players' lists, cards, answers, the NPC list and the website leave it out;
recaps (Discord and the website's recap name check alike) leave it out **unless the table said it in that
session's lines** (Supervisor, 2026-10-22, #2446); the DM and the DM screen see it, and a backup carries it. Only the
DM sets it. The relationship-level secret is separate and already exists (a secret connection).
A player's character, the party and DMbot's own player entries can't be hidden. **A hidden name is not a secret for DMbot's listeners** (Supervisor, 2026-10-22, replacing
"treated like a clue in the transcript"): the cleaner and the DM sidebar recognise and use it; only a 🤫
spelling or a clue only some know keeps the secret treatment.
A database trigger clears the flag whenever an entry becomes a player's character or the party, whichever path does it (an import, a change of kind, the plays answer, a merge).

### Recap output room and Try again (#2341, 2026-10-22)
The beat-sheet call's output room is sized for a full window of beats (`beats.room`) and doubles on the
retry, so an answer cut off at the first limit is never asked for again at the same one. A recap that
fails on our side (a cut-off answer, a check, the service) gives back the DM's Try again; one that fails
for no speakers, funds or stopped recording does not. The failing check's fixed name is in `last_error`
(`(check: length)`) and the log. dev1 starts a session over with
`python -m dmbot.devtools.recap_requeue GUILD CAMPAIGN SESSION_AT`.
