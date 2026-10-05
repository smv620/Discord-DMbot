# Read-aloud test scripts

Fixed scripts to read out loud during a live test (#119). Free talk at a D&D table has long,
natural pauses, and afterwards nobody knows exactly what was said. With a script we know the
words, who said them, and where the silences are. That lets us check two things:

- **Audio:** are packets really lost, or was someone just quiet?
- **Speech-to-text:** how well does Whisper (or a cloud service) write it down? Use the same
  script on every run so results can be compared across engines, models and settings.

| Script | Who reads | Length |
|---|---|---|
| [dm-and-player.md](dm-and-player.md) | Two people: one reads `[DM]`, the other `[Player]` | about 1 minute |
| [one-person.md](one-person.md) | One person reads it all | under 1 minute |

**Before each test:** decide whether you're testing with one person or several. Send each
reader the matching script; with more than two people, two of them read and the rest stay
quiet (or take turns on later runs).

Each script has two parts:

1. **Everyday words** any speech-to-text should get right. Mistakes here point to audio
   problems, not hard vocabulary.
2. **D&D words** that are harder: Ten-Towns names from *Icewind Dale: Rime of the
   Frostmaiden*, a spell, and a rules term.

Stage directions are in double brackets and are not read out:
- **(( dramatic pause ))**: about 3 seconds of silence. DMbot should count this as a pause,
  not lost audio.
- **(( whispering ))**: a quiet line, to see whether quiet speech is still heard and written
  down.

## Running the test

1. `/dmbot start`, and everyone reading presses **I consent** (or already has).
2. Read the script once, start to finish.
3. Wait for the next capture check in the DM screen (every 15 seconds), then `/dmbot stop`.
4. Copy the capture checks from the DM screen (they show what DMbot wrote down) and note:
   - which script, how many readers, and whether anything like "clumsy" added packet loss;
   - the speech-to-text engine and model (for example `whisper-local`, `small`).

## Scoring

**Audio:** every capture check should read 95–100% with no ⚠️. The dramatic pauses must not
lower the %. If they do, pauses are being counted as lost audio.

**Part 1 (everyday words):** count the words written down wrongly or missing. Expect 0–2.

**Part 2 (D&D words):** count how many of these came out right.

| Script | Words to check |
|---|---|
| DM and Player (13) | Bryn Shander, Ten-Towns, Targos, Easthaven, Detect Magic, Dexterity saving throw, frost giant, blizzard, Auril, Frostmaiden, longsword, Lonelywood, Caer-Dineval |
| One person (12) | Bryn Shander, Ten-Towns, Targos, Easthaven, Detect Magic, Dexterity saving throw, frost giant, blizzard, Auril, Frostmaiden, Lonelywood, Caer-Dineval |

Close spellings count as right only if a DM would read them the same way ("Bryn Shandar"
yes, "Brin Shonder" no).

**Whispered line:** written down, partly written down, or missing.

Record the scores in `docs/testing-history.log` with the run, so later runs (and engines) can
be compared.
