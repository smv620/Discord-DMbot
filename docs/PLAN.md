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
| 5 | **NPC tracker** (remembers NPCs, relationships, factions between sessions), then **PlotBot** (DM-confirmed story events) | Read the campaign memory; use **confirmed** entities and relationships only. *Split (decided 2026-10-06, docs/STORY_MEMORY.md):* 5a claims, state facts and the first continuity warnings · 5b NPC tracker, who knows what, hierarchical reputations · 5c PlotBot: threads, promises, summaries, pre-session note · 5d the Shared story switch |
| 6 | **DM sidebar**: quick, very short AI answers for the DM, by voice memo or by asking out loud at the table; tagged `[DM Sidebar]` in the raw transcript only (owner, 2026-10-09) | No install needed; being built early, after the rules lookup |
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
(SRD)", up to five close names as buttons, nothing picked for the DM. Two decisions
(Supervisor, 2026-10-09): **only a campaign's DMs** look things up for now (the answer is
private, never in a channel or a transcript; players looking up rules is a later question),
and **precedence is shown, not decided**: a house rule of that campaign (and no other's) that
names the thing comes first as "🏠 House rule 12: …", then the book, and the DM decides what
applies. Not built: the AI, shared rulebooks.

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
decides: the card says what the free rules say. Not built: AI checks of what was ruled, house
rules proposed by voice, players' lookups.

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

**AI models by task (decided 2026-10-10, built #1006).** Every AI call names a *tier*, never
a model.
- **Which job uses which:** **FAST** (the small model, Haiku today) for simple jobs:
  transcript cleaning, the off-topic filter, Find names, rules checks and lookups, the audio
  check and the DM sidebar. **CAREFUL** (the middle model, Sonnet) for house-rule changes.
  **DEEP** (the strongest, Opus) for PlotBot (phase 5c; nothing uses it yet).
  `dmbot.ai.FEATURE_TIERS` lists every job's tier; a test checks no other file names a model.
  Model names change, so the tiers are the stable thing.
- **The settings:** `AI_MODEL_FAST`, `AI_MODEL_CAREFUL`, `AI_MODEL_DEEP`, each with a
  default; a malformed one (not a `claude-…` name) stops start-up naming the setting. The
  old `AI_MODEL` stands for FAST, with a line in the log, and is removed in a later update.
  Note that it now covers every quick job (before, it only chose the model for Find names;
  the filter and the sidebar were always on the small one).
- **If a model isn't available** (not found, or a `permission_error` for this key) the call
  goes to the next tier down (DEEP, CAREFUL, FAST). It is noted once in the log, naming the
  setting to check, and only when a lower tier then answered (so a bad key is not blamed on a
  model). The model is tried again after 15 minutes, so fixing the name or the access needs
  no restart. A 403 that isn't a `permission_error` (a blocked request) never counts.
  Running out of money never falls back (it messages the admins, #972).
- **One model for each job:** a job stays on one model, because Anthropic keeps cached
  prompts separately for each model; switching a job's model back and forth throws the
  cache away.
- **DEEP work runs in batches:** PlotBot and the after-session continuity pass run at a
  break, at the end of a session, or when the DM asks; never on every transcript line.
  The strongest model costs several times what the small one does.
- **Changing a tier's model:** a tier's model changes only after its checks pass on the new
  model (for the sidebar, `python -m dmbot.devtools.sidebar_check`). Moving a job to
  another tier is a change to `FEATURE_TIERS` and its own issue.
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
  **⚙️ Menu** button (#33, #34; the menu replaced a red 🛑 button, below). Rejoining in
  the same session doesn't send another (built 2026-10-05: once per person per session,
  not once per join).
- **The ⚙️ Menu, and a warning before stopping** (owner decision, 2026-10-08: the big red
  🛑 Stop recording me button on every private message was annoying). DMbot's private
  messages that offered 🛑 (the "you said yes" message, the per-session reminder, the
  `/consent give` answer) carry one grey **⚙️ Menu** button instead. The menu is about that
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
    the "you said yes" message, ⚙️ Menu swaps that message's buttons for the menu, Stop
    puts the warning at the top of that message's text, Yes leaves it reading "🛑
    Stopped…" with the ✅ consent button, and Keep or Close restore it exactly. So a reminder never
    keeps saying "recording you" after a stop. The warning is in the text, never an embed,
    because Discord hides embeds for people who turn previews off;
  - ⚙️ Menu and `/consent revoke` answer from the in-memory consent first, never waiting
    on the database before Discord's 3-second limit, so stopping can't fail on a slow
    database;
  - a 🛑 button on a message sent before this change shows the warning too;
  - **No thanks** on the first request stays one tap (nothing is recorded yet).
  Why: the owner wants a calmer message, and people who stop should know what it costs
  the campaign. Stopping takes three taps (Menu, Stop, Yes) where saying yes takes one;
  the owner's legal review found that acceptable (2026-10-08). The warning still stays
  short and one-time, and the menu button is on every reminder.
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
**⚙️ Settings**, on the DM screen's help card (there between sessions too) and first on
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

**Channel structure (decided 2026-10-04, #85).** Every channel DMbot creates starts with
`dmb-`, so its channels group together in the sidebar and are clearly bot-managed.

*Names.* Discord turns every text channel name into lowercase with dashes for spaces,
so the docs always show names the way Discord does. For the campaign
**Rime of the Frostmaiden**:

| Channel | Name in Discord | Built in |
|---|---|---|
| DM screen | `dmb-dm-screen-rime-of-the-frostmaiden` | Phase 1.5 (now) |
| Live transcript (cleaned lines, view only) | `dmb-transcript-rmfthfrstmdn` | Phase 1 (#124, built 2026-10-06; misheard names fixed since 2026-10-07, #127) |
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
  **The audio warning checks the transcript before it warns (owner decision 2026-10-08,
  #671, after a TV in the room set it off; #697 stage 1, #699 stage 2):** pieces shorter
  than the transcription minimum (by length as ears measured them, so a short answer
  that breaks up still counts) never count; among the rest, under 95% received and at
  least 2 s lost in a rolling 60 s window per speaker is the *trigger*: it starts a
  check of that speaker's lines in the window (the engine's per-word confidence first,
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
  time is the whole session). An AI recap of what happened is a later phase.

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
- Still to come: "Delete my past transcripts", retention, cleaned and both downloads
  (Phase 2b), and `{entity}` labels for the DM's lines (narrating, which NPC; #53).

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
  Later, once earlier lines are re-fixed after a correction, the message will add "If
  the DM fixes names later, download again for the updated version." (not yet: a
  correction applies from the next line only).
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
    more [Show all]": what it is, last heard, **Also called**, **🤫 Secret**,
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
  removed with their lines (Retention). Not yet in campaign backups. The fixed tiers
  are prepared off the event loop, once per change to the names. Until the DM can add
  connections (step 2), the tip-of-the-tongue tier stays empty.
- **Step 2, first part built (2026-10-06, `dmbot.ui.name_card`, `dmbot.memory.search`):**
  the overview (counts by kind, the waiting check, "Heard last session", "Added lately",
  an **Open a name…** menu; buttons 🔍 Find a name, ➕ Add a name, 📝 Check new names,
  🧑 Add a player's character), **🔍 Find a name** (the form and `/dmbot names find:`
  with type-ahead), and the **name card** with ✏️ Fix spelling, Add another name (with
  a secret-name field for the campaign's DMs only), Change what it is, and Remove
  (asks first; Undo). Still to come in step 2: Edit other names, Same as…, 🧭 Connect
  to…, and "Show all" on long sections. The type-ahead answers from the copy the
  session already keeps (no expiry for search-only copies yet). Decided while building:
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
  name's side or the other way round, then the other name; saved as confirmed, and a
  **Remove a connection…** menu takes one back), and **Show all** when a section is
  cut. Decided while building: a card groups connections by how they read from that
  name's side ("is a member of **Frostwolf tribe**"; on the tribe's card "has as
  members **Ulfgar**"; ally, enemy and family read the same both ways); connections the
  DM adds are not secret for now (a 🤫 secret connection comes later); a connection that
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
- **Learning is immediate:** a confirmed correction becomes a `misheard` alias and
  reaches the Cleaner within about a second. Earlier lines of the same session with the
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
- **Next:** Undo notes for medium fixes (proposed names), re-checking earlier lines
  after a correction, then off-topic hiding (#52).

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
    Earlier lines stay as heard." Never if it would put a secret name in the line (the
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
- **Later:** unanswered "Did they mean…?" questions (they expire quietly at the
  session's end, the line staying as heard) go to the after-session report once it
  exists.

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
  right before a window is sent. Lines of two words or fewer are never sent. Not yet held
  back: the name fixer's word list (lower-case words from every line). The log line
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
- **Plots:** story threads (hinted, open, active, stalled, resolved, abandoned),
  promises with due dates, clues, and cause and effect; a pre-session note of what's
  open and what could clash tonight.
- **Shared story switch** per campaign: **Off** (default; never suggests story),
  **Watch the story** (warns when the table breaks something the adventure still needs),
  **Guide me** (says what the story has next, with the section). For any written
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
- The DM can correct the clock with buttons ([+1 hour] [It's dawn] [Set time…]).

*Built, part 1: the clock and the DM's buttons (2026-10-10, #965):* no AI, and no guessing time
from narration. `game_clocks` (migration 0041): one row per campaign, in its own server scope,
deleted with the campaign and carried in backups (`ClockSection`, without message ids); none
until a DM sets it. **Game time** is minutes since the start of Day 1; dawn is 06:00, noon 12:00,
dusk 18:00 (`dmbot.timebot.clock`, pure). A DM presses ⚙️ Settings, then **Game clock**, gives a
day and an hour, and DMbot posts and pins one message, "🕰️ Day 4, afternoon (14:30)", edited in
place, with **+10 min**, **+1 hour**, **Short rest** (+1 h), **Long rest** (+8 h), **Skip to dawn**
and **Set time…**. Only the campaign's DMs can press them: checked on every press, in the same
database transaction as the change; the buttons survive a restart. **Said at the table:** a
DM's clear "we take a short rest" / "you take a long rest" (a strict phrase list: a question, a
wish, an "if", a "can't" or a long ramble never counts; the same rest twice in five minutes is
one) moves a clock the DM has set, with a one-line note and **Undo** (a rest from a button gets its note and Undo for the DM who
pressed it alone; it only undoes while the clock is still where the rest left it); a player's line never does. **Speaks up only for** dawn,
noon and dusk as a button or rest passes them (not when the DM sets the time), and **24 hours
without a long rest**, once per stretch, with its source ("Check: 24 hours since the last long rest… Optional rule, Xanathar's… Your call."); on by default, and off if the DM turns the "Going without a long rest" optional rule
off. The first time the clock is set the party counts as rested then. Not built yet: split-party
clocks, reading time from narration, periodic notes.

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
the transaction. Effects live on the game clock, so a restart keeps them. Not built: split-party
clocks, reading time from narration, AI.

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
  description"). Rules answers still carry their source and how sure DMbot is, in a few
  words.
- **Only on topic:** the campaign, the game's rules and content, DMbot itself, and Discord
  mechanics. Anything else gets one short line saying it can't help with that here.
- **Where answers go:** privately, in the DM's chat with DMbot, never in a public
  channel.
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
  - **Cost limits:** nothing is downloaded, written down or asked while the answer engine (#934) is missing. A voice message may be up to 30 seconds. A spoken question is one a minute per table; questions in the DM chat are
    six a minute per DM (each can spend speech-to-text and AI money). A voice message's speech
    counts in the end-of-session "sent" line for outside engines, not in the hours meter.
  - **Switch:** the ways in answer only when `DMBOT_SIDEBAR=1` (default 0, and it needs
    `ANTHROPIC_API_KEY`); off, a DM gets "Quick answers aren't switched on yet." and the table
    trigger stays quiet. Turn it on after the sidebar's test answers have been read (#954). One
    question at a time per campaign.
  - **Not built yet:** a line the Cleaner tags as in character is not told apart from the DM
    speaking (the `in_character` flag exists, nothing sets it); only the "hold on" lead-in
    guards against it.
  - **Limits:** DMs reach only the first shard's process, so a table on another shard's process
    is not found (single process today).

*Built, part 1: the answer engine (#934, CloudDev, 2026-10-09):* `dmbot.sidebar` (no Discord, no
voice). `bot.sidebar_answers.answer(campaign, question, asker_id=..., scene=...)` returns an `Answer`:
`text` (short, with its source and "sure"), `in_game`, `refused`, `model`, `prompt_version`,
`sources` (everything the AI was given, in short words: "SRD 5.2.1 p. 131", "house rule 3",
"campaign names", "the scene", "DMbot help"), `parts` (a long answer as messages) and `seconds`.
`asker_id` has no default: it decides the wording of a plan refusal.
- **Brevity is enforced in code.** At most 2 sentences and 200 characters (the source and "sure"
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
  Only matching entries are sent, each cut to 1,200 characters. Homebrew is not stored yet, so
  none is sent; content a DM shared counts only with its right-to-use confirmation and none is
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
check your book)" and is never "sure"; the prompt asks for the same wording for general D&D
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
and is never "sure" ("a spell attack can crit on a 20" is not house rule 3).
(2) **Older edition always tagged and named:** when the AI was given a `[Legacy 2014]` entry, the source names
both entries by their own names and pages ("Goblin Warrior SRD 5.2.1 p. 290; Goblin SRD 5.1 p. 315
[Legacy 2014]"). Brevity is unchanged; tags and sources are added after the cut.

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
| Table | 18 ("about 4 hours a week") | 1 | $8.99 (first month $1.99 after Try It) |
| Two Tables | 43 ("about 10 hours a week") | 2 | $17.99 |
| Guild | 87 ("about 20 hours a week") | 5 | $34.99 |
| Pro | 217 ("about 50 hours a week"); needs the bigger server | 20 | coming soon |
| Extra hours | +10 this month | — | $4.99 |

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
existing `CampaignStore.delete`, then the owner is told once; the log has counts only. The job
runs per server (the bot's own servers), reading across servers only the owner's plan, through
`usage.retention_standing`'s owner-scoped door; a plan that can't be read means that campaign is
left alone. **A campaign is only deleted after its last warning:** one already past its date when
first seen (the first run after deploy, a long outage) gets a warning and is deleted three days
later, and playing a session, or restoring a backup (which starts the clock from now), cancels
it; the job looks again right before each delete. **Safety:** behind `DMBOT_ENFORCE_PLANS` (off: no warnings, no deletion, one dry-run
log line with counts); a run that would delete more than 10% of all campaigns (and more than
3, so a small deployment isn't stuck on one old campaign) stops and logs an error instead, before any warning goes out; at most 300 warnings go out per run (the rest wait a day).

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
immediate. **Deleting an account (decided 2026-10-08, #435, #552):** the account and the
campaigns it owns go at once (that is what delete means); the paid subscription is
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
offers, must only be able to insert unsent, unclaimed offers from the signed-in owner.
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
with one signed-in area; Cloudflare Pages; sign-in with Discord only (scopes `identify
email guilds`); payments through a merchant-of-record hosted checkout, **Lemon Squeezy** (owner's
choice, 2026-10-09, #498) with its customer portal for plan changes; the API is FastAPI in
`core/src/dmbot/web/`, its own container, the only writer of the `entitlements` table via
the provider's webhook. No D&D or Wizards trademarks or art: "for 5e-compatible tabletop
games". Terms, privacy and refund pages before launch: **signed off by the owner on
2026-10-09, which is their effective date** (#498). Also approved for go-live that day:
rate limits at the proxy, the separate `dmbot_web` database role, and Cloudflare in front
of the API. The Discord app stays private until the rest of #498 is done and the owner
says go. Settings stay in Discord for now;
the site is account, plan, campaigns, invite and marketing.
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
- Story memory: models and the per-session cost cap for the AI roles, after measuring
  tokens per call and the "how it was said" accuracy (the rest was decided 2026-10-06,
  #227)
