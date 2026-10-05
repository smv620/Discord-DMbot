# Live test runbook: voice capture

A step-by-step script for testing DMbot against a real Discord voice channel on the
owner's PC. Written so **both the owner and a Claude session** can follow it. Each step
says who does it.

- **Owner:** anything in Discord or the Developer Portal, editing `.env`, and talking.
- **Claude (PyCharm session):** starting and watching the bot on the PC, reading logs,
  and filing issues.

Claude must **never read `.env`** or ask for the bot token. If something looks wrong
with a setting, ask the owner to check it.

---

## Test 1: voice capture (no transcription)

**Goal:** prove DMbot can capture each consenting speaker through Discord's DAVE
encryption, with little or no audio loss, and that consent is enforced.

### Prerequisites (owner, one time)

- [ ] **Bot created** in the Discord Developer Portal, with its token copied.
- [ ] **Install link** with scopes `bot` + `applications.commands` and permissions View
      Channels, Send Messages, Read Message History, Connect, Speak, Manage Channels,
      Manage Roles and Pin Messages. **Bot added** to the server.
- [ ] **Developer Mode** turned on (User Settings → Advanced), and the **server ID**
      copied.
- [ ] **No DM screen to make by hand:** `/dmbot start` creates
      `#dmb-dm-screen-<campaign>` (for a campaign called "Test Campaign":
      `#dmb-dm-screen-test-campaign`).
- [ ] **Test voice channel:** if it's private or restricted, DMbot has View Channel,
      Connect, and Send Messages there.
- [ ] **Postgres set up** on the PC, once (README, "Database"). Earlier test data
      (the old `data/` folder) is not carried over: campaigns are made again.
- [ ] **A second person** available for voice: a friend, or a second account on a phone.

### Step 1: `.env` (owner)

