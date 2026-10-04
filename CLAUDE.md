# CLAUDE.md — DMbot team rulebook

Read `docs/PLAN.md` first. It is the source of truth for scope, architecture, and phase order.

## The product in one line
A Discord bot that listens to a D&D table's voice channel and privately advises the
Dungeon Master. **The bot advises; the DM decides.**

## Repository layout
- `ears/` — Node + TypeScript voice service. Captures per-speaker audio (DAVE E2EE) and
  streams PCM to core. No game logic here, ever.
- `core/` — Python service. Slash commands, consent, transcription, AI analysis, storage,
  integrations. The owner develops in PyCharm Community, so keep core idiomatic Python.
- `docs/` — plan and design notes. `.claude/agents/` — reviewer team definitions.

## Commands
```bash
# ears
cd ears && npm ci && npm run typecheck && npm test && npm run build

# core (Python 3.12+)
cd core && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```
CI runs all of the above on every pull request. Never merge red CI.

## Workflow
- **Branches:**
  - `development` (default): the lead branch for work in progress. Feature and fix
    branches start here, and pull requests go back into it.
  - `beta`: code ready for testing. `development` is promoted into it when the owner
    wants to test, for example a live session on the test server.
  - `main`: only code that has passed beta testing and looks ready to deploy. The
    cloud server deploys from here.
- Never push directly to `development`, `beta`, or `main`. The owner merges.
- Promotions (`development` → `beta`, `beta` → `main`) are PRs, opened only when the
  owner asks.
- One concern per PR. Link the GitHub issue it closes.
- Before opening a PR, run the reviewer agents in `.claude/agents/` (reviewer,
  ux-critic, perf-qa) on the diff and address their findings or explain why not.
- Update `docs/PLAN.md` when a decision changes scope or architecture.

## Hard rules
- **Simple enough for a child:** user-facing text uses plain words, never technical terms
  ("remembers your NPCs between sessions", not "knowledge graph"). Prefer buttons over
  commands. Make it obvious the bot never invents story and never decides. The ux-critic
  agent checks this on every PR.
- **Campaign and server isolation:** every query, cache, file, and AI prompt is scoped to
  one campaign. No data or settings ever cross between campaigns, and never between
  Discord servers.
- **API keys:** customers' keys are entered through private forms, stored encrypted, never
  logged, and never shown back in full.
- **Secrets:** never commit tokens or keys. Config comes from environment variables
  (`.env` locally, see `.env.example`). Never log tokens.
- **Consent:** never capture, decode, store, or transcribe audio from a user who has
  not opted in. Bots are never captured. This is enforced in ears (allowlist) and
  re-checked in core, including after every async step. Consent is given with a DM
  button (slash command as fallback), carries over per server, and every join triggers
  a reminder with the consent date and a stop button. See docs/PLAN.md.
- **One voice channel:** the bot listens only to the configured table channel.
- **DM authority:** the bot never posts rulings to players or public channels. Advice
  goes only to `#dm-screen` / the DM. PlotBot and NPCBot record only DM-confirmed facts.
- **Transcripts are shared; the DM screen is not.** Every consenting participant may
  view and download session transcripts. DM-screen content (rules alerts, house-rule
  prompts, NPC and plot notes) never goes into transcripts or to players.
- **Citations:** every rules alert includes its source and confidence.
- **Rules edition:** newest ruleset first, always — even in legacy adventures — for
  spells, rules, and monsters (currently 2024 PHB / 2025 MM). Use legacy content only
  when no newer version exists, and tag it `[Legacy 2014]` everywhere it appears.
  Precedence: house rules → homebrew → target ruleset → fallback ruleset (the DM picks
  target and fallback; defaults 2024 → 2014). Optional supplement rules are on by
  default where the target doesn't conflict. See docs/PLAN.md.
- **Copyrighted content:** SRD 5.2 (CC-BY-4.0) may be stored with attribution. Do not
  bulk-copy D&D Beyond or sourcebook text into the repo, database, or prompts; send
  only short, relevant excerpts per query.
- **D&D Beyond:** no server-side storage of the user's D&D Beyond password or session
  cookies. Use public character links; campaign access comes later via a browser
  extension running in the DM's own session.
- **Least privilege:** request the narrowest Discord intents and OAuth scopes
  (Google: `drive.file`). Every access must be visible and revocable via slash command.

## Code standards
- **TypeScript:** `strict` mode, ES modules, no `any` without a comment explaining why.
- **Python:** type hints everywhere (mypy strict), Ruff for lint + format, async
  throughout (discord.py is asyncio). Small modules, one service per package.
- Tests for every behaviour change. Pure logic (consent, protocol, buffering,
  rule matching) must be unit-testable without Discord or network access.
- User-facing text (slash command descriptions, alerts, errors) is short, plain, and
  tells the user what to do next.

## ears ↔ core protocol
Defined in `ears/src/protocol.ts` and `core/src/dmbot/ears/protocol.py`. Keep the two
in sync; a change to one requires the same change and tests in the other.
