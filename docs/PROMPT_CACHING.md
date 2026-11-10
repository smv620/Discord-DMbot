# Prompt caching (#4054)

The AI service can remember the front of a prompt for a few minutes. Reading that remembered
part again costs a tenth of a normal input token; writing it the first time costs 1.25 times.
So it pays when the same front is sent two or more times within minutes, and costs a little extra
when it is sent once. This page says where DMbot uses it, where it does not and why, what it saved,
and how to measure it again. Counts only: no text, no names, no campaign.

## How it works in the code
- `ai.py`: `cache_system=True` on a client marks the **system** text `cache_control: ephemeral`.
  The service ignores the mark below its minimum length (it then answers as usual).
- Every call logs one line with `in`, `out`, `cache_read` and `cache_write` (token counts).
- The spend watch counts a write at 1.25 and a read at 0.1 of an input token
  (`ai.py`, "the billed count"; `story_plan.micro_usd` does the same in dollars).
- A cache is keyed by the **exact** prompt prefix, so it can only be shared by calls of one
  campaign that send the same words. A prefix never holds two campaigns' text.

## Where it is used
| Caller (feature) | What is cached | Why it pays |
|---|---|---|
| Names from a document (`NAMES`) | the instructions | one call for each 40,000-character piece, same instructions |
| Names from a campaign background (`NAMES_BACKGROUND`, `NAMES_BACKGROUND_READ`) | the instructions | the same read calls, piece by piece, and its retries |
| Story plan from an adventure (`PLAN_READER`) | the instructions | one call for each part of the adventure |
| Name pass on a finished transcript (`NAME_PASS`) | the instructions | one call for each batch |
| House rules from a file (`HOUSE_RULES`) | the instructions | one call for each piece of the file |
| A session recast as a script (`THEATRE`) | the instructions | one call for each scene |
| Story extractor (`EXTRACTOR`) | the instructions | already cached before #4054 |

Pieces read side by side do not hit the cache until the first has written it. A document with one
piece pays the 1.25 write and never reads it back: a small loss, the price of the many-piece wins.

## Where it is not used, and why
- **Table answers, the sidebar, FightBot, time suggestions:** short prompts, one call each, and
  live; a write would cost 1.25 for no reads.
- **A player's sheet (`SHEET`):** one call for each sheet.
- **Recaps:** the transcript is the shared prefix of about a dozen calls. This needs the
  transcript sent first, as its own cached block, so it is a second change (`cache_prefix`) with the
  recap score before and after (CLAUDE.md, "A recap-writer change is scored before and after").
  Not in this change yet.

## Before and after
To be filled from dev1's runs (counts only; commit and date of each run).

| Run | Commit | Date | Input | Cache write | Cache read | Output | Cache share of input | Dollars | Saved |
|---|---|---|---|---|---|---|---|---|---|
| Lore upload, before | | | | | | | | | |
| Lore upload, after | | | | | | | | | |
| Recap, before | | | | | | | | | |
| Recap, after | | | | | | | | | |

Recap score (`recap_run --score`), before and after, on the fixed set: must-haves covered, missed,
not given, and the fact check. To be filled when the recap change is made.

## How to measure it again
1. Run the same upload (or `recap_run` on the fixed set) on the server.
2. Add up the `AI call:` log lines of the run: `in`, `cache_write`, `cache_read`, `out`.
   `cache share = cache_read / (in + cache_write + cache_read)`.
3. Dollars = (`in` + 1.25 x `cache_write` + 0.1 x `cache_read`) x the tier's input price + `out` x
   its output price (`story_plan.micro_usd` has the prices). Saved = 1 - dollars / the same tokens
   with no cache.
4. Put the row in the table above with the commit and the date.
