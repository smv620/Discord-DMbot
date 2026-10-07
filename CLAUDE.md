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
- `web/` — the customer website (Astro + TypeScript, static with one signed-in area) and
  nothing else; its API is Python in `core/` so plan rules and isolation live in one place.
- `docs/` — plan and design notes. `.claude/agents/` — reviewer team definitions.

## Commands
```bash
# ears (Node 22, npm 10; nvm reads .nvmrc, `corepack enable npm` pins npm to packageManager)
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
- Never push directly to `development`, `beta`, or `main`. The owner merges, except
  log-only PRs (see "Testing logs").
- Promotions (`development` → `beta`, `beta` → `main`) are PRs, opened only when the
  owner asks.
- One concern per PR. Link the GitHub issue it closes.
- Before opening a PR, run the reviewer agents in `.claude/agents/` (reviewer,
  ux-critic, perf-qa) on the diff and address their findings or explain why not.
- Update `docs/PLAN.md` when a decision changes scope or architecture.

## Claude sessions and who does what (owner decision, 2026-10-05, updated 2026-10-07)
DMbot runs on the cloud test server (VPS), not on the owner's PC. Several Claude
sessions work on this repo. **Each one has a name, and its name is its issue label:**
`session: dev1`, `session: dev2`, `session: dev3`, `session: clouddev`, `session: web`,
`session: WebDev` (and `session: pycharm` when the owner's PC is used). **Assignments come from the issue
list:** an open issue labelled with your name is yours; take them lowest number first
unless an issue says otherwise. Say which session you are in every issue and PR. More
developers can be added by adding a label.
- **dev1** (Claude Code on the deployment server, in the main checkout): **the only
  session that builds, deploys or restarts containers**, runs `docker compose`
  up/down/restart/build, runs migrations, changes `.env` (`scripts/set-key`,
  `scripts/update-env`) or restarts a service. It runs every test that needs the running
  server (live tests in Discord, timing, resource use) and maintains the testing logs
  (below).
- **dev2, dev3, …** (Claude Code on the same server): each works in **its own git
  worktree** (`git worktree add ../dmbot-dev2 -b <branch> origin/development`), never in
  dev1's checkout, and runs unit tests, lint and type checks there. **They may look at
  the running system but never change it:** `docker compose logs` and `ps`,
  `journalctl`, read-only database queries through the read-only role `dmbot_ro`,
  checking that an endpoint answers, reading firewall rules and resource use. Nothing
  read from the live database (players' data, secret names) goes into an issue, a PR or
  a log file. Their `.claude/settings.local.json` denies the deploy commands, so the
  rule is enforced, not promised.
- **clouddev** (Claude Code in the cloud, its own clone): builds queued issues that need
  no live server. Cloud limits: no server access at all, GitHub only through `gh api`
  (REST; GraphQL is blocked), no deleting branches, and CI is its test runner
  (discord.py, psycopg and pytest aren't installed locally).
- **WebDev** (Claude Code, its own clone or worktree): the customer website in `web/` and the
  web API in `core/src/dmbot/web/`. Same rules as the other coding sessions: PRs into
  `development`, never deploys the bot, never touches the live server; dev1 deploys the
  API container. It owns the site's words with the ux-critic, never the plan prices or
  rules: those are owner decisions recorded in docs/PLAN.md, and a change to them is a
  question on the issue, not a commit.
- **web** (the coordinating session, cloud): planning, design decisions, `docs/PLAN.md`
  and this file, issue hygiene, reviewing every PR before the owner merges, and the code
  that needs design judgement. Scope questions from any session go in the issue; web
  answers there.
- **Handover to a deploy:** when a session's PR is merged and needs to go live, it adds
  one line under "Ready to deploy" in `docs/testing-status.log` (what to deploy, what to
  check) or comments on the PR, then takes its next issue. dev1 deploys, checks, and
  records it in `docs/testing-history.log`. The log is the queue; nobody waits in chat.

Only one copy of DMbot may be logged in to Discord at a time: they share the bot token.
On the server that copy is the one in Docker Compose, run by dev1.

## Issue log: shared memory between Claude sessions
The sessions don't share memory, so **GitHub Issues are the shared log.**
- **Start of every task:** read open issues and recent `fix-log` issues, and anything
  touching the area you're about to change:
  `gh api "repos/smv620/Discord-DMbot/issues?state=all&per_page=30"`
- **Every bug you find gets an issue**, opened before or while you fix it. Labels:
  `bug` plus your session label (`session: dev1`, `session: web`, …). Use the template in
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

## Testing logs
Live-testing progress is kept in two files on `development`, so no session needs
terminal output or Discord screenshots copied to it.
- **`docs/testing-status.log`:** the immediate testing picture only: the next test, its
  checklist, blockers, and the latest result. Keep it short (about one screen) by moving
  finished items to the history and trimming them here.
- **`docs/testing-history.log`:** the complete record of every test run, append-only and
  oldest first. Fix mistakes with a new dated entry, never by rewriting.
- **Maintainer:** dev1 updates both after every live test and whenever
  testing plans change, from the server logs and what the owner pastes from Discord. The
  PyCharm session adds its offline test results the same way. Other sessions read them
  before planning test-related work.
- Same privacy rule as issues: no tokens, `.env` contents, or players' personal data.
- **Log-only PRs may be merged by Claude** once CI passes (owner decision, 2026-10-04).
  A PR counts as log-only when it changes nothing but `docs/testing-status.log` and/or
  `docs/testing-history.log`. Every other PR is still merged by the owner.

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
- **Transcripts are shared; the DM screen is not pushed to players.** Anyone in the
  Discord server may view and download a campaign's session transcripts (decided
  2026-10-05). Only people who agreed are ever recorded, and the consent request tells
  them the whole server can read the transcript. DM-screen content (rules alerts,
  house-rule prompts, NPC and plot notes) never goes into transcripts, and the bot never
  sends it to players. Players see the DM screen only if the campaign's DM-screen
  visibility allows it: **private** (DM only), **opt-in peek** (the default: hidden, with a
  "Peek behind the DM screen" button that warns about spoilers before granting access),
  or **open**. See docs/PLAN.md.
- **Backups are complete, and anyone in the server may download one of a paid campaign**
  (owner decision, 2026-10-06, narrowed 2026-10-07: Try It campaigns have no backups or
  downloads; loading a backup needs a subscriber with a free campaign slot, see #437): a campaign must never be lost if its DM disappears. A backup holds the
  whole campaign, unabridged, including secret names and DM notes; that is the one
  exception to keeping DM-screen content from players. The download message says it holds
  the DM's secrets and tells players not to open it. Whoever restores a backup becomes the
  restored campaign's DM; only a campaign's own DM may replace it with a backup.
- **Citations:** every rules alert includes its source and confidence.
- **Rules edition:** newest ruleset first, always — even in legacy adventures — for
  spells, rules, and monsters (currently 2024 PHB / 2025 MM). Use legacy content only
  when no newer version exists, and tag it `[Legacy 2014]` everywhere it appears.
  Precedence: house rules → homebrew → target ruleset → fallback ruleset (the DM picks
  target and fallback; defaults 2024 → 2014). Optional supplement rules are on by
  default where the target doesn't conflict. See docs/PLAN.md.
- **Intellectual property (IP):**
  - **Never in the repository** (it's public): no sourcebook, adventure or D&D Beyond
    text, and no players' or DMs' shared material.
  - **Open licences:** SRD 5.2 (CC-BY-4.0) and other open-licence content may be stored
    with attribution.
  - **Shared content, only after the DM confirms the right to use it:** when a DM shares
    material (an adventure through the Shared story switch, or a rulebook or supplement
    for the rules advisor) and confirms they have the right to use it (the warning says DMbot doesn't check; DMbot does no legal review),
    DMbot may copy chunks of that text into that campaign's storage and AI prompts to
    build the knowledge graph (its structures, nodes and edges) or the campaign's rules
    data, with short quotes as evidence and citations. Record who confirmed and when. It stays in that one campaign (never shared
    between campaigns or servers), and the DM can delete it; it goes when the campaign is
    deleted.
  - **Everything else:** without that confirmation, nothing is bulk-copied into the
    database or prompts; send only short, relevant excerpts per query (rules lookups
    without a shared rulebook, D&D Beyond).
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
