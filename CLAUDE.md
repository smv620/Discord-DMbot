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
- Work on a branch, open a PR, never push to `main`. The owner merges.
- One concern per PR. Link the GitHub issue it closes.
- Before opening a PR, run the reviewer agents in `.claude/agents/` (reviewer,
  ux-critic, perf-qa) on the diff and address their findings or explain why not.
- Update `docs/PLAN.md` when a decision changes scope or architecture.

## Issue log: shared memory between Claude sessions
Several Claude sessions work on this repo (the cloud/web session and the Claude Code
session in PyCharm). They don't share memory, so **GitHub Issues are the shared log.**
- **Start of every task:** read open issues and recent `fix-log` issues, and anything
  touching the area you're about to change:
  `gh api "repos/smv620/Discord-DMbot/issues?state=all&per_page=30"`
- **Every bug you find gets an issue**, opened before or while you fix it. Labels:
  `bug` plus `session: web` or `session: pycharm`. Use the template in
  `.github/ISSUE_TEMPLATE/bug.md`: Background (what you were doing), Symptom (exact
  error), Root cause, Fix, Watch for.
- **Bugs found and fixed within the same piece of work** still get an issue: label it
  `fix-log` as well, and close it with a link to the PR.
- **Reviewer-agent findings you fix** are logged too: one issue per significant finding,
  and minor ones grouped into one issue.
- **Fix PRs say `Fixes #N`** so the issue closes when the PR merges.
- **The repo is public:** never put tokens, `.env` contents, or players' personal data
  in issues.
- In cloud sessions `gh issue …` and `gh pr …` may fail (GraphQL is blocked). Use the
  REST API through `gh api repos/smv620/Discord-DMbot/...` instead.

## Hard rules
- **Secrets:** never commit tokens or keys. Config comes from environment variables
  (`.env` locally, see `.env.example`). Never log tokens.
- **Consent:** never capture, decode, store, or transcribe audio from a user who has
  not opted in. Bots are never captured. This is enforced in ears (allowlist) and
  re-checked in core.
- **One voice channel:** the bot listens only to the configured table channel.
- **DM authority:** the bot never posts rulings to players or public channels. Advice
  goes only to `#dm-screen` / the DM. PlotBot and NPCBot record only DM-confirmed facts.
- **Citations:** every rules alert includes its source and confidence.
- **Rules edition:** newest ruleset first, always — even in legacy adventures — for
  spells, rules, and monsters (currently 2024 PHB / 2025 MM). Use legacy content only
  when no newer version exists, and tag it `[Legacy 2014]` everywhere it appears.
  Precedence: house rules → homebrew → newest ruleset → legacy. See docs/PLAN.md.
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
