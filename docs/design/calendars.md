# A campaign's own calendar

**Status:** binding. The owner said yes to custom calendars on 2026-11-27; Supervisor accepted the
Expert's design note on #5364 the same day and answered its open questions (section 9).

**In one line:** game time stays one number, and a calendar only gives that number words. A DM can
describe their world's months, seasons and festivals once, and every date DMbot shows or reads
uses them.

## 1. The problem

"The party learns on the 10th day of summer that Mara was in Bryn Shander on the 2nd day of
summer." DMbot cannot count that today:

- `timebot/calendar.py` knows two calendars: plain days ("Day 12") and Harptos.
- The only parser that reads a named date is Harptos-only and sits inside the quest time limits
  (`memory/time_limits.py`, `_harptos_day`).
- Homebrew settings and other published worlds name their own seasons, months, weekdays and
  festivals. Until a calendar exists, their dates stay as words
  ([`fact_times.md`](fact_times.md), 2.4), which loses nothing but orders nothing.

## 2. The rule everything rests on

**Game time is minutes since Day 1 (`timebot/clock.py`). A calendar only gives that number words,
and reads words back into it.** No calendar date is ever stored on a fact, a time limit or a
recap. So a DM can set up or change a calendar at any time and nothing is lost or re-dated: every
stored time is re-worded.

## 3. One shape for every calendar

| Field | Holds |
|---|---|
| `name` | the calendar's name |
| `parts` | the year in order; each has a name, a number of days, and whether it is a month, a season or a festival |
| `leap` | optional: one extra day every N years, its name, and the part it follows |
| `weekdays` | optional: the names in order |
| `year_label` | "AR", or empty |
| Day 1 | a year, a day of that year, and (with weekdays) its weekday |

- A festival is a part that belongs to no month. It may last one day or several.
- A calendar is months **or** seasons. "Four seasons of N days" is this shape with four parts.
- Harptos is this shape too, with its leap day (Shieldmeet, every 4 years, after Midsummer).
- Weeks run on through every day, festivals included. They never pause.
- **Limits**, so a mistake stays small: at most 40 parts, a year of at most 1,000 days, names of
  at most 30 characters, no two names alike (whatever their capitals), no name that is only a
  number.

## 4. Storage

- One new column on `game_clocks`, `calendar_def` (JSONB), beside `calendar_kind`,
  `calendar_year` and `calendar_ordinal`, which already hold the kind and Day 1.
  `calendar_kind` gains `custom`.
- Campaigns on `days` and `harptos` are untouched. No row is rewritten, so the migration opens no
  row-level security.
- No new table, so nothing new for the isolation list or the campaign-move cascade.
- The clock's backup reads and checks the column, and an older backup loads without it.
- The website's clock door returns the definition with the kind (the function of migration 0516).

## 5. Code

- **`timebot/calendar.py`** gains a pure `Layout` (the definition, checked) and `Calendar`
  carries one. Printing and `day_number_of` work from the layout.
- **Harptos becomes a built-in layout.** A golden test shows every date prints exactly as it does
  today, so nothing users see changes.
- **One date parser, in timebot,** built from the layout's own names. It replaces the Harptos-only
  one in `memory/time_limits.py`; quest limits and fact_times both call it. It reads:
  - "the 2nd day of Summer", "the 2nd of Summer", "Summer 2nd", "Summer 2";
  - a festival's name alone;
  - "Day N", in every calendar.
- **A date with no year needs a direction,** given by the caller:
  - a time limit means its next coming, as today;
  - fact_times' "when it happened" means its last coming at or before the moment it was learned.
- **The strict rules stay:** two dates in the words are none; a name the calendar does not have is
  none; nothing before Day 1. A month or season name needs a day number or "day of" beside it, so
  a bare "Autumn" is never read as a date; only a festival's name stands alone.
- **Printing** follows today's Harptos form: "Summer 2nd, 1201 AR", or "Midwinter, 1201 AR" for a
  festival day. The weekday is shown only where the clock is shown in full.

## 6. Where it shows

- The game clock, quest time limits, the recap's dates and fact_times' two times all print through
  `Calendar.words`, so they change together.
- **The names of months, seasons, festivals and weekdays are never offered as new names.** They
  join the names the name finders leave out (`memory/scan.py`, `memory/name_reader.py`).
- **Settings, in Discord and on the website together:** pick Days, Harptos, Four seasons or "My
  own"; a few fields at a time; a preview line such as "Day 1 is Summer 2nd, 1201 AR" before
  saving.

**Changing a calendar later:**

- Every shown date is re-worded at once.
- A day that was counted from words under the old calendar stays on that day. The settings page
  says so in one line: "Dates DMbot already counted stay on their day."
- The story's own words (`when_words` in fact_times) are never rewritten.

## 7. Rules to keep

1. Only minutes are stored; a calendar date never is.
2. Code reads and prints dates. The AI gives the words as said and never counts a date.
3. A date DMbot cannot read stays as words. Nothing is guessed.
4. A calendar is one campaign's. It goes in that campaign's backup and moves with it. Sharing a
   calendar between campaigns is not part of this design.
5. The repository ships only Days, Harptos (as today) and the plain four seasons. Any other
   published world's calendar is typed in by the DM, never shipped.
6. Harptos prints and reads exactly as before.

## 8. Alternatives rejected

- **A table of calendar parts.** More rows, policies and backup work for a list of at most 40
  short names that is always read whole.
- **A calendar kind in code for each published world.** Every new world would need a release; the
  one shape covers them.
- **Let the AI turn "the 2nd of Summer" into a day.** A wrong sum would be stored as a fact; the
  arithmetic is small and testable.
- **Store dates as year, part and day.** Changing the calendar would then re-date or break every
  stored time.

**Risks:**

- A month name that is also an ordinary word or a character's name. The parser's "needs a day
  number" rule covers it (section 5).
- Harptos changing by accident when it moves onto the layout. The golden test guards it.

## 9. Questions answered (Supervisor, #5364, 2026-11-27)

1. **Months and seasons together** (seasons as names over ranges of months): later, as its own
   issue, only if a DM asks.
2. **Leap rules beyond "one extra day every N years":** no.
3. **Weeks pausing on festival days:** no; weeks never pause.

## 10. Slice order

1. **Pure code:** `Layout`, its checks, printing, `day_number_of`, the one parser with its
   direction, Harptos as a layout with the golden test. No storage, nothing visible.
2. **Storage:** the column, the kind, the store's read and write, the backup, the website's door.
3. **Settings,** Discord and website together, with the preview, the ready-made four seasons and a
   What's new entry.
4. **Callers:** quest limits use the shared parser; the calendar's names join the name finders'
   leave-out list.
5. **fact_times slice 3** calls the parser with "last coming". fact_times slice 2 waits for none
   of this.

**Who builds:** CloudDev-lead builds slices 1, 2 and 4; CloudDev-lead and WebDev-lead build slice 3
together; GraphEngineer builds slice 5 inside fact_times slice 3. Each PR is checked with the
Expert before it opens.
