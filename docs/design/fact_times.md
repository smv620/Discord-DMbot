# When it happened and when it was learned

**Status:** proposed (Expert, 2026-11-27, for #5359; the owner's question of the same day). Not
binding until Supervisor rules and this merges. Worked through with GraphEngineer by message; what
it told me about the code is marked "(GraphEngineer)".

**In one line:** a fact about where someone was carries two times: when it happened in the story,
and when the table learned it. DMbot keeps both, never invents either, and works out "last known
in" from when it happened.

## 1. The problem

A DM is asked to confirm "Mara was last seen in Bryn Shander". The words claim something about
*now*, and by the time the DM answers, Mara may have moved. The party may also have only been
*told* about an old visit: on Day 10 they learn Mara was in Bryn Shander on Day 2. Today DMbot has
one time for that fact, the session it was said in, so an old visit learned late can look like the
newest.

**What exists:**

- PR #4571 (open, held for dev1's before-and-after run) already asks whether a **visit** happened
  and works out "last seen in" as the newest confirmed visit, ordered by session and line. That
  order is the order *learned*.
- `memory_relations` has `from_session_at`, `to_session_at`, `from_game_time` and `to_game_time`.
  Only quest time limits write the game-time pair (`memory/thread_parts.py`); a visit confirmed
  from a session claim sets `from_session_at` and no game time (GraphEngineer).
- The game clock is one number, minutes since Day 1 (`timebot/clock.py`). The clock's log gives
  the game minute of any spoken line (`timebot/stamp.py`, `game_time_at`), or "not known".
- A calendar gives that number words: "Day 12" or a Harptos date (`timebot/calendar.py`).
- A connection the players can see has **no knower rows**: shown means the party knows it. Knower
  rows are written only for held-back facts (a split party, the DM's pick of who knows, backstory,
  secrets), and carry only `created_at` (GraphEngineer).

## 2. The decision

### 2.1 Two times, on the fact

| Time | What it means | Where it is kept |
|---|---|---|
| **Happened** | when, in the story, the visit took place | new columns on the fact (2.2) |
| **Learned** | when the table learned it | `from_session_at` (as now) and a new `learned_game_time` on the fact |

My first draft put "learned" on the knower rows, because it differs per knower. That fails for the
common case: a shown fact has no knower rows. So the fact carries its own learned time, and a
knower row carries one only when it differs (a split party, someone told later; slice 6).

### 2.2 "Happened" is words first, numbers only when code can work them out

| Column on `memory_relations` | Holds |
|---|---|
| `when_words` | the story's own words, as said: "two days ago", "last winter", "" |
| `when_earliest`, `when_latest` | game minutes it happened between; either may be empty |
| `when_from` | how DMbot knows: `said` (a date was said), `worked_out` (counted from the clock), `seen` (it happened as it was learned), `dm` (the DM set it), or "" (not known) |
| `learned_game_time` | the game minute when it was learned; empty when the clock did not know |

- **Nothing happens after it was learned.** So `when_latest` is never later than
  `learned_game_time`. With no time words at all, that one bound is still true and still useful.
- **`from_game_time`** is set to `when_earliest` only when the day is known, so the "as it stood
  on Day N" lookups (`memory/recall.py`) stay right. Otherwise it stays empty, which every reader
  already takes as "not known".
- The columns are on every fact, but only visits use them in this design. A death, a hand-over
  or a promise can use them later, with its own ruling.

### 2.3 Seen or told

The reader says which, and code never guesses:

| The reader's `when` | Meaning | What DMbot does |
|---|---|---|
| `seen` | it happened as the table watched: the party went there, met them there | happened = learned |
| time words | a report of the past: "two days ago", "on Day 2", "last winter" | kept as said; turned into days when code can (2.4) |
| nothing | the lines give no time | happened is not known, but no later than learned |

- `seen` is the reader's judgement, so it is quote-checked like any reader field, and the DM can
  change it (GraphEngineer).
- A visit with **no time words keeps #4571's order** (by session and line). Only words that say
  "this was in the past" put a visit before the moment it was learned. Being held back needs
  evidence; otherwise nothing changes from #4571.

### 2.4 Code turns words into days, never the AI

A small, strict, pure parser, in the style of `timebot/phrases.py` and `timebot/durations.py`:

- "N days ago", "N weeks ago", "yesterday", "last night", "this morning", "today";
- "Day N", or a date of the campaign's own calendar.

When the clock knew the game minute at that line, the parser gives a day and `when_from` is
`worked_out`. The card says "about" and shows the words beside it. Anything else ("last winter",
"years ago", "the 2nd day of summer" in a campaign that counts plain days) stays as words, with no
number. With no clock at that line, everything stays as words.

### 2.5 How "last known in" is worked out

For one mover, over its confirmed visits, in this order:

1. **By when it happened**, where the two ranges do not overlap.
2. **Else by when it was learned** (#4571's order: session, then line; an upload's by when it was
   kept), but only between visits that are `seen` or have no time words.
3. **A told visit whose time DMbot cannot place** never ends or replaces another visit. It goes
   straight to history ("was in Targos, last winter"). If it is the mover's only visit, it is the
   last known, shown with its words.
4. **Two visits nothing orders** are both "was in", and neither is the newest (#4571's rule A).
5. An upload's visit has no learned game time; it ranks below any visit that has one
   (GraphEngineer).

Rule 3 is the owner's "never move someone back". Its cost: a true newer report with no usable time
("he was in Targos last week", no clock running) does not move the mover. The DM sees it on the
card and can set when (2.7). DMbot never moves someone on a guess.

Everything else in #4571 stays: a hidden visit never makes a visible one history, ❌ ends nothing
else, a place inside the other is one place.

### 2.6 What the DM is asked

Plain words, one question, the same three answers as #4571 (✅ it happened, ❌ it never happened,
🤫 it happened but keep it from the players):

| Case | The card |
|---|---|
| seen | 🗺️ **The party** went to **Bryn Shander** on **Day 10** (session of 15 Nov)? |
| a date said | 🗺️ **Mara** was in **Bryn Shander** on **Day 2**? The party learned it on Day 10 (session of 15 Nov). |
| worked out | 🗺️ **Mara** was in **Bryn Shander** about **Day 8** ("two days ago", said on Day 10)? |
| words only | 🗺️ **Mara** was in **Bryn Shander** **last winter**? Said in the session of 15 Nov. |
| no time | 🗺️ **Mara** was in **Bryn Shander** at some point? Learned in the session of 15 Nov. |

Days are shown in the campaign's calendar words. With no clock, the day parts are left out and the
session's date stands alone, as in #4571.

### 2.7 What the DM can change

One more button on a kept visit, on Discord's card and the website's: **📅 Change when**. It
offers a day, "as it was learned", or "not known". The answer is written with `when_from` `dm`,
and the mover's visits are put in order again from the start (slice 4). The DM's word outranks the
reader's and the parser's.

### 2.8 The words everywhere else

- **"Was last seen in" leaves the shown words.** Each visit reads "was in **X**" with its time.
- The worked-out line reads "**Last known in X** (Day 10)", or "(learned Day 10)" when only that
  is known. This is the owner's "was known to be in".
- The stored word stays `LAST_SEEN`, as #4571 keeps it, so no reader of the graph changes.

### 2.9 Who knows it

- "Last known in" is worked out from the visits a viewer may see. The players' line uses shown
  visits only; #4571's rule that a hidden visit never makes a visible one history already keeps
  that line right.
- A split party, or a character told later, learned it at another time. Those knower rows get
  their own `learned_session_at` and `learned_game_time` (slice 6), for
  [`party_knowledge.md`](party_knowledge.md)'s "who knows what". Empty means "as the fact says".

### 2.10 What the readers extract

- One optional field on a claim, `when`: the lines' own time words, and whether it was `seen`.
- It is stored in its own claims columns, never in `details`: `details` feeds the shown label and
  `knows_how`, so time words there would leak into what is shown (GraphEngineer).
- The words must be in the cited lines, or they are dropped (the guard every reader field has).
- The readers are told: give the words as said, never a date you worked out; say nothing when the
  lines say nothing. Lengths stay targets, never checks.
- The prompt version is bumped, so the sample is scored before and after.
- The lore reader gives the same field. A document's "two days ago" has no clock to count from,
  so it stays as words unless it names a day.

## 3. Rules to keep

1. DMbot never invents a time. A missing time is "not known", said plainly.
2. The AI gives words; only code gives a number, and only from the game clock.
3. Nothing is recorded as happening after it was learned.
4. A visit learned late never moves someone back, and a told visit DMbot cannot place never moves
   anyone at all.
5. The DM is asked whether a visit happened, never where someone is now.
6. "Last known in" is worked out. It is never asked and never typed in.
7. A hidden visit never changes what the players can see.
8. Everything is one campaign's: the new columns sit on rows that are already isolated, backed up
   and moved with the campaign.

## 4. Alternatives rejected

- **Only reword the question to "was known to be in".** The words improve, but an old visit
  learned late still becomes the newest.
- **One time, the game day it was said.** That is the learned time. It cannot tell "we saw her
  today" from "she was there last week".
- **Learned time only on knower rows.** A shown fact has none.
- **A separate table of sightings.** A visit is already a fact, with its confirm, Undo, hiding,
  backup and isolation. A second home for it would need all of those again.
- **Let the AI work out the day.** A wrong sum would be stored as a fact. The clock arithmetic is
  already in code and tested.
- **Ask the DM for a day on every visit.** One tap must stay one tap; the day is offered, never
  required.
- **Change #4571 first.** Its order is this design's second tier, unchanged. Holding it delays a
  fix that is already built.

## 5. Risks

- **The reader marks a told visit as `seen`.** It would move the mover. It is quote-checked, shown
  on the card, and the DM can change it. The sample key should hold a told visit to score this.
- **The clock is often not running**, above all in imported sessions (GraphEngineer: usually no
  clock row). Then nothing gets a number, and the design falls back to #4571's order plus rule 3.
  It is never worse than #4571.
- **Stored ends go stale** when a day changes. Ends are written on the rows, not worked out on
  read, and there is no settle-from-scratch today (GraphEngineer). Slice 4 adds one.
- **The prompt change moves other counts.** Scored before and after, as for every reader change.

## 6. Storage

- **`memory_relations`:** `when_words`, `when_earliest`, `when_latest`, `when_from`,
  `learned_game_time`.
- **`memory_claims`:** `when_said`, `when_seen`.
- **`memory_knowers`** (slice 6): `learned_session_at`, `learned_game_time`.
- Columns only, added with defaults, so the migration updates no rows and opens no row-level
  security. No new table, so nothing new for the isolation list, the campaign-move cascade or
  `campaigns/tables.py`.
- The backup reads and checks the new columns, and an older backup loads with them empty
  (`memory/backup.py`, as for `described` in #5173). The change log's column lists gain them
  (`memory/_changes.py`).

## 7. Slice order

1. **#4571 merges as it is.**
2. **Storage and the learned time.** The migration; `claims.confirm` writes `learned_game_time`
   from the claim's lines and the clock log; the card and the website show "learned …". No reader
   change, no change of order.
3. **The reader's `when`.** The claim columns, the instructions, the quote check, the pure parser,
   and the card's five wordings. Scored before and after.
4. **The order.** The happened tier in front of #4571's comparison, rule 3, and a
   `resettle(mover)` that clears the "moved" ends on a mover's visits and replays them.
5. **📅 Change when**, on Discord and the website together.
6. **Per-knower learned times**, with the party-knowledge slices.

## 8. Open questions

1. A campaign with its own calendar ("the 2nd day of summer"): today only plain days and Harptos
   exist, so such a date stays as words. Is a custom calendar wanted?
2. Should the DM's own card show a second line when a secret visit is newer than the shown one
   ("Last known to you: …")?
3. Which other kinds of fact should carry the two times next: a death, a hand-over, a promise?
