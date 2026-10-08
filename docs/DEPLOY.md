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

### Claude Code on the server

The server's Claude Code session runs in auto mode and keeps its approved commands in
`.claude/settings.local.json` (not in git). If prompts return after a fresh clone,
recreate it from this list.

```json
{
  "permissions": {
    "defaultMode": "auto",
    "allow": [
      "Bash(docker compose *)",
      "Bash(docker *)",
      "Bash(git *)",
      "Bash(gh *)",
      "Bash(scripts/*)",
      "Bash(./scripts/*)",
      "Bash(bash scripts/*)",
      "Bash(python3 *)",
      "Bash(python *)",
      "Bash(pytest *)",
      "Bash(ruff *)",
      "Bash(mypy *)",
      "Bash(npm *)",
      "Bash(node *)",
      "Bash(sleep *)",
      "Bash(curl *)",
      "Bash(psql *)",
      "Bash(pg_dump *)",
      "Bash(systemctl *)",
      "Bash(journalctl *)",
      "Bash(df *)",
      "Bash(free *)",
      "Bash(ps *)",
      "Bash(top *)",
      "Bash(ss *)",
      "Bash(chmod *)",
      "Bash(mkdir *)",
      "Bash(cp *)",
      "Bash(mv *)",
      "Bash(tar *)",
      "Bash(sed *)",
      "Bash(awk *)",
      "Bash(sort *)",
      "Bash(uniq *)",
      "Bash(xargs *)",
      "Bash(tee *)",
      "WebFetch(domain:docs.claude.com)",
      "WebFetch(domain:code.claude.com)",
      "WebFetch(domain:github.com)",
      "WebFetch(domain:api.github.com)"
    ],
    "deny": [
      "Read(./.env)",
      "Read(./**/.env)",
      "Bash(cat .env*)",
      "Bash(cat */.env*)",
      "Bash(docker compose config *)",
      "Bash(rm -rf /*)",
      "Bash(git push * main)",
      "Bash(git push * beta)",
      "Bash(git push * development)"
    ]
  }
}
```

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
| Set the admin page's password (#772) | `scripts/set-admin-password` |
| Put the admin API online (#836) | see "Put the admin API online" below |

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

### Put the admin API online

When you sign in to the admin page with Google, Google sends you back to the website's API
(#836), so that API must be reachable at `https://api.getdmbot.com`. A Cloudflare Tunnel
does that safely: the server only calls out to Cloudflare, so no new door is opened on it,
and Cloudflare looks after the secure padlock (HTTPS). Only the admin pages go through.
The pages customers will use stay closed until #498.

**You (the owner), for Discord sign-in (once, only if dev1 says the website's Discord
sign-in isn't set; the API won't start without it):**
- Open https://discord.com/developers/applications, then your app, then OAuth2, then Client
  information. Tell dev1 the **Client ID** (it is not secret). Press **Reset Secret**, copy
  the new secret, and on the server type `scripts/set-key DISCORD_CLIENT_SECRET` and paste it
  (press Enter, meaning No, if it asks to restart).
- **Never press Bot, then Reset Token.** That is the bot's own token: resetting it logs the
  bot out of Discord until it is changed on the server. If you are unsure which page you
  are on, stop and ask dev1.

**You (the owner), in Cloudflare:**
1. Go to https://one.dash.cloudflare.com, then Networks, then Tunnels (newer screens:
   Networks, then Connectors, then Cloudflare Tunnels), then Create a tunnel. Pick
   "Cloudflared" and name it `dmbot-api`. Cloudflare then shows an "Install and Run" page
   with a choice of system (Windows, Mac, Debian, Docker and so on) and a long command with
   the token inside it. Pick Docker (any choice will do: only the text matters) and **do
   not run any command shown there.** The token is only the very long text that starts
   with `eyJ`, after `--token` (or after `install`). You don't need to copy or keep it now:
   when dev1 says the update is in (step 4), open the tunnel's page in Cloudflare and copy
   it again from its install command. Never paste it into a chat, an email, an issue, or the
   Claude Code window. If Cloudflare won't show it again, tell dev1 (a new tunnel is easy to
   make).
