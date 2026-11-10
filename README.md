# Discord-DMbot

A Discord bot that helps the Dungeon Master run D&D sessions. It listens to the table's
voice channel, writes down what each person who agreed says, remembers the campaign's story,
writes session recaps, and privately advises the DM on rules, published and homebrew.
**The bot advises; the DM decides.**

See [`docs/PLAN.md`](docs/PLAN.md) for the full plan and roadmap.

## What it does today

DMbot is in testing on the owner's own server, with a public website at
[getdmbot.com](https://getdmbot.com). What is built:

- **Listening, only with consent.** DMbot joins the table's voice channel and writes down
  what each person who pressed **I'm 16 or older, record me** says. Bots and anyone who
  hasn't agreed are never recorded. Anyone can stop at any time (🗂️ **Menu**, then
  **Stop recording me**).
- **Transcripts** of every session, to read and download, and imports of older sessions.
- **Recaps** of each session, checked against what was said, with an optional voice-over.
  Nothing goes to the players until the DM approves it.
- **Story memory** (the Story Graph on the website): the people, places, groups and things
  of the campaign, how they connect, and who in the story knows what. It's built from
  sessions and from lore the DM uploads. **DMbot suggests; the DM confirms.**
- **Rules help:** looking up spells, conditions, creatures and rules in the free rules
  (SRD 5.2.1, with 2014 as the fallback), house rules and homebrew, Xanathar's and
  Tasha's optional rules, and short private tips for the DM with their source.
- **The table:** a game clock with split-party groups, fight reminders, and "Hey DMbot"
  questions, answered privately for the DM and in the table's chat for players.
- **The website:** the signed-in area holds the campaign's settings, names, Story Graph,
  questions waiting for the DM, transcripts, recaps, uploads and backups. Most features
  are in both Discord and the website ([`docs/discord-web-parity.md`](docs/discord-web-parity.md)).
- **Backups:** a complete copy of a campaign to download and restore.

Some features are built but switched off until they've been tested live. The current
picture is in [`docs/testing-status.log`](docs/testing-status.log) and
[`docs/discord-web-parity.md`](docs/discord-web-parity.md).

## How it's built

