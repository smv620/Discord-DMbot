# Deploying DMbot to a cloud server

DMbot runs as two containers (core and ears) on one Linux server. Nothing is opened to
the internet: the bot only makes outgoing connections to Discord (and to your
speech-to-text company, if you use `TRANSCRIBER=deepgram` or `cloud`).

## 1. Choose a server

Any provider that sells a plain Linux VM works (Hetzner, DigitalOcean, Linode/Akamai,
Vultr, AWS Lightsail, …). Pick **Ubuntu 24.04 LTS**.

| Transcription | Suggested size | Notes |
|---|---|---|
| `TRANSCRIBER=deepgram`, `cloud` or `none` | 1–2 vCPU, 2 GB RAM | Cheapest server. With `deepgram` or `cloud` you pay that company separately, per minute of speech. Build with `CORE_EXTRAS=dev`. |
| `whisper-local`, model `small` | 4+ vCPU, 8 GB RAM | Works for a typical table; a few seconds of delay. Prefer dedicated CPU over shared. |
| `whisper-local`, `large-v3`/`turbo` | NVIDIA GPU server | Best accuracy; costs much more. Needs the NVIDIA container toolkit (not covered here). |

Start small. You can switch `TRANSCRIBER` later by editing `.env` and running
`docker compose up -d`. Moving to `whisper-local` from a `CORE_EXTRAS=dev` build also
needs a rebuild (`--build`) without that line.

## 2. Install Docker

SSH into the server, then:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # log out and back in after this
```

## 3. Get DMbot and configure it

Servers run the **`main`** branch: tested code that is ready to deploy. (`development` is
work in progress and `beta` is for testing; see CLAUDE.md.)

```bash
git clone --branch main https://github.com/smv620/Discord-DMbot.git
cd Discord-DMbot
cp .env.example .env
nano .env        # fill in DISCORD_TOKEN, EARS_SHARED_SECRET, TRANSCRIBER (and
                 # DEEPGRAM_API_KEY for deepgram), POSTGRES_ADMIN_PASSWORD,
                 # DMBOT_DB_PASSWORD, …
chmod 600 .env   # only you can read your secrets
```

To add or change a key later, for example from your phone over SSH, skip the editor:

```bash
cd Discord-DMbot
scripts/set-key                    # pick from a list of keys and tokens
scripts/set-key DEEPGRAM_API_KEY   # or name the one you want
```

Paste the key when asked. You won't see it as you paste; that's normal. Paste once, then
press Enter. The helper shows back only the key's length and last 4 characters, and
keeps `.env` at `chmod 600`. If DMbot is running it offers to restart it, which drops the
bot from voice for about a minute, so don't do it mid-session. Keys go into the server's
`.env` only: never paste them into chat or issues. It sets only keys, tokens and secrets;
edit `.env` for anything else, including the database passwords (see below).

Make both database passwords long, random, and letters and numbers only
(`openssl rand -hex 24`). Compose runs Postgres for you and sets `DATABASE_URL`; the
database is not reachable from outside the server.

Leave `DISCORD_DEV_GUILD_ID` set to your server's ID — slash commands appear there
instantly. (Without it, commands register globally, which can take up to an hour.)

If you use `TRANSCRIBER=deepgram`, `cloud` or `none`, also add `CORE_EXTRAS=dev` to
`.env` for a much smaller image.

## 4. Start it

```bash
docker compose up -d --build
docker compose logs -f        # watch it start; Ctrl+C stops watching, not the bot
```

You should see `ears connected` in core's log and `logged in as …` from ears. The first
start with local Whisper downloads the model (a few hundred MB for `small`); it is kept
in a volume, so restarts are fast.

## 5. Everyday commands

| Task | Command |
|---|---|
| Update to the latest release | `git checkout main && git pull origin main && docker compose up -d --build` |
| Test a beta on a test server | `git checkout beta && git pull origin beta && docker compose up -d --build` |
| Restart | `docker compose restart` |
| Stop | `docker compose down` |
| See logs | `docker compose logs -f core` (or `ears`) |
| Status | `docker compose ps` |
| Update `.env` after an update changes `.env.example` | `scripts/update-env` (add `--check` to only look) |
| Add or change a key | `scripts/set-key` |

Updates never change your `.env`. When one brings a new `.env.example`, run
`scripts/update-env`. It rebuilds `.env` in the new layout, keeps every value you had, and
puts settings it doesn't know at the end. Your old file is saved next to it as
`.env.backup-…`. If a value runs over several lines it stops and changes nothing, so fix
that line first. It then lists empty keys: fill in the ones you use with `scripts/set-key`.
Then restart with `docker compose up -d` (DMbot leaves voice for about a minute, so not
mid-session).

**After updating,** look at core's log (`docker compose logs core`). Updates never change
your `.env`, so if `.env.example` gained new settings, the log names them at start-up
("Your .env is missing …"). Copy those lines into `.env` (blank keeps the default) and
run `docker compose up -d`.

The containers restart automatically after a crash or a server reboot. A game that was
running picks up where it left off: DMbot rejoins the voice channel and posts a note in
the DM screen. A few seconds of speech during the restart can't be recovered.

DMbot won't rejoin, and says why in the DM screen, if the voice channel is gone or it's no
longer allowed in, if the session started more than 16 hours ago, or if it restarted five
times in a row (to stop a crash loop). `/dmbot stop` always ends a session for good, even
while DMbot is restarting.

### Logs

On a server, both parts write one JSON object per line (`LOG_FORMAT=json`, the Compose
default), with `shard_id`, `guild_id` (the Discord server) and `campaign_id` where known,
so a log collector can filter by server or campaign. Logs never contain names, what was
said, or keys. Set `LOG_FORMAT=text` in `.env` for easier reading by eye.

### Growing past one shard

One core + ears pair with one shard is enough until DMbot is in a few thousand servers.
To split the load, run more pairs, each with the **same** `SHARD_COUNT` and its own
`SHARD_IDS` (e.g. `SHARD_COUNT=4`: `SHARD_IDS=0,1` on one, `2,3` on another). A core
refuses an ears whose shard settings differ, and says so in the log.

## Data and backups

Campaigns and consent live in Postgres, in the `postgres-data` Docker volume. Back it
up with:

```bash
docker compose exec -T postgres pg_dump -U postgres -Fc dmbot > dmbot-$(date +%F).dump
```
Restore into a fresh install with
`docker compose exec -T postgres pg_restore -U postgres -d dmbot --clean < dmbot-DATE.dump`.
The Whisper model is kept in its own volume and downloads again if lost, so it needs no backup.

## Security checklist

- `.env` is `chmod 600` and never committed (it is in `.gitignore`).
- No ports are published; `docker compose ps` should show no `0.0.0.0:` mappings.
- Keep the server patched: `sudo apt update && sudo apt upgrade` monthly.
- Use SSH keys, not passwords, to log in to the server.
