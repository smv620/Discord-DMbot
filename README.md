# Discord-DMbot

A Discord bot that helps the Dungeon Master run D&D sessions. It listens to the table's
voice channel, transcribes each player who has opted in, and privately advises the DM on
rules — published and homebrew. **The bot advises; the DM decides.**

See [`docs/PLAN.md`](docs/PLAN.md) for the full plan and roadmap.

## Status

**Phase 1 — live transcription.** The bot joins your table channel, records only players
who opt in, and every 15 seconds posts to the DM's private channel who spoke, what they
said, and audio health.

## How it's built

| Part | Language | Job |
|---|---|---|
| `ears/` | Node + TypeScript | Joins voice, captures each consenting speaker (Discord's DAVE encryption), streams audio to core |
| `core/` | Python | Slash commands, consent, speech segmenting, and (later) transcription, rules advice, house rules |

They run side by side on the same computer and talk over a local, password-protected link.

## Setup (test server)

For a full, step-by-step live test (who does what, expected output, pass/fail, and how to
report), see [`docs/LIVE_TEST.md`](docs/LIVE_TEST.md).

1. **Create the bot.** In the [Discord Developer Portal](https://discord.com/developers/applications):
   New Application → **Bot** → Reset Token (copy it). Under **Installation**, give it the
   `bot` and `applications.commands` scopes with permissions **View Channels**, **Send
   Messages**, **Read Message History**, **Connect**, **Speak**, **Manage Channels**, and
   **Manage Roles**. Use the install link to add it to your private test server.
   *Why Manage Channels and Manage Roles:* Discord grants these for the whole server, but
   DMbot only uses them on its own DM screen channels: it creates `#dm-screen-<campaign>`,
   hides it from players unless you choose otherwise, and lets a player peek after a
   spoiler warning. It never changes other channels or anyone's server roles.
2. **Configure.** Copy `.env.example` to `.env` in the repo root and fill in
   `DISCORD_TOKEN`, `DISCORD_DEV_GUILD_ID`, a long random `EARS_SHARED_SECRET`, and
   `DATABASE_URL` (see [Database](#database)).
3. **Start Postgres** (once per PC restart; see [Database](#database)).
4. **Start core** (PyCharm terminal, Python 3.12+):
   ```bash
   cd core
   python -m venv .venv
   .venv\Scripts\activate        # Windows  (macOS/Linux: source .venv/bin/activate)
   pip install -e ".[dev,whisper]"   # drop ",whisper" if using cloud transcription
   python -m dmbot
   ```
5. **Start ears** (second terminal, Node 22.12+):
   ```bash
   cd ears
   npm ci
   npm run dev
   ```
6. **In Discord:** make a private text channel (e.g. `#dm-screen`) and, in the channel's
   permissions, add the bot with **View Channel** and **Send Messages** — a private
   channel hides the bot too, so without this no updates appear. Join your voice channel
   and run `/dmbot start` from `#dm-screen`. The first time, it asks you to name your
   campaign; after that it offers the last campaign and voice channel you used. Each player runs `/consent give` (you too, if
   you want your own voice transcribed). Talk for a bit and watch the capture check appear.

## Database

DMbot keeps campaigns and consent in **Postgres** (16 or newer). Each Discord server's
rows are locked to that server by Postgres itself (row-level security), so DMbot must
connect as an **ordinary database user, not a superuser** such as `postgres`. It refuses
to start otherwise. The database must use UTF8. DMbot creates its tables on first start.

**On your PC with conda** (one time):
```bash
conda activate dmbot
conda install -c conda-forge postgresql
initdb -D "%USERPROFILE%\dmbot-pg" -U postgres -E UTF8 --no-locale   # macOS/Linux: -D ~/dmbot-pg
pg_ctl -D "%USERPROFILE%\dmbot-pg" -l "%USERPROFILE%\dmbot-pg\log.txt" start
psql -U postgres -c "CREATE ROLE dmbot LOGIN PASSWORD 'dmbot'"
createdb -U postgres -O dmbot -E UTF8 -T template0 dmbot
```
Then in `.env`: `DATABASE_URL=postgresql://dmbot:dmbot@localhost:5432/dmbot`.
This database only listens on your own PC. After a restart, run the `pg_ctl … start` line
again before starting core. Stop it with `pg_ctl -D "%USERPROFILE%\dmbot-pg" stop`.

**With Docker Compose:** nothing to install. Set `POSTGRES_ADMIN_PASSWORD` and
`DMBOT_DB_PASSWORD` in `.env`; Compose starts Postgres and creates the `dmbot` user.

## Running on a server

See [`docs/DEPLOY.md`](docs/DEPLOY.md): one `docker compose up -d --build` starts both
parts on any Linux cloud server.

## Transcription engines

Pick one with `TRANSCRIBER` in `.env`:

| Engine | Cost | Needs | Notes |
|---|---|---|---|
| `whisper-local` (default) | Free | `pip install -e ".[whisper]"`; a strong CPU or an NVIDIA GPU | Audio never leaves your server. Model downloads on first run. |
| `cloud` | Pay per minute of speech | `CLOUD_STT_API_KEY` | Any OpenAI-compatible speech-to-text API. Best for servers without a GPU. |
| `none` | Free | — | Capture checks only, no text. |

On a CPU-only server use `WHISPER_MODEL=small` (or `base` if it falls behind). With an
NVIDIA GPU use `large-v3` or `turbo`. The default compute type (`auto`) picks the fastest
precision for your hardware. If transcription falls behind, DMbot warns you in
`#dm-screen`; the **Status** button in `/dmbot help` shows the backlog.

When `TRANSCRIBER=cloud`, players are told during `/consent give` that their voice clips
go to an outside service.

## Commands

| Command | What it does |
|---|---|
| `/dmbot start` | Pick the campaign (or make a new one) and the voice channel, then start listening. DM notes go to the campaign's `#dm-screen-<campaign>` channel, which DMbot makes the first time. |
| `/dmbot stop` | Stop listening (the campaign's DM, or a server manager). |
| `/dmbot help` | What DMbot does and doesn't do, plus a **Status** button. |
| `/dmbot backup` | Download a copy of a campaign you run. |
| `/dmbot restore` | Bring a campaign back from a copy, as a new campaign or replacing one of yours. |
| `/consent give` | Let DMbot record and transcribe your voice in this server. |
| `/consent revoke` | Stop recording you and discard unprocessed audio. |

## Development

Rules for contributors (human or Claude) are in [`CLAUDE.md`](CLAUDE.md). CI runs
type checks, lint, and tests for both parts on every pull request.

## License

Licensed under the [PolyForm Strict License 1.0.0](LICENSE). You may use DMbot for
noncommercial purposes. Changing it, building on it, redistributing it, or any
commercial use requires written permission from the owner — contact
[@smv620](https://github.com/smv620).

Rules content from the System Reference Document 5.2 is used under
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) and attributed where it appears.