| Part | Language | Job |
|---|---|---|
| `ears/` | Node + TypeScript | Joins voice and captures each consenting speaker (Discord's DAVE encryption), then streams the audio to core. No game logic. |
| `core/` | Python | The bot: slash commands, consent, transcription, AI, story memory, recaps, rules and storage. Also the website's API (`core/src/dmbot/web/`, run as the `web-api` service). |
| `web/` | Astro + TypeScript | The customer website: static pages plus one signed-in area. |
| Postgres | | Every campaign's data. Each Discord server's rows are locked to that server by row-level security. |

On the server, Docker Compose runs `postgres`, `core`, `ears`, `web-api` and `cloudflared`
(the tunnel that carries the website's API). The website is hosted on Cloudflare Pages.
Ears and core talk over a local, password-protected link
(`ears/src/protocol.ts` ↔ `core/src/dmbot/ears/protocol.py`).

## Using it in Discord

Most things are buttons. The DM runs `/dmbot start`, and DMbot offers the rest from there.

| Command | What it does |
|---|---|
| `/dmbot start` · `stop` | Pick the campaign and voice channel, then start or stop listening. |
| `/dmbot help` | What DMbot does and doesn't do, with a **Status** button. |
| `/dmbot settings` | The campaign's settings (its DM). |
| `/dmbot names` | Teach DMbot the campaign's names, so it spells them right. |
| `/dmbot recap` · `recap-upload` | Make a session's recap (or make it again), or add one someone at the table wrote. |
| `/dmbot transcript` · `fix` · `delete-my-lines` | Download a session's transcript, fix a line DMbot heard wrong, or delete what DMbot wrote down from you. |
| `/dmbot import` | Bring in older sessions, on the website. |
| `/dmbot rule` · `houserules` · `homebrew` · `optionalrules` | Rules: look one up, the campaign's house rules, its homebrew, and the optional rules. |
| `/dmbot backup` · `restore` | Download a copy of a campaign, or bring one back. |
| `/dmbot migrate` | Move your plan and campaigns to a new Discord account. |
| `/consent give` · `revoke` | Agree to be recorded, or stop, if the private message didn't reach you. |

DMbot posts in the campaign's **DM screen** (private notes and tips for the DM) and its
**DM Bot channel** (counts and progress the campaign's people may see). Players' questions
are answered in the table's chat. Who can see the DM screen is the DM's choice: only the
DM, players can peek after a spoiler warning (the default), or everyone.

## Running it

- **On a server:** [`docs/DEPLOY.md`](docs/DEPLOY.md). One `docker compose up -d --build`
  starts every part. On the test server, only the dev1 session deploys (see
  [`CLAUDE.md`](CLAUDE.md)).
- **API keys** (AI, speech-to-text, voice-over): [`docs/how_to_setup_api_keys.md`](docs/how_to_setup_api_keys.md).
  Copy `.env.example` to `.env`. Pulling updates never changes your `.env`, and core's log
  names any new settings at start-up.
- **The database:** Postgres 16 or newer, UTF8. DMbot must connect as an ordinary user, never
  a superuser, so row-level security applies; it refuses to start otherwise. The website's
  API connects as its own user, `dmbot_web`. Compose creates both
  (`DMBOT_DB_PASSWORD`, `DMBOT_WEB_DB_PASSWORD`).
- **Speech-to-text:** `TRANSCRIBER` in `.env` picks `whisper-local`, `deepgram`, `cloud` or
  `none`. With a speech company, players' voices go to it, and
  the consent request says so.
- **A live test with people at a table:** [`docs/LIVE_TEST.md`](docs/LIVE_TEST.md) and
  [`docs/next-test-step.md`](docs/next-test-step.md).

## Developing

Read [`CLAUDE.md`](CLAUDE.md) first (the team's rules), then [`docs/PLAN.md`](docs/PLAN.md)
(the plan and every decision, with dates).

```bash
# ears (Node 22)
cd ears && npm ci && npm run typecheck && npm test && npm run build
# core (Python 3.12+)
cd core && python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
# web
cd web && npm ci && npm run typecheck && npm test
```

CI runs all of these on every pull request. Core's database tests run only where
Postgres is (CI has it).

**Where to read more:**

| Document | What's in it |
|---|---|
| [`docs/how-it-works.md`](docs/how-it-works.md) | A plain-words tour of DMbot's helpers. |
| [`docs/PLAN.md`](docs/PLAN.md) | Scope, architecture, phases and every decision. |
| [`docs/STORY_MEMORY.md`](docs/STORY_MEMORY.md), [`docs/STORY_GRAPH_DESIGN.md`](docs/STORY_GRAPH_DESIGN.md) | How story memory works. |
| [`docs/RECAP_STANDARD.md`](docs/RECAP_STANDARD.md) | What a good recap is, and how recaps are scored. |
| [`docs/WIZARD_DESIGN.md`](docs/WIZARD_DESIGN.md) | The website's start-up wizard. |
| [`docs/discord-web-parity.md`](docs/discord-web-parity.md) | What is in Discord, what is on the website. |
| [`docs/GO_LIVE.md`](docs/GO_LIVE.md) | What each switched-off feature needs before it goes on. |
| [`docs/costs.md`](docs/costs.md) | What the paid services cost. |
| [`docs/testing-status.log`](docs/testing-status.log), [`docs/testing-history.log`](docs/testing-history.log) | What is live on the test server, and every test so far. |

## License

Licensed under the [PolyForm Strict License 1.0.0](LICENSE). You may use DMbot for
noncommercial purposes. Changing it, building on it, redistributing it, or any
commercial use requires written permission from the owner — contact
[@smv620](https://github.com/smv620).

Rules content from the System Reference Document 5.2.1 is used under
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) and attributed where it appears
(see [`core/src/dmbot/rules/data/srd52/ATTRIBUTION.md`](core/src/dmbot/rules/data/srd52/ATTRIBUTION.md)
for what was changed):

> This work includes material from the System Reference Document 5.2.1 (“SRD 5.2.1”) by
> Wizards of the Coast LLC, available at <https://www.dndbeyond.com/srd>. The SRD 5.2.1 is
> licensed under the Creative Commons Attribution 4.0 International License, available at
> <https://creativecommons.org/licenses/by/4.0/legalcode>.

The 2014 rules, kept only as the legacy fallback, come from the System Reference Document 5.1
under the same licence (see
[`core/src/dmbot/rules/data/srd51/ATTRIBUTION.md`](core/src/dmbot/rules/data/srd51/ATTRIBUTION.md)):

> This work includes material taken from the System Reference Document 5.1 (“SRD 5.1”) by
> Wizards of the Coast LLC and available at
> <https://dnd.wizards.com/resources/systems-reference-document>. The SRD 5.1 is licensed
> under the Creative Commons Attribution 4.0 International License available at
> <https://creativecommons.org/licenses/by/4.0/legalcode>.
