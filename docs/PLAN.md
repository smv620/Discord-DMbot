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

## Phases

| Phase | Deliverable | Notes |
|---|---|---|
| 0 | Scaffolding, CI, ears ↔ core audio pipeline | Prove live per-speaker capture in a real DAVE channel |
| 1 | **Listener**: `/table join`, consent by DM with buttons (#33–#35), live per-speaker transcript in a private `#dm-screen` channel, stored session transcripts that participants can download (#41) | No AI yet; useful on its own |
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

**Consent (decided 2026-10-04).** Consent is asked by **private message with buttons**,
the way other Discord bots handle opt-ins. No typing, and no slash command needed.
- When `/table join` starts a session, DMbot DMs everyone in the table voice channel
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
  `/table join`, is not repeated after a voice-service reconnect, and the DM is warned if
  it can't be posted.

**Transcripts vs. the DM screen (decided 2026-10-04).**
| Content | Who sees it |
|---|---|
| **Transcripts** (what was said at the table) | The DM **and every consenting participant** can view and download them (#41) |
| **DM screen** (rules alerts, house-rule prompts, NPC and plot notes) | The DM only, by default. Players *may* see `#dm-screen` if the DM shares it, but that's discouraged, like peeking behind the screen at a real table. The bot never sends DM-screen content to players. |

Sessions and their participants are stored, and each transcript can be downloaded as a
file by its participants and the DM: from a 📄 button in the consent DMs, at session end,
or with `/transcript`. Transcripts never contain DM-screen content.

**Retention.** Configurable auto-delete of audio and transcripts per server, and a
"Delete my past transcripts" action for each player.

**Bots are never transcribed** (music bots etc.) — enforced in ears by an allowlist.

**License (decided 2026-10-03).** Public repository under PolyForm Strict 1.0.0.

## Open decisions

- Cloud server provider and size (CPU vs GPU) — depends on Whisper model quality needed
- Final wording of the consent DM and join reminder (#33)
- Whether revoking consent also removes a person's past lines from stored transcripts (#34)
- Whether consenting members who missed a session can download its transcript (#41)