`.env` lives in the repo root and is copied from `.env.example`. If it was copied a
while ago, compare it with `.env.example` and add any missing sections (see #31).

Required for this test:
```
DISCORD_TOKEN=<bot token>
DISCORD_DEV_GUILD_ID=<server ID>
EARS_SHARED_SECRET=<long random string>
DATABASE_URL=postgresql://dmbot:dmbot@localhost:5432/dmbot
TRANSCRIBER=none
```
To make a secret: `python -c "import secrets; print(secrets.token_hex(32))"`

### Step 2: start core (Claude)

The owner uses **conda**, in an environment named `dmbot` (Python 3.12, Node 22).
Terminal 1:
```
conda activate dmbot
git fetch origin && git checkout development && git pull
pg_ctl -D "%USERPROFILE%\dmbot-pg" -l "%USERPROFILE%\dmbot-pg\log.txt" start
cd core
pip install -e ".[dev]"
python -m dmbot
```
(`pg_ctl` says "another server might be running" if Postgres is already up; that's fine.)
**Expect:** a log line `Waiting for ears on ws://127.0.0.1:8765`.
**If it exits with "Missing required settings":** ask the owner to fill in `.env` (step 1).
**If it exits with "Database problem":** the message says what's wrong. Usually Postgres
isn't running (run the `pg_ctl … start` line) or `DATABASE_URL` uses the `postgres`
superuser instead of `dmbot`.
**If it says "Local Whisper is not installed":** `TRANSCRIBER` isn't set to `none`. Ask
the owner to fix `.env`.

### Step 3: start ears (Claude)

Terminal 2:
```
conda activate dmbot
cd ears
npm ci
npm run dev
```
**Expect:** `[ears] logged in as DMbot#…`, then `[ears] connected to core`. Core logs
`ears connected`. In Discord, DMbot shows **online** (green dot).
**If `npm ci` fails building `@discordjs/opus` on Windows:** install "Visual Studio Build
Tools" with the C++ workload, then retry (see #8).

Tell the owner: **"Both parts are running. Go ahead with step 4."**

### Step 4: run the session (owner)

1. **Join the voice channel,** both people.
2. **Run `/dmbot start` in any text channel** (not the voice channel's chat). Pick the
   campaign (the first time, name a test campaign and press **Create campaign**), check
   the voice channel, and press **▶ Start listening**. If the command doesn't appear,
   press Ctrl+R in Discord.
3. **Expect:**
   - A new channel `#dmb-dm-screen-<campaign>` (the DM screen; for "Test Campaign":
     `#dmb-dm-screen-test-campaign`) with a help card ("🛡️ DM screen for …") that shows
     under the 📌 icon at the top of the channel, and in it: "✅ Listening in <channel>."
     If Pin Messages wasn't turned on: the card isn't pinned, and a "📌 I couldn't pin…"
     note appears in the DM screen once. That's expected. Turn it on, run `/dmbot start`
     again, and check that the card is pinned and the note is gone.
   - No other `dmb-` channels and no category yet. That's normal (compact mode).
   - In the voice channel's chat: "🔴 DMbot is listening in this channel…" (with a 👀
     **Peek behind the DM screen** button under the default setting).
   - If either is missing, the bot should warn in the DM screen about what to fix (#27).
4. **Both people get a private message from DMbot** ("🎙️ Can DMbot record you for your
   D&D game…") with **I consent** and **No thanks** buttons, and both press
   **I consent**. The message changes to "✅ You said yes on …" with a
   **Stop recording me** button.
   - Someone who agreed in an earlier session gets a reminder with the date instead.
   - Someone with DMs from server members turned off gets nothing; the DM screen says
     "📭 Not recording: …". They use `/consent give` instead.
   - Leaving and rejoining the voice channel in the same session doesn't send another
     message.
   - Check that someone already sitting in voice **before** `/dmbot start` gets the message
     too, and that the phone notification preview starts with "Can DMbot record you…".
   - Optional: a third person presses **No thanks**. They must never appear in capture
     checks, and they're asked again next session.
5. **Read the test script, then talk for 1–2 minutes.** First read the matching script in
   [`docs/test-scripts/`](test-scripts/README.md) (one person, or DM and Player), so audio
   and speech-to-text can be judged against known words and pauses. Then talk freely: take
   turns, use a few long sentences, and overlap once.
6. **Watch the DM screen.** Every 15 s:
   ```
   🎙️ Capture check
   • Name — N × speech, X.X s, audio NN%
   ```
   **The terminals show the same picture** (IDs and numbers only, no names or words):
   core logs `Session started`, `Consent given: user …` and a `Capture check: …` line
   every 15 s while someone is talking (low audio shows as `(audio gaps)`); ears logs `joined voice channel …` and `capturing user …` the first time
   each person is heard.
7. **Consent check:** the second person presses **Stop recording me** in DMbot's private
   message (or runs `/consent revoke`) and keeps talking. They
   must **disappear** from the following capture checks. In the terminals: core logs
   `Consent withdrawn: user …` and ears logs `not capturing user …: opted out`.
8. **Run `/dmbot help` and press Status,** then `/dmbot stop`.

### Step 5: judge the result (Claude and owner)

| Check | Pass | Fail |
|---|---|---|
| Both parts connected, bot online | Yes | Any startup error |
| Join messages in the DM screen and voice chat | Both appear | Either missing |
| Audio % per speaker | **95–100%** | Below 90% |
| Every consenting speaker appears in capture checks | Yes | Someone missing |
| Revoked speaker disappears | Yes | Still listed after revoke |
| No errors in either terminal during the session | None | Any traceback or error |

Audio of 90–94% (shown with "⚠️ audio gaps") is borderline. Repeat the test once before
calling it.

### Step 6: report (Claude)

1. **Every failure or oddity becomes a GitHub issue** (CLAUDE.md, "Issue log"), labeled
   `bug` and `session: pycharm`, with the exact log lines. Remove anything secret first.
2. **Post a summary comment** on the tracking issue for live tests (create one titled
   "Live test results" if none exists), including:
   - date, `development` commit SHA, OS, and Node and Python versions;
   - number of speakers and session length;
   - audio % per speaker, from 2–3 capture checks;
   - the Status output (from `/dmbot help`);
   - a pass or fail for each row of the table in step 5;
   - links to any issues filed.
3. **Update the testing logs** (CLAUDE.md, "Testing logs"). The logs are public:
   when copying terminal lines, replace Discord user IDs with "DM", "player A",
   "player B", and so on.
   - Append the full record, including anything the owner pasted from Discord, to
     `docs/testing-history.log`.
   - Rewrite `docs/testing-status.log` so it shows only the next test, any blockers and
     the latest result.
4. **Stop both processes** with Ctrl+C in each terminal.

---

## Test 2: live transcription (after Test 1 passes)

Same as Test 1, with these changes:

- **Owner:** set `TRANSCRIBER=whisper-local` in `.env` (keep `WHISPER_MODEL=small` to
  start).
- **Claude:** `pip install -e ".[dev,whisper]"` in core before starting. The first start
  downloads the Whisper model (a few hundred MB), so expect a delay.
- **Extra checks:**
  - Capture checks show `› <transcribed text>` lines that roughly match what was said.
  - Note how long text takes to appear after someone speaks.
  - Watch for "🐢 Transcription is falling behind" warnings.
  - Note CPU or GPU model and usage. This decides the cloud server size.
- **Report** as in Test 1, adding a few example transcriptions (said vs. heard) and the
  delay.
