# Discord-DMbot

A Discord bot that helps the Dungeon Master run D&D sessions. It listens to the table's
voice channel, transcribes each player who has opted in, and privately advises the DM on
rules — published and homebrew. **The bot advises; the DM decides.**

See [`docs/PLAN.md`](docs/PLAN.md) for the full plan and roadmap.

## Status

**Phase 0 — capture pipeline.** The bot joins your table channel, records only players
who opt in, and posts a capture check to the DM every 15 seconds (who spoke, how long,
and audio health). Transcription arrives in Phase 1.

## How it's built

| Part | Language | Job |
|---|---|---|
| `ears/` | Node + TypeScript | Joins voice, captures each consenting speaker (Discord's DAVE encryption), streams audio to core |
| `core/` | Python | Slash commands, consent, speech segmenting, and (later) transcription, rules advice, house rules |

They run side by side on the same computer and talk over a local, password-protected link.

## Setup (test server)

1. **Create the bot.** In the [Discord Developer Portal](https://discord.com/developers/applications):
   New Application → **Bot** → Reset Token (copy it). Under **Installation**, give it the
   `bot` and `applications.commands` scopes with permissions **View Channels**, **Send
   Messages**, **Connect**, and **Speak**. Use the install link to add it to your private
   test server.
2. **Configure.** Copy `.env.example` to `.env` in the repo root and fill in
   `DISCORD_TOKEN`, `DISCORD_DEV_GUILD_ID`, and a long random `EARS_SHARED_SECRET`.
3. **Start core** (PyCharm terminal, Python 3.12+):
   ```bash
   cd core
   python -m venv .venv
   .venv\Scripts\activate        # Windows  (macOS/Linux: source .venv/bin/activate)
   pip install -e ".[dev]"
   python -m dmbot
   ```
4. **Start ears** (second terminal, Node 22+):
   ```bash
   cd ears
   npm install
   npm run dev
   ```
5. **In Discord:** make a private text channel (e.g. `#dm-screen`), join your voice
   channel, and run `/table join` from `#dm-screen`. Each player runs `/consent give`.
   Talk for a bit and watch the capture check appear.

## Commands

| Command | What it does |
|---|---|
| `/table join` | Listen to the voice channel you're in. You become the DM; updates go to the channel you ran it in. |
| `/table leave` | Stop listening (DM or server manager). |
| `/table status` | Show connection, table, and who has opted in. |
| `/consent give` | Let DMbot record and transcribe your voice in this server. |
| `/consent revoke` | Stop recording you and discard unprocessed audio. |

## Development

Rules for contributors (human or Claude) are in [`CLAUDE.md`](CLAUDE.md). CI runs
type checks, lint, and tests for both parts on every pull request.
