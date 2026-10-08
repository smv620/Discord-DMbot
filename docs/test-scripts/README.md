# Read-aloud test scripts

Fixed scripts to read out loud during a live test (#119). Free talk at a D&D table has long,
natural pauses, and afterwards nobody knows exactly what was said. With a script we know the
words, who said them, and where the silences are. That lets us check:

- **Audio:** are packets really lost, or was someone just quiet?
- **Speech-to-text:** how well does Whisper (or a cloud service) write it down? Use the same
  script on every run so results can be compared.

| People testing | Script | Who reads |
|---|---|---|
| 1 (DM only) | [dm-only.md](dm-only.md) | The DM reads every line (they're all `[DM]`; the script already narrates the players' actions) |
| 2 (DM + 1 player) | [dm-and-player.md](dm-and-player.md) | The DM reads `[DM]`, the player reads `[Player]` |
| 3 or more (DM + players) | [dm-and-player.md](dm-and-player.md) | The DM reads `[DM]`. The players split the `[Player]` lines and agree who reads which before starting (for example, in voice-channel order) |
| Speech-to-text bake-off (#128), 2–4 readers | [stt-bakeoff.md](stt-bakeoff.md) | Each reader reads every line, recorded on their own device (not through DMbot). About 6–8 minutes |
| Twin name test, 1 reader (#367) | [bakeoff-story.md](bakeoff-story.md) | One reader, alone, recorded on their own device: a three-minute story with every bake-off name at least twice. Read with a two-count stop between lines, it should cut into about 27 pieces of speech (25 lines and 2 dramatic pauses). The recording, `bakeoff-story.m4a`, goes next to it once the owner records it. Its names list for the Add many test is [bakeoff-story-names.txt](bakeoff-story-names.txt), with [the setup](bakeoff-story-names-setup.md) (#368) |
| Two voices, 1 reader (#534) | [two-voices.md](two-voices.md) | One reader, alone, recorded twice on their own device: once the `[DM]` lines, once the `[Player]` lines, quiet for ten seconds at the other voice's cues (but not after your own last line). About a minute and a half each |
| Look-alike names, 1 reader (#534) | [names-stress.md](names-stress.md) | One reader, alone, on their own device: three to four minutes of 20 pairs of names that sound alike (two known names, a name and an everyday word, a known name and a new one), for the name fixing and the after-session name scan. Its names list is [names-stress-names.txt](names-stress-names.txt). Scored by hand with its scoring key for now: the twin can't score it yet |

The two table scripts, [dm-only.md](dm-only.md) and [dm-and-player.md](dm-and-player.md),
take about a minute each and score the same 12 D&D terms.

**Recordings** (for the session twin, #299, and offline speech-to-text checks): `DMOnlyAudio.m4a`
(dm-only.md), `dm-and-player.m4a` (dm-and-player.md) and `stt-bakeoff.m4a` (stt-bakeoff.md, the
owner reading alone, 2026-10-07, read straight through without the script's 2-second
pauses). The bake-off recording is the better test for name resolution: 24 campaign names,
each said several times, at a natural pace.

**Sending it:** paste the script into a Discord message (it fits, and Discord shows the bold
labels) rather than sending the file. two-voices.md doesn't fit in one Discord message:
send the file or a link instead.

## What's in it

1. **Part 1, everyday words** (35 scored words in dm-and-player, 33 in dm-only, plus the
   whispered sentence). Any
   speech-to-text should get these right, so
   mistakes here point to audio problems, not hard vocabulary.
2. **Part 2, D&D words.** Ten-Towns names from *Icewind Dale: Rime of the Frostmaiden*, a god,
   a monster, a spell, a weapon and a rules term, with a pronunciation guide so every reader
   says them the same way.

Stage directions:
- **(( dramatic pause ))**, inside a line: the reader **mutes**, counts to three, and
  unmutes. Muting stops Discord sending sound (just going quiet often doesn't: Discord keeps
  sending for a moment, and room noise keeps it going). DMbot ends a piece of speech after
  less than a second of silence, so each dramatic pause should **split that line into two
  pieces of speech**. Pauses are spaced so no stretch of speech is longer than about 8 s:
  even if one pause fails, the merged piece stays under DMbot's 15 s cut. Silence between pieces is never counted as lost audio. Short
  breaths inside a sentence are what DMbot counts as pauses (`pauses=` in the server logs).
- **(( whispering ))**: an everyday sentence in *italics*, said quietly. It has no scored
  D&D words, so a miss is about quiet speech, not vocabulary. Note: Discord itself may not send a whisper
  (voice activity sensitivity, noise suppression such as Krisp). If the whisper is missing,
  check whether the reader's green speaking ring lit up.

## Running the test

1. **Before:** on the server, `DMBOT_DEBUG_AUDIO=1` in `.env` (ears logs one `audio …` line per
   piece of speech). Ask: how many people (1 = DM only)?
2. `/dmbot start`, and every reader presses **I consent** (or already has).
3. Read the script once, start to finish.
4. **Wait until the last line ("…Lonelywood and Caer-Dineval") shows up in the transcript
   channel** (`#dmb-transcript-<short name>`, #124), or until nothing new has appeared for
   30 seconds. Only then do free talk or `/dmbot stop`: stopping drops lines that are still
   being written down.
5. Copy **everything** in the transcript channel between this session's "Session started"
   and "Session ended" dividers. Lines are in the order each piece of speech finished being
   written down, labelled `**Name:**`. (Text only shows if speech-to-text is on, that is
   `TRANSCRIBER` isn't `none`.)

## Scoring

Ignore capitals, punctuation and hyphens. "It's" = "it is", "3" = "three", "Ten Towns" =
"Ten-Towns".

- **Audio:** from the ears `audio …` lines in the server logs: add up `received` and
  `expected` for the script. (Core's `Capture check:` lines show only a % per check, not
  counts, and the DM screen only warns below 90%, so use the ears lines.) 95–100% passes;
  90–94% is borderline, so repeat once; under 90% fails (same as LIVE_TEST.md).
- **Pieces of speech:** count the ears `audio …` lines (core's own cut at 15 s adds pieces
  ears doesn't see). dm-and-player: the DM about 6 (4 lines + 2 dramatic pauses) and the
  players about 4 in total (one per `[Player]` line). dm-only: about 7 (2 paragraphs + 5
  dramatic pauses); 7–9 is fine, since a slow reader's sentence breaks or the switch to a
  whisper can add a piece. Fewer means a dramatic pause didn't split the speech. Any piece
  of about 15 s means DMbot's cut split it, possibly mid-word. Judge lost audio from
  `received`/`expected`, not from the number of pieces.
- **Part 1:** count words wrong, missing, and **added** (for example "Thanks for watching"
  during a pause), **leaving out the whispered sentence** (scored on its own below). Out of
  35 (dm-and-player) or 33 (dm-only).
- **Part 2:** count how many of these 12 came out right. Accepted variants are in brackets.

  | Term | Also OK |
  |---|---|
  | Bryn Shander | Brin Shander, Bryn Shandar |
  | Ten-Towns | Ten Towns |
  | Targos | Targus |
  | Easthaven | East Haven |
  | Detect Magic | |
  | Dexterity saving throw | |
  | frost giant | |
  | Auril | Aurel |
  | Frostmaiden | Frost Maiden |
  | longsword | long sword |
  | Lonelywood | Lonely Wood |
  | Caer-Dineval | Care Dineval, Kair Dineval |

- **Whispered sentence:** all / part / missing, and whether the speaking ring lit up.
- **Errors on the first word after a dramatic pause:** worth noting separately, but only
  where the pause really split the speech (an ears `audio …` line ends there). If errors
  bunch up there, the start of each piece of speech is being cut (#121, an ears problem,
  not speech-to-text).

## Replaying a recording (the session twin, #299)

A recording of `dm-only.md`, `dm-and-player.md` or `stt-bakeoff.md` can be scored without
anyone in voice. It goes through core's real speech path (the Segmenter, the transcription
pipeline and the same speech-to-text as the bot) and comes out scored the way this page
describes. For `stt-bakeoff.md` the record instead scores every time a campaign name is
said (right, wrong or missing, per name), the nickname "Bell", the rules words, names
written where none was said, and the everyday lines' word error rate. Names are scored
wherever they land; a name cut in half between two pieces of speech is counted apart
("cut"), since live the halves arrive separately. Its names and rules words go to the engine
as hints, as the live bot sends a campaign's names (`--no-hints` to leave them out). One
reader takes every role, so pieces run longer and core's 15 s cut falls more often than at a
table, where each player's speech arrives on its own.

```bash
scripts/replay docs/test-scripts/DMOnlyAudio.m4a --script docs/test-scripts/dm-only.md \
  --transcriber deepgram          # or whisper-local, cloud; --log adds a "Twin run"
```

- **The name scan (#395):** `--names FILE` (a names list, or a setup note such as
  `bakeoff-story-names-setup.md`) sends those names as the bot's own hints, with any
  script. With `stt-bakeoff.md` or `bakeoff-story.md`, the campaign also knows them for the
  name scan, and the real Cleaner fixes them when misheard. After the replay, the bot's after-session name scan runs on the text as heard
  (what it scans today), on the cleaned text (#394), and on the script's own text (the most
  it could find). The record counts the new story names found, known names suggested again,
  rules words suggested and anything else, with the bot's 10-suggestion limit (what the DM
  sees) and without it (the real recall). Without `--names` there's no scan score: the live
  bot never hints names it doesn't know.
- **Two voices (#534):** [two-voices.md](two-voices.md) is the DM and Player script recorded
  by one person as two files, one per voice. Give them with
  `--speakers two-voices-dm.m4a:1001,two-voices-player.m4a:1002` instead of one recording.
  The twin finds each file's turns at 5 s of quiet (`--turn-quiet-ms`) and plays them in
  the script's order as two made-up people. Each turn starts 800 ms after the last one ends
  (`--answer-ms`; a negative number talks over the end). The record adds each speaker's own
  score.
  - `--stop 1002@0:25`: that person presses Stop recording me at 0:25 of the replay. What
    they say after it must never be written down, and the record checks this.
  - `--agree 1002@0:25`: they say yes only then (the first-time question), so nothing
    before it is heard. The times are the replay's, as printed under "heard:".
  - One of these per person. Without `--realtime`, the twin waits at each one for what was
    said before it to be written down, which is kinder than a table: live, words still
    being written down when someone stops are thrown away. Only `--realtime` shows that.
- Recordings other than 16 kHz mono WAV need `pip install -e ".[twin]"` in `core/`.
- The engine's settings come from the environment, as for the bot.
- `--realtime` sends the audio as it was spoken, to time the delay a table would see.
- Each piece also gets the 100 ms of audio just before it (`--lead-in-ms`, 0 to turn off).
  Live audio probably gets this anyway, as Discord starts sending a moment before the first
  loud sound (not measured). On DMOnlyAudio.m4a it saved a first word after a pause and the
  whole whispered sentence, but one pause no longer counted as split; that is still being
  looked into (#299).
- An outside engine (`deepgram`, `cloud`) costs money: it runs only when `--transcriber`
  names it, and says first how much audio it sends and roughly what that costs.
- **Speech sent (#523):** every record says how many seconds of speech went to the engine
  (what an outside engine charges for) out of the recording's length, as a percentage.
  Pieces the engine failed on still count. A read-aloud script is nearly all speech, so
  this runs higher than at a real table.
- **On the server:** run it from the host's venv, never inside the bot's container and never
  during a live session (speech-to-text competes with the bot). It holds the whole recording
  in memory, so keep to the one-minute scripts.
- **What it doesn't model:** Discord and ears. Pieces are cut at 1 s of near-silence (set
  so `DMOnlyAudio.m4a` matches run 7; in a recording that wasn't muted, near-silence means
  within 10 dB of the room's noise, as a voice gate works), no audio is lost, and a recording's dramatic pauses
  are quiet, not muted. It scores the text as heard, before the name cleaning the transcript
  channel shows (part 2 of #299). So use replays to compare speech-to-text and changes to
  core, and live runs for audio and pieces of speech.

## Run 8: what the twin answers, and what needs Discord (#534)

Live tests need the owner and a table, so run 8 only checks what the twin can't. The twin
replays recordings through the bot's own code. It can't test Discord itself (buttons,
private messages, channels, menus, phones) or the live audio.

**The twin answers these.** Each row is its own run. The `--speakers`, `--stop` and `--agree`
rows wait for #549 (those options and two-voices.md) and for the owner's two recordings of
two-voices.md; until both are in, check those four live.

| Run 8 item | Twin run, and the line to read |
|---|---|
| Each voice's own accuracy, two people taking turns | `scripts/replay --speakers docs/test-scripts/two-voices-dm.m4a:1001,docs/test-scripts/two-voices-player.m4a:1002 --script docs/test-scripts/two-voices.md --transcriber deepgram`: the "DM (1001)" and "Player (1002)" lines |
| Script score: part 1 (35 words), part 2 (12 terms), the whisper | the same run: "part 1:", "part 2:" and "whisper:" |
| A player stops: nothing after it is written down | the first command plus `--stop 1002@M:SS`, with a time between two of the player's cues, read from the first run's "heard:" lines (the replay's own clock): "Player stopped at …: 0 pieces (should be 0)" |
| Capture only after the first-time yes | the first command plus `--agree 1002@M:SS` (a separate run: one change per person): "Player agreed at …: 0 pieces (should be 0)" |
| Auril and Caer-Dineval with hints (run 7's misses) | `scripts/replay docs/test-scripts/dm-and-player.m4a --script docs/test-scripts/dm-and-player.md --names docs/test-scripts/dm-and-player-names.txt --transcriber deepgram`: "part 2:". Ready now. It sends only those two names as hints (live also sends people's names and the campaign's others), and scores the text as heard, before any name fixing |
| How much speech goes to Deepgram compared with the time listened (#523) | every record's "speech sent:" line, for a recording. Live gives a real table's number |

**Partly** (the twin helps, but check these live too):
- First word after a dramatic pause (#121, #270): the twin scores it, but a recording's
  pauses are quiet, not muted, so how Discord handles a mute is only tested live.
- Last words kept at a stop (#202): the twin always writes everything down, so only live
  tests the stop button's wait.
- Deepgram refusing a long list of names (#262): a twin run with a real names list shows
  "failed: 0", but a list that short is rarely refused. Only live shows the refusal and
  the retry in the log.

**Live only** (the short run 8 checklist, in session order):

Before the session
- [ ] Tell the server session how many people are playing. It redeploys if development has
  moved. Use a steady connection.
- [ ] Add Auril and Caer-Dineval: /dmbot names → Add many → upload
  dm-and-player-names.txt (#244)
- [ ] Bulk import, setup B of bakeoff-story-names-setup.md: "📥 Added 22 names · 4 already
  known · 3 look like known names · 2 kinds differ", the questions, then Undo takes all 22
  back (#425)
- [ ] The name card: easy to find from /dmbot names (#380); Fix spelling, Edit other
  names, Remove asks first, Undo brings it back (#223, #226)
- [ ] Uploads and links in Add many (#249, #389, #408, #414)
- [ ] Turn an optional rule on and off (#314, #338)
- [ ] Change how much DMbot says (#512)
- [ ] Make a backup as a player, then restore it (#241, #345)
- [ ] Someone who has never agreed before is ready to join voice

Joining and agreeing
- [ ] Everyone presses I consent before speaking. The first-time request says Deepgram will
  hear them; nothing is written down until they press Yes
- [ ] The DM screen's "who is recorded" list updates (#107)

During play
- [ ] /dmbot start typed inside the transcript channel works (#188)
- [ ] Lines appear a few seconds after speaking, one per speaker, no pop-ups, and the DM
  screen stays quiet (#194)
- [ ] Lines read "(speaker) {character}"; say "Bell Eros" with Belleros known: fixed in
  the line. Secret names never appear (#283)
- [ ] Mute at each dramatic pause; the first word after it is still written down (#121,
  #270)
- [ ] Someone agrees part-way through, then says one sentence at once: their first word is
  kept (#306)
- [ ] A player presses Stop recording me: their lines stop at once (#306)
- [ ] /consent revoke from the DM screen stops someone (#190)
- [ ] Use DMbot on a phone too (#284, #288, #302)

Ending
- [ ] Press Stop listening (#108): last words kept, then the summary, the download and
  Check new names (#202, #196, #184)
- [ ] The download's first lines name Deepgram, and its text stays as heard (#492, #283)
- [ ] Name fixes to check, "Did they mean…" and "Type it…" work (#456, #509, #525)
- [ ] "It's X" and "New name" on the new names (#405); the name card shows "Last heard"
  (#220)
- [ ] /transcript on a campaign played with transcripts off says "No transcripts yet"
  (#329)

For the server session (from the log afterwards, not at the table)
- Audio received equals audio expected, and matches the DM screen's audio % (#306, #276);
  pieces of speech per person
- Session saved and removed, with reasons (#293); "N opted out" (#329)
- Change and flag counts after stop (#410, #436); flags look as before (#477, #487)
- Workers and per-server queues (#309, #464); outage messages (#495, #505)
- Any "Deepgram refused N keyterms" and what came next (#262)
- "Listened N min, sent M min of speech" (#543)

## Record

One entry per run in `docs/testing-history.log`:

```
script: dm-and-player | dm-only   people: N (1 = DM only)   commit: <sha>
engine: whisper-local small (device/compute auto, beam 1) | cloud <model>
packet loss added: none | clumsy N%      Discord noise suppression: on/off
audio: received/expected = __/__ (__%)   pieces of speech: DM __, players __
part 1: __ wrong, __ missing, __ added (of 35 or 33, whisper not included)
part 2: __ of 12 right
whisper: all / part / missing (ring lit: y/n)
delay: about __ s from speaking to text in the DM screen
```

Each run is a different human reading, so compare engines over two or more runs each.
