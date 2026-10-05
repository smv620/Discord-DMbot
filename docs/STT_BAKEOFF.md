# Speech-to-text bake-off: Speechmatics vs Deepgram (#128)

**Goal:** pick DMbot's default cloud speech-to-text service on measured results, not
marketing. The front-runner is **Speechmatics** (custom dictionary with "sounds like"
hints); the challenger is **Deepgram Nova-3** (keyterm prompting, word list swappable on an
open connection). Local Whisper `small`, already on the test server, runs as the baseline.

The questions to answer:
1. **Names:** which gets more campaign names right, with and without custom words?
2. **Traps:** which wrongly turns ordinary words into names less often?
3. **Learning:** how much do "sounds like" hints learned from earlier mistakes help
   (Speechmatics), compared with simply listing the words (Deepgram)?
4. **Re-listen:** when a name is missed, does a second pass with a small word list built
   from what was heard recover it, and how fast?
5. **Speed:** time from the end of speech to final text, and the cost of opening a
   connection (Speechmatics must reconnect to change its dictionary).
6. **Cost:** what's actually billed, including any minimum per connection.

## Who does what

| Who | Does |
|---|---|
| **Owner** | Creates the Speechmatics and Deepgram accounts and puts the keys **in the server's `.env` only** (never in chat, issues or the repo). Records the readings (below). |
| **Web session** | This plan, the script, and the tool (`dmbot.devtools.stt_bakeoff`), with offline tests. |
| **PyCharm session** | Reviews the scoring and can re-score saved results offline. |
| **Server session** | Runs the bake-off on the server (US East, close to both US endpoints), writes the report and the testing-log entries. |

Keys (server `.env`, read by the tool, never printed or logged):
```
SPEECHMATICS_API_KEY=...
DEEPGRAM_API_KEY=...
```
**Data settings:** Deepgram requests opt out of its model-improvement program
(`mip_opt_out=true`). In the Speechmatics account, check the data-retention setting and
turn off anything that keeps audio for training.

## The recordings

Readers each read the bake-off script
([test-scripts/stt-bakeoff.md](test-scripts/stt-bakeoff.md)) once. **Two readers is the
minimum; three or four are much better** (see "Deciding": each reader adds 57 names).

- **Record on your own device**, not through DMbot, so DMbot's rule that audio is never
  stored stays untouched. A phone voice-memo app or Audacity is fine. A quiet room, the
  same microphone and distance you use for games.
- **Leave about 2 seconds of silence between lines.** The tool cuts the recording at those
  silences and matches the pieces to the script in order, so don't skip lines. If you
  stumble, stop and say the whole line again after a pause; the tool lists the pieces so
  the extra one can be skipped.
- Save as WAV or M4A, **one file per reader, named `reader-a`, `reader-b`, …** (not real
  names: reader names appear in the report). Copy them to the server into
  `~/bakeoff-audio/`, **outside the repo** (the tool refuses folders inside it). They're
  deleted when the bake-off is done.
- **Every reader agrees** to the recordings being sent to Speechmatics and Deepgram for
  this test.

**Making it sound like Discord:** the tool converts every clip to what DMbot really sends:
Opus at Discord's voice bitrate (64 kbps), decoded to 16 kHz mono. It also runs once on the
clean audio, to see how much Discord's compression costs.

## What gets tested

