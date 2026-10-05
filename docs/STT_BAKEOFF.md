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
4. **Re-listen:** when a name is missed, does a second pass with a small, focused word
   list recover it, and how fast?
5. **Speed:** time from the end of speech to final text, and the cost of opening a
   connection (Speechmatics must reconnect to change its dictionary).
6. **Cost:** what's actually billed, including any minimum per connection.

## Who does what

| Who | Does |
|---|---|
| **Owner** | Creates the Speechmatics and Deepgram accounts and puts the keys **in the server's `.env` only** (never in chat, issues or the repo). Records the readings (below). |
| **Web session** | This plan, the script and word lists, and the bake-off tool on this branch, with offline tests for the scoring. |
| **PyCharm session** | Reviews the scoring and can re-score saved results offline. |
| **Server session** | Runs the bake-off on the server (US East, close to both US endpoints), writes the report and the testing-log entries. |

Keys (server `.env`, read by the tool, never printed or logged):
```
SPEECHMATICS_API_KEY=...
DEEPGRAM_API_KEY=...
```

## The recordings

Two readers (the owner and one friend) each read the bake-off script
([test-scripts/stt-bakeoff.md](test-scripts/stt-bakeoff.md)) once.

- **Record on your own device**, not through DMbot, so DMbot's rule that audio is never
  stored stays untouched. A phone voice-memo app or Audacity is fine. A quiet room, the
  same microphone and distance you use for games.
- **Leave about 2 seconds of silence between lines.** The tool cuts the recording at those
  silences and matches the pieces to the script in order, so don't skip or repeat lines.
  If you stumble, stop and say the whole line again after a pause; the tool will flag the
  extra piece for a quick check.
- Save as WAV or M4A, one file per reader, and copy them to the server into
  `~/bakeoff-audio/` (**outside the repo**; the repo is public). They're deleted when the
  bake-off is done.
- **Both readers agree** to the recordings being sent to Speechmatics and Deepgram for this
  test.

**Making it sound like Discord:** the tool converts every clip to what DMbot really sends:
Opus at Discord's voice bitrate (64 kbps), decoded to 16 kHz mono. So results reflect game
conditions, not studio audio. It also runs once on the clean audio, to see how much
Discord's compression costs.

## What gets tested

**Word lists** (from [the script's name list](test-scripts/stt-bakeoff.md#name-list)):

| List | Contents | Tests |
|---|---|---|
| **None** | No custom words | The raw engine |
| **Scene** | The ~30 names in the script | The normal case |
| **Scene + sounds like** | Scene, plus sounds-like forms for the names (Speechmatics only; Deepgram has no equivalent, so it gets Scene) | Question 3 |
| **Big** | Scene + ~270 made-up distractor names | Whether a "2–3 hops away" list dilutes accuracy |
| **Learned** | Scene + sounds-like forms taken from round 1's actual mistakes | DMbot learning from corrections |

**Setups:** Speechmatics Standard, Speechmatics Enhanced, Deepgram Nova-3, and Whisper
`small` (baseline, with the names as a prompt). Every setup × every list that applies ×
both readers. That's roughly 2.5 hours of audio in total, a few dollars, within both
free credits.

**Re-listen:** for every name a setup missed with the Scene list, re-send just that clip
with a focused list of at most 5 candidates (the right one plus the closest-sounding
others, with sounds-like forms for Speechmatics). It records whether the name was
recovered and the round-trip time, including opening the connection.

**How audio is sent:** in 20 ms chunks at real-time pace, the way live speech arrives. At
the end of each clip the tool tells the service the utterance is over (Speechmatics
`ForceEndOfUtterance` / `EndOfStream`, Deepgram `Finalize`). Timing runs three times, and
reports the median and the slowest 5%.

## Scoring

- **Normalizing:** the same rules as the read-aloud scripts. Ignore capitals, punctuation
  and hyphens; "it's" = "it is", "3" = "three". Accepted spellings are listed per name.
- **Name accuracy:** of the names actually said, the share written correctly.
- **False names:** names written where none was said (the trap lines and everyday lines).
  Weighted heavily: a wrong name is worse than a missed one.
- **Word error rate:** for everything, and for the everyday lines alone.
- **Confidence check:** of the wrongly written names, the share the service marked as
  low-confidence (below 0.7). The Transcript Cleaner relies on this to find doubtful words.
- **Speed:**
  - end of speech → final text (median and slowest 5%);
  - connection open, cold and with a cached dictionary;
  - re-listen round trip.
- **Cost:** audio seconds sent per setup, compared afterwards with each dashboard's billed
  amount to spot minimum charges per connection.

## Deciding

**Choose Speechmatics** (Enhanced or Standard) if all of these hold:
- its name accuracy with **Scene + sounds like** is within 2 points of Deepgram's with
  **Scene**, or better;
- its false names are no higher than Deepgram's;
- end of speech → final text is **1.0 s or less** for the slowest 5%;
- re-listen is **1.5 s or less** for the slowest 5%.

Otherwise **choose Deepgram**. If both qualify, the better name accuracy wins; if that's
within 1 point, the cheaper one wins. Standard vs Enhanced: pick Standard if it's within 2
points of Enhanced on names.

## Output

- **On the server only** (never committed): per-clip results with the text each service
  returned, in `~/bakeoff-results/`.
- **Committed:** `docs/STT_BAKEOFF_RESULTS.md` with numbers only (accuracy, speed, cost
  per setup), the decision, and an entry in `docs/testing-history.log`. Then #128 is
  updated, and the winner becomes a real `Transcriber` in core.

## The tool (to be built on this branch)

`tools/stt_bakeoff/`, kept out of DMbot itself; it uses core's existing dependencies
(`aiohttp`).
- `script.yaml`: the script's lines with expected names, accepted spellings and the
  category of each line.
- `vocab.yaml`: names, sounds-like forms and distractors for each list.
- `split.py`: converts each recording to Discord-like audio and cuts it at the pauses into
  numbered clips matching the script.
- `providers.py`: one small class per service (Speechmatics, Deepgram, Whisper) with the
  same `transcribe(clip, words) → words + confidence + timings` shape.
- `run.py`: runs setups × lists × clips, plus re-listen, and saves the results.
- `score.py` and `report.py`: the numbers above, and the results summary.
- Tests for the normalizing and scoring that run offline (no keys, no network).
