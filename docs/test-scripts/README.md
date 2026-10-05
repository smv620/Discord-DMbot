# Read-aloud test scripts

Fixed scripts to read out loud during a live test (#119). Free talk at a D&D table has long,
natural pauses, and afterwards nobody knows exactly what was said. With a script we know the
words, who said them, and where the silences are. That lets us check:

- **Audio:** are packets really lost, or was someone just quiet?
- **Speech-to-text:** how well does Whisper (or a cloud service) write it down? Use the same
  script on every run so results can be compared.

| People testing | Script | Who reads |
|---|---|---|
| 1 (DM only) | [dm-only.md](dm-only.md) | The DM reads everything, narrating the players' actions |
| 2 (DM + 1 player) | [dm-and-player.md](dm-and-player.md) | The DM reads `[DM]`, the player reads `[Player]` |
| 3 or more (DM + players) | [dm-and-player.md](dm-and-player.md) | The DM reads `[DM]`; the players take turns on the `[Player]` lines |

Both scripts take under a minute and score the same 12 D&D terms.

**Sending it:** paste the script into a Discord message (it fits, and Discord shows the bold
labels) rather than sending the file.

## What's in it

1. **Part 1, everyday words** (43 words in dm-and-player, 42 in dm-only). Any
   speech-to-text should get these right, so
   mistakes here point to audio problems, not hard vocabulary.
2. **Part 2, D&D words.** Ten-Towns names from *Icewind Dale: Rime of the Frostmaiden*, a god,
   a monster, a spell, a weapon and a rules term, with a pronunciation guide so every reader
   says them the same way.

Stage directions:
- **(( dramatic pause ))**, inside a line: 3 seconds of silence. DMbot ends a piece of
  speech after less than a second of silence, so each dramatic pause should **split that line
  into two pieces of speech**. Silence between pieces is never counted as lost audio. Short
  breaths inside a sentence are what DMbot counts as pauses (`pauses=` in the server logs).
- **(( whispering ))**: an everyday sentence, said quietly. It has no scored D&D words, so a
  miss is about quiet speech, not vocabulary. Note: Discord itself may not send a whisper
  (voice activity sensitivity, noise suppression such as Krisp). If the whisper is missing,
  check whether the reader's green speaking ring lit up.

## Running the test

1. **Before:** on the server, `DMBOT_DEBUG_AUDIO=1` in `.env` (ears logs one `audio …` line per
   piece of speech). Ask: how many people (1 = DM only)?
2. `/dmbot start`, and every reader presses **I consent** (or already has).
3. Read the script once, start to finish.
4. **Wait until the last line ("…Lonelywood and Caer-Dineval") shows up in a capture check**,
   or until two checks in a row add nothing. Only then do free talk or `/dmbot stop`:
   stopping drops lines that are still being written down.
5. Copy **all** the capture checks since the start. Each check lists text by speaker, not in
   the order it was read, and one reader's lines can be spread over several checks. (Text only
   shows if speech-to-text is on, that is `TRANSCRIBER` isn't `none`.)

## Scoring

Ignore capitals, punctuation and hyphens. "It's" = "it is", "3" = "three", "Ten Towns" =
"Ten-Towns".

- **Audio:** from the ears `audio …` lines in the server logs (more complete than the DM
  screen's %): add up `received` and `expected` for the script. 95–100% passes; 90–94% is
  borderline, so repeat once; under 90% fails (same as LIVE_TEST.md).
- **Pieces of speech:** dm-and-player: the DM about 6 (4 lines + 2 dramatic pauses), each
  player about 4 between them. dm-only: about 5 (2 paragraphs + 3 dramatic pauses). Many
  more, with no pause by the reader, hints at lost packets; fewer means a dramatic pause
  didn't come through as silence (Discord kept sending sound).
- **Part 1:** count words wrong, missing, and **added** (for example "Thanks for watching"
  during a pause). Out of 43 (dm-and-player) or 42 (dm-only).
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
- **Errors on the first word after a dramatic pause:** worth noting separately. If they bunch
  up there, the start of each piece of speech is being cut (an ears problem, not
  speech-to-text).

## Record

One entry per run in `docs/testing-history.log`:

```
script: dm-and-player | dm-only   people: N (1 = DM only)   commit: <sha>
engine: whisper-local small (device/compute auto, beam 1) | cloud <model>
packet loss added: none | clumsy N%      Discord noise suppression: on/off
audio: received/expected = __/__ (__%)   pieces of speech: DM __, players __
part 1: __ wrong, __ missing, __ added (of 43 or 42)
part 2: __ of 12 right
whisper: all / part / missing (ring lit: y/n)
delay: about __ s from speaking to text in the DM screen
```

Each run is a different human reading, so compare engines over two or more runs each.
