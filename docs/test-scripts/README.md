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

Both scripts take about a minute and score the same 12 D&D terms.

**Recordings** (for the session twin, #299, and offline speech-to-text checks): `DMOnlyAudio.m4a`
(dm-only.md), `dm-and-player.m4a` (dm-and-player.md) and `stt-bakeoff.m4a` (stt-bakeoff.md, the
owner reading alone, 2026-10-07). The bake-off recording is the better test for name
resolution: 24 campaign names, each said several times, with 2-second pauses between lines.

**Sending it:** paste the script into a Discord message (it fits, and Discord shows the bold
labels) rather than sending the file.

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