**Word lists** (from [the script's name list](test-scripts/stt-bakeoff.md#name-list)):

| List | Contents | Tests |
|---|---|---|
| **None** | No custom words | The raw engine |
| **Scene** | The 26 names and 12 rules words in the script | The normal case |
| **Scene + sounds like** | Scene, plus hand-written sounds-like forms (Speechmatics only; Deepgram has no equivalent) | Question 3 |
| **Big** | Scene + 270 made-up distractor names, last | Whether a "2–3 hops away" list dilutes accuracy. Deepgram rejects long lists, so its list is cut (distractors only) until accepted; the report shows how many it took |
| **Learned** | Scene + sounds like, plus sounds-like forms taken from the **other** readers' actual mistakes with Speechmatics Enhanced | DMbot learning from corrections, without testing a reader on their own mistakes |

**Setups:** Speechmatics Standard and Enhanced, Deepgram Nova-3, and Whisper `small`
(baseline, with the names as a prompt). Optional: `sm-melia` (Speechmatics' newer, cheaper
model) if the account offers it in real time.

**How audio is sent:** in 20 ms chunks at real-time pace, the way live speech arrives.
At the end of each clip the tool says the utterance is over: Speechmatics
`ForceEndOfUtterance` then `EndOfStream`; Deepgram `Finalize` then `CloseStream`.
Speechmatics runs with `max_delay` 1.0 s.

**Timings, the same for both services:**
- **End of speech → final text:** from the end of the speech in the clip (not the end of
  the clip's trailing silence) until the last final text for that utterance arrived;
- **Connect:** opening the connection until it's ready for audio (for Speechmatics,
  including loading the dictionary);
- **Re-listen round trip:** opening the connection until the final text.

The **timing phase** runs one request at a time, so nothing competes: a discarded warm-up,
then 20 lines × 3 repeats with the Scene list (dictionary cached), then the same with a
one-off extra word that forces a new dictionary ("cold"; Speechmatics only, since Deepgram
caches nothing).

**Re-listen:** for every name a setup missed with the Scene list, re-send that clip at once
with a shortlist of 5 names, the ones closest in spelling to **what the service wrote**
(DMbot won't know the right name live). It records whether the right name made the
shortlist, whether it came back, and any new false names. **Controls:** trap lines 50,
53 and 56 are re-sent with shortlists matching their ordinary words ("bell or", "quill
on", "kale"), to see how often a shortlist forces a name onto a real word.

## Scoring

- **Normalizing:** the same rules as the read-aloud scripts. Ignore capitals, punctuation,
  hyphens and apostrophes; "it's" = "it is", "3" = "three", "OK" = "okay", "2d6" = "two
  d6". Accepted spellings are listed per name. A name split into pieces ("Ka Zeth") counts
  as **missed**: that's what would show in the transcript.
- **Name accuracy:** of the names actually said, the share written correctly.
- **False names:** names written where none was said (trap lines, everyday lines, and
  "Belleros" where the nickname "Bell" was said).
- **Word error rate:** for everything, and for the off-topic and everyday lines alone.
- **Confidence:** the share of missed names flagged below 0.7, **and** the share of
  correct names flagged. The Transcript Cleaner needs the first high and the second low.
- **Cost:** audio minutes per setup (Deepgram split by whether keyterms were used),
  compared afterwards with each dashboard's billed amount to spot minimum charges per
  connection.

## Deciding

**The sample is small:** 57 names per reader, only 26 different ones. A 2-point difference
is about two names. So names are compared **paired** (the same clips, where both services
returned a result) with a **95% interval** for the gap, resampled by name.

**Choose Speechmatics** (Scene + sounds like, against Deepgram with Scene) if:
- **names:** the interval's low end is above −2 points (it isn't clearly more than 2 points
  worse). If the interval is wider than ±4 points, names are **too close to call** and the
  decision rests on the other conditions;
- **false names:** it doesn't have 2 or more extra on the same clips;
- **speed:** end of speech → final text is **1.0 s or less** for the slowest 5% (timing
  phase, dictionary cached);
- **re-listen:** **1.5 s or less** for the slowest 5%.

Otherwise **choose Deepgram**. Nothing is decided (and the report says why) if more than 5%
of either side's requests failed, or the timing phase is missing. When both services
qualify, Speechmatics is chosen: it's cheaper at list price and its sounds-like hints are
what DMbot's learning builds on.

**Standard vs Enhanced:** Standard if, paired against Enhanced, its interval's low end is
above −2 points.

## Running it (server session)

The tool is in the core image (`dmbot.devtools.stt_bakeoff`; DMbot never imports it). Run
it in a one-off core container, with the recordings and results **outside the repo**:

```bash
cd ~/Discord-DMbot && git fetch && git checkout feat/stt-bakeoff && git pull
docker compose build core
mkdir -p ~/bakeoff-results
BAKEOFF="docker compose run --rm --no-deps \
  -v $HOME/bakeoff-audio:/bakeoff/audio:ro -v $HOME/bakeoff-results:/bakeoff/results \
  core python -m dmbot.devtools.stt_bakeoff"

$BAKEOFF check                                   # keys, network, dictionary format
$BAKEOFF split --audio /bakeoff/audio --out /bakeoff/results
$BAKEOFF run --out /bakeoff/results --phase main --limit 3   # quick trial
$BAKEOFF run --out /bakeoff/results --phase all
$BAKEOFF report --out /bakeoff/results > ~/bakeoff-results/report.md
```
- `split` prints one line per piece of speech if the count doesn't match the script. Fix it
  with `--min-silence` (default 1.2 s) or a `--map` file listing, for each reader, the line
  number of each piece (`null` skips a stumble).
- `run` can be stopped and started again; finished requests are skipped and failed ones
  retried. If Speechmatics reports `quota_exceeded`, lower `--concurrency` (default 2).
- The report has numbers only. Review it, copy it to `docs/STT_BAKEOFF_RESULTS.md`, and
  add a `docs/testing-history.log` entry. Per-clip results and the recordings stay in
  `~/bakeoff-results` and `~/bakeoff-audio` and are deleted afterwards.

Expected size: about 2–2.5 hours of audio in total, roughly $1–5 at list price (more if a
service bills a minimum per connection), within both free credits.

## Afterwards

#128 records the decision, and the winner becomes a real `Transcriber` in core, with
the vocabulary interface from the plan.