2. Continue stays grey ("No connection detected yet"). That is normal: leave the page with
   Cancel or the back arrow. It stays grey because nothing is connected until dev1 starts
   the tunnel. You should now see `dmbot-api` in your list of tunnels, marked Inactive or
   Down. That is right, and the tunnel is already saved. If it isn't in the list, tell dev1.
3. Add the route. Go to https://one.dash.cloudflare.com, then Networks, then Tunnels (or
   Connectors, then Cloudflare Tunnels), then
   `dmbot-api`, then "Published application routes" (older screens call it "Public
   Hostname"), then Add, and fill in:
   - Subdomain: `api`
   - Domain: pick `getdmbot.com` from the list
   - Path: `^/admin(/|$)` (copy it exactly, including the `^` at the start and the `$`
     near the end)
   - Service type: HTTP (Cloudflare may ask for this first)
   - URL: `web-api:8080` (if Cloudflare insists on a full address, use
     `http://web-api:8080`)

   Save. The Path is what keeps everything except the admin pages closed, so never leave
   it empty: if Cloudflare rejects it, stop and tell dev1. Cloudflare adds the DNS record
   itself: don't add one by hand.

**You (the owner), on the server, once dev1 says the update is in:**

4. Copy the token again from the tunnel's page in Cloudflare (open
   https://one.dash.cloudflare.com, then Networks, then Tunnels, then `dmbot-api`). Open your
   server connection (ssh) as for the earlier steps, `cd Discord-DMbot`, type this and press
   Enter:

       scripts/set-key CLOUDFLARE_TUNNEL_TOKEN

   When it asks for the value, paste the token and press Enter. You won't see it as you
   type. It may ask "Restart DMbot now?": press Enter (that means no). It may also print
   "Not restarted..." and a `docker compose up -d` line: ignore it and don't type it, dev1
   starts the tunnel. When you see "Saved CLOUDFLARE_TUNNEL_TOKEN", it worked. If you
   pasted the wrong thing, run the same command again and paste the right token. If it says
   "Not saved. CLOUDFLARE_TUNNEL_TOKEN isn't one of DMbot's keys", stop: don't edit
   anything and don't follow its `nano .env` hint. Tell dev1 "set-key doesn't know the
   tunnel key" (the update isn't in yet). Nothing was changed.
5. Tell dev1, in the Claude Code window where dev1 runs (not GitHub): "tunnel ready"
   (just those words, never the token). dev1 will reply with the result, usually within a
   few minutes. If nothing comes back, ask again.

If any step shows an error, or Cloudflare says something you don't understand, stop and
tell dev1 what the screen says (never the token). Nothing is broken by stopping: the
tunnel stays off until dev1 starts it.

**dev1:**
0. Before the owner's step 4: update the server's checkout (`git pull`), run
   `scripts/update-env --check`, then `scripts/update-env` so `.env` gets the new
   `CLOUDFLARE_TUNNEL_TOKEN` line. Confirm with `scripts/set-key`: its list must show
   `CLOUDFLARE_TUNNEL_TOKEN` (press Enter to cancel). Only then tell the owner: "The update
   is in. Do step 4."
1. Make the website API ready to start, or the tunnel connects but answers 502:
   - Check the settings (this prints no secrets):
     `grep -E '^(COMPOSE_PROFILES|WEB_CLIENT_IP_HEADER|WEB_API_URL|WEB_SITE_URL|WEB_API_PORT)=' .env`.
     `COMPOSE_PROFILES` must include `web`, `WEB_CLIENT_IP_HEADER` must be `CF-Connecting-IP`,
     `WEB_API_URL` must be `https://api.getdmbot.com` and `WEB_SITE_URL` the website's address
     (`https://dev.getdmbot.com`, #833). `WEB_API_PORT` must be empty or 8080, the port the
     owner's route points at. Fix them with `nano .env` (set-key only takes keys).
   - The API also won't start without its database role (`DMBOT_WEB_DB_PASSWORD`), the Discord
     sign-in (`DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`) and `WEB_SECRET_KEY`.
   - Run `docker compose up -d web-api`, then `docker compose logs --tail 20 web-api`. An
     error names the setting to fix: fix it and run both again.
   - Check `scripts/set-key` lists `CLOUDFLARE_TUNNEL_TOKEN` as "set" (press Enter to cancel):
     with the `web` profile on and no token, the tunnel keeps restarting.
2. Wait for the owner's "tunnel ready", then run `docker compose up -d cloudflared`, then
   `docker compose logs --tail 20 cloudflared`. Look for "Registered tunnel connection".
3. Check from any computer (each prints a number):
   - `curl -s -o /dev/null -w '%{http_code}\n' https://api.getdmbot.com/health` must print
     404. web-api itself always answers 200 here, so a 404 proves Cloudflare is keeping
     the other paths closed. If it prints 200, the limit is off: run
     `docker compose stop cloudflared` at once and tell the Supervisor.
   - `curl -s -o /dev/null -w '%{http_code}\n' https://api.getdmbot.com/admin/auth/ways`
     must be an answer from DMbot itself: 404 while `ADMIN_EMAILS` is empty (the admin page
     isn't on yet), 200 after "Turn on the admin page". A Cloudflare error page (502 or 530)
     means the route or web-api is wrong.
   - Optional second check: `https://api.getdmbot.com/me` must print 404 as well. A 401
     also means the limit is off.
4. If it fails:
   - No "Registered tunnel connection": the token is missing or wrong. Ask the owner to run
     set-key again, then `docker compose up -d --force-recreate cloudflared`.
   - A 502 or 530: check `docker compose logs --tail 20 web-api`, then the route
     (subdomain, domain, URL) with the owner.
   - `/health` or `/me` answers as web-api would: stop the tunnel (see above) and fix the
     Path with the owner.
5. When it works, record the result in the testing log.

To turn it off, dev1 runs `docker compose stop cloudflared`. The admin page then can't be
reached from outside until it is started again. To remove the token as well, empty the
`CLOUDFLARE_TUNNEL_TOKEN=` line with `nano .env` (set-key can't empty a value).

### Turn on the admin page

The admin page (`/admin` on the website, never linked) is for giving free access (#772).
The owner sets the password and, if wanted, Google sign-in; dev1 adds the other settings
and restarts the website. Your email and the Google Client ID never go in the repository,
an issue or a PR.

**You (the owner):**
0. Tell dev1 you're turning on the admin page. If you want Google sign-in, dev1 gives you
   a redirect address for step 3.
1. On the server: log in with ssh, then `cd Discord-DMbot`.
2. Type `scripts/set-admin-password`. Pick a password of 16 or more characters (a long
   sentence works). You won't see it as you type. Type it only there, never into a chat.
   If it shows an error, tell dev1 what it says (never the password).
3. Optional, for Google sign-in, in the Google Cloud console
   (https://console.cloud.google.com/auth/clients; Google may call these pages "Google Auth
   Platform": Branding, Audience, Clients):
   - The OAuth consent screen (Audience): External, kept in Testing, with the email
     you'll sign in with as a test user.
   - Credentials (Clients) → Create credentials → OAuth client ID → Web application. Its
     redirect address is the one dev1 gave you. Copy the Client ID and Client secret
     now; Google may not show the secret again.
   - On the server, type `scripts/set-key GOOGLE_CLIENT_SECRET` and paste the Client
     secret. If it asks "Restart DMbot now?", press Enter (that means no), and skip its
     line about docker compose: dev1 restarts the website part.
4. Tell dev1, in the Claude Code window where dev1 runs (not GitHub): "admin password
   set", the email you'll sign in with (for Google, that Google account's email), and
   the Client ID if you made one. Never the password.

**dev1:**
1. When the owner says they're starting and wants Google sign-in, give them the redirect
   address: your `WEB_API_URL` value followed by `/admin/auth/google/callback`.
2. When the owner says it's done, edit `.env` (`nano .env`; set-key only takes keys):
   set `ADMIN_EMAILS` to the owner's email, `GOOGLE_CLIENT_ID` if the owner gave one, and
   `WEB_CLIENT_IP_HEADER` to match how the API is served (`CF-Connecting-IP` behind
   Cloudflare). The website won't start with a Client secret and no Client ID: if you
   must restart it before the ID arrives, empty `GOOGLE_CLIENT_SECRET` first.
3. Run `docker compose up -d web-api`, then check `docker compose logs --tail 20 web-api`.
   An error names the setting to fix: fix it and run both again.
4. When it's clean, send the owner the full link (`WEB_SITE_URL` + `/admin`) to sign in,
   and record the result in the testing log (no email).

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
