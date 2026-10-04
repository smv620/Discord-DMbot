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
   pip install -e ".[dev,whisper]"   # drop ",whisper" if using cloud transcription
   python -m dmbot
   ```
4. **Start ears** (second terminal, Node 22.12+):
   ```bash
   cd ears
   npm install
   npm run dev
   ```
5. **In Discord:** make a private text channel (e.g. `#dm-screen`) and, in the channel's
   permissions, add the bot with **View Channel** and **Send Messages** — a private
   channel hides the bot too, so without this no updates appear. Join your voice channel
   and run `/table join` from `#dm-screen`. Each player runs `/consent give` (you too, if
   you want your own voice transcribed). Talk for a bit and watch the capture check appear.

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
`#dm-screen`; `/table status` shows the backlog.

When `TRANSCRIBER=cloud`, players are told during `/consent give` that their voice clips
go to an outside service.

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

## License

Licensed under the [PolyForm Strict License 1.0.0](LICENSE). You may use DMbot for
noncommercial purposes. Changing it, building on it, redistributing it, or any
commercial use requires written permission from the owner — contact
[@smv620](https://github.com/smv620).

Rules content from the System Reference Document 5.2 is used under
[CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/) and attributed where it appears.
