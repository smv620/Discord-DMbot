# DMbot — Project Plan

DMbot is a Discord bot that helps a D&D **Dungeon Master** run sessions. It listens to the
table's voice channel, transcribes each speaker, and privately advises the DM on rules
(published and homebrew). It never makes rulings: **the bot advises, the DM decides.**

## Guiding principles

1. **The DM is the authority.** The bot proposes; the DM confirms, overrides, or ignores.
   Overrides can become house rules, and house rules beat book rules.
2. **Consent first.** Nobody is recorded or transcribed unless they have opted in.
3. **One table channel.** The bot listens to exactly one voice channel per server.
   Anything said in another channel is never heard. Players step out of the table
   channel for private asides.
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
- **core** (Python): everything else — slash commands, consent records, transcription,
  AI analysis, storage, integrations. Developed in PyCharm.
- Both use the same Discord bot token. ears requests only the voice-state intent.

## Phases

| Phase | Deliverable | Notes |
|---|---|---|
| 0 | Scaffolding, CI, ears ↔ core audio pipeline | Prove live per-speaker capture in a real DAVE channel |
| 1 | **Listener**: `/table join`, `/consent`, live per-speaker transcript in a private `#dm-screen` channel, post-session transcript export | No AI yet; useful on its own |
| 2 | **Rules advisor**: SRD 5.2 + homebrew links, alerts with ✅ Agree / 🙈 Ignore / ⚖️ Override buttons, verbosity levels, house-rules database | Tiered pipeline: local trigger filter → fast model triage → stronger model for real rulings |
| 3 | **Google Drive**: OAuth (`drive.file` scope), house-rules doc mirror, `/access status · test · revoke` | Small web callback page for sign-in |
| 4 | **Character data** from public D&D Beyond character links | Unofficial endpoints; handle breakage gracefully |
| 5 | **PlotBot**: candidate events from transcript, DM confirms; never invents story | |
| 6 | **NPCBot**: knowledge graph (nodes + edges with properties) of NPCs, party, factions; DM confirms; tracks player vs DM knowledge | Plain DB tables first |
| later | D&D Beyond companion browser extension (AboveVTT-style) for campaign and owned content | Runs in the DM's own browser session; no server-side credentials |

## Feature notes

**Delivery to the DM.** Discord has no pop-ups. Alerts go to a private `#dm-screen`
text channel (only the DM can see it) and optionally to DMs.

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

Precedence, highest first: **house rules → homebrew → newest ruleset → legacy rules
`[Legacy 2014]`**.

Lookups must match renamed content (e.g. 2024 dropped many creator names from spell
titles), so the rules index keys each entry by a normalized name plus known aliases,
and a legacy entry is used only when no newer entry matches any alias. If a future
edition supersedes 2024, it becomes "newest" and 2024 content gets its own legacy tag.

**House rules.** The database is the source of truth; the Google Doc is a readable
mirror. Each rule records: the rule, the book rule it supersedes, and the scenario
that created it (session, date, what happened).

**Transcription (decided 2026-10-03).** Per-speaker audio means no diarization is
needed. Name hints (players now; characters, NPCs, places later) are fed to the
transcriber. Default engine is **local Whisper** (faster-whisper). Every engine sits behind
one `Transcriber` interface and is chosen by `TRANSCRIBER=` in config, so switching to a
**cloud pay-as-you-go** API (any OpenAI-compatible endpoint) is a settings change, not a
code change — for DMs without a GPU.

**Hosting (decided 2026-10-03).** A cloud server runs both ears and core. Because local
Whisper runs on that server, its size decides transcription quality and speed: a CPU-only
server suits the `base`/`small` models; larger models need a GPU server, or switch to
`TRANSCRIBER=cloud`. Deployment packaging (Docker) is a follow-up task.

**Consent & retention.** `/consent` opt-in per player, an announcement when listening
starts, configurable auto-delete of audio and transcripts, and player data removal.

**Bots are never transcribed** (music bots etc.) — enforced in ears by an allowlist.

**License (decided 2026-10-03).** Public repository under PolyForm Strict 1.0.0.

## Open decisions

- Cloud server provider and size (CPU vs GPU) — depends on Whisper model quality needed
