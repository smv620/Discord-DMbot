# Design basis: quests and story threads

**Status:** revision 1, 2026-11-15, on the owner's direction (brief and analysis on issue #4448).
**Not binding until Supervisor approves it.**

**Who builds it:** GraphEngineer owns the threads, their readers and the checks
(`core/src/dmbot/memory/`). WebDev builds the website's halves. Each slice says whose it is.

**Read with:** issue #4448; `docs/STORY_MEMORY.md` sections 2, 3, 9, 9a and 9b;
`docs/graph_design_basis.md` sections 8 to 10; `docs/design/mapbot_layers.md` (the same storage
pattern and the same rule for getting the DM's attention).

## Contents
1. [Problem and context](#1-problem-and-context)
2. [The decision](#2-the-decision)
3. [What a thread is made of](#3-what-a-thread-is-made-of)
4. [The `thread_parts` row](#4-the-thread_parts-row)
5. [The parts, one by one](#5-the-parts-one-by-one)
6. [Who is doing it: the party without an entry](#6-who-is-doing-it-the-party-without-an-entry)
7. [Mysteries, clues and "solved so far"](#7-mysteries-clues-and-solved-so-far)
8. [Time limits](#8-time-limits)
9. [How threads get into memory: the readers](#9-how-threads-get-into-memory-the-readers)
10. [What the DM sees, and what players see](#10-what-the-dm-sees-and-what-players-see)
11. [Never](#11-never)
12. [Alternatives rejected](#12-alternatives-rejected)
13. [Rules developers must keep](#13-rules-developers-must-keep)
14. [Slice order](#14-slice-order)
15. [The four questions, answered](#15-the-four-questions-answered)
16. [Open questions](#16-open-questions)

---

## 1. Problem and context

The owner wants DMbot to keep the campaign's quests in memory without the DM building each one
by hand:

1. **Quest threads are found** in lore and in sessions, as suggestions the DM keeps.
2. **They are tagged for the Story Graph** ("Cluster by quest" and the kind chips), for the DM.
3. **Each part is tracked:** mysteries, discoveries (clues), progression (objectives), complications,
   promises and rewards.
4. **Time limits,** soft and hard, are measured against the in-game calendar, shown on the story
   timeline, and flagged privately to the DM.

**What exists today** (GraphEngineer's analysis on #4448):

- **The kinds are there:** `thread` with the subkinds `quest`, `mystery`, `hook`, `goal` and
  `promise`, and `clue` (`core/src/dmbot/memory/ontology.py`). All are hidden from players
  (`PLAYER_HIDDEN_TYPES`).
- **The connections are there:** `in_thread` (the part someone plays is its detail), `advances`,
  `resolves`, `led_to`, `promised_by`, `promised_to`, and `knows` for clues.
- **A thread's state** (hinted, open, active, resolved, abandoned) is kept in `memory_states`, one
  current at a time. "Stalled" is worked out, never stored (`memory/threads.py`).
- **Only the DM writes any of it.** `MemoryStore.new_thread` refuses every other source, and no
  reader proposes a thread, a part, a clue or a promise. So the graph's quest clusters
  (`0284_web_node_quests`) are empty unless the DM made everything by hand.
- **Missing:** objectives, complications, a real due date (it is a free-text note, never read as a
  date), and any measure of how far a mystery has come.
- **Built and switched off:** the readings (`memory/readings.py`: a theory, a reveal, a frame-up
  as beliefs) and PlotBot's recap check (`plot_check.py`).
- **The game clock and calendar are there:** one number of game minutes, a log of every change,
  and a calendar that turns a minute into "Day 12" or a Harptos date
  (`core/src/dmbot/timebot/`).

---

## 2. The decision

1. **A thread stays an entry in the graph, and its parts become rows beside it.** Quests, mysteries,
   hooks, goals, promises and clues are already entries with connections, and stay so. The small
   things inside a thread (an objective, a complication, a reward, a time limit) are rows in one new
   table, `thread_parts`, the way a place's layer facts are rows in `map_facts`.
2. **The readers propose; only the DM keeps.** The lore reader and the session reader suggest
   threads and parts. Nothing a reader finds is added by itself, however sure it is. This is the
   existing hard rule: PlotBot records only what the DM confirmed.
3. **The party is never an entry and never stored.** A thread is the party's unless it names
   someone else (section 6).
4. **A time limit is a part with its own words and, when DMbot can work one out, a game date.**
   It is measured against the campaign's one game clock, and only there.
5. **DMbot counts; it never judges.** A mystery's progress is a count of clues found. Whether a
   guess is right, whether a deadline's passing fails the quest, and whether a promise was broken
   are the DM's to say.
6. **Everything here is DM-screen content.** Players see a thread only when the DM shows it, and a
   part only when the table knows it.

**Why:**

- **Entries for threads** keeps what already works: states, "stalled", the Threads list, the graph
  clusters and the pre-session note all read thread entries today.
- **Rows for parts,** because parts are many, mostly nameless and belong to exactly one thread. As
  entries they would crowd the names list, be matched as names when heard ("find the key"), and
  be drawn as dots on the Story Graph.
- **One table for all four kinds of part,** because everything around the words is the same:
  DMbot's read against the DM's word, history, Undo, the backup, isolation and who may see it.

---

## 3. What a thread is made of

| The owner's word | What it is | Where it lives | New? |
|---|---|---|---|
| **Quest, mystery, hook, goal, promise** | a thread entry of that subkind | `memory_entities` | no |
| **Where it stands** | the thread's state, one at a time, with history | `memory_states` | no |
| **Who is in it** (giver, target, villain, victim) | `in_thread`, the part as its detail | `memory_relations` | one new detail word: "takes it on" |
| **What moved it** | an event that `advances` or `resolves` it; what `led_to` it | `memory_relations` | no |
| **Progression** | objectives and sub-steps, each with a state | `thread_parts` | **yes** |
| **Complications** | a setback, twist or new obstacle, each with a state | `thread_parts` | **yes** |
| **Rewards** | what was offered, by whom, and whether it was paid | `thread_parts` | **yes** |
| **Promises** | a `promise` thread with `promised_by` and `promised_to` | as today | no |
| **Time limits** | soft or hard, with words and a game date when known | `thread_parts` | **yes** |
| **Clues** | a `clue` entry | `memory_entities` | no |
| **Discoveries** | who knows a clue, and since when | `knows` and the knower lists | no |
| **What a clue bears on** | a clue is a clue to a thread | `memory_relations` | **one new connection, `clue_to`** |
| **The table's guesses** | a theory held by the party or one character | beliefs (`readings.py`) | no (switched on in slice 7) |

**New in the ontology** (a reviewed code release, as its rules require):

- **`clue_to`** (clue → thread; reads "is a clue to", read back "has the clue"). Its detail is one
  of two words: "points to it" (the default) or "false lead". It is the DM's word or a kept read.
  `led_to` stays for what it means: one thing sent the party on to another.
- **"takes it on"** joins the suggested parts of `in_thread` (`threads.ROLES`).
- Nothing else. No `gives_quest`, `hired`, `seeks` or `wants`: `in_thread` with its part already
  says who gave it and who it is aimed at.

---

## 4. The `thread_parts` row

One row is one part of one thread.

| Field | Meaning |
|---|---|
| campaign scope, `id` | as every memory table |
| thread | the thread entry it belongs to |
| `kind` | `objective`, `complication`, `reward` or `time_limit` |
| words | what it is, in the source's or the DM's words: "find where Sephek sleeps", "100 gold" |
| under | another objective this one is a sub-step of. Objectives only, one level deep |
| place in the list | a number for the DM's order. Objectives only |
| `state` | one word from that kind's short list (section 5) |
| points at | an optional entry: the event that brought a complication or met an objective; the item that is the reward; who offers the reward |
| limit fields | time limits only: soft or hard, the game minute it falls due, and where that minute came from (section 8) |
| story time | `from_session_at`, `to_session_at` and game minutes, as connections carry them |
| `status` | `read` (DMbot's suggestion), `confirmed` (the DM kept or wrote it) or `rejected` |
| `source` | `dm`, `lore` or `session`, with the evidence it was read from, as claims keep it |
| `table_knows` | true when the table has heard it (section 10) |

**How a part changes** (the history rule ruled for route condition, #4258):

- **A change in the story closes the old row at that story time and opens a new one.** An
  objective met, a deadline moved, a reward paid: each is a new row, and the old one stays as
  history.
- **"That was wrong" rejects the row.** It never becomes history.
- **One open row** per thread, kind and words.

**Storage rules:** per campaign with forced row-level security; foreign keys that carry `guild_id`
are `ON UPDATE CASCADE`; listed in `campaigns/tables.py` and in the backup; every write goes through
`memory_changes`, so one keep, one upload or one session's suggestions is one Undo. Deleting a
thread deletes its parts. Merging two threads moves the parts to the kept one, and two that say the
same thing become one.

---

## 5. The parts, one by one

| Kind | States | Notes |
|---|---|---|
| **Objective** | to do · done · failed · no longer needed | Order and one level of sub-steps. Meeting every objective does **not** finish the thread: only the DM changes a thread's state |
| **Complication** | in the way · overcome · no longer matters | A setback, a twist, a new obstacle. It may point at the event that brought it |
| **Reward** | offered · paid · not paid · turned down | Words, who offers it, and the item entry when the reward is a named thing. Any kind counts: gold, goods, a favour, a privilege, standing ("100 gold", "a room whenever you pass", "the town's goodwill") |
| **Time limit** | running · met · passed · lifted | Section 8. "Passed" is worked out from the clock, never stored |

- **A reward is not a promise thread.** It is what a quest's giver offers for the job, and it
  lives and ends with the quest. A free-standing promise ("I'll bring the healer back") stays a
  `promise` thread, which can now have objectives and a time limit like any thread.
- **The existing "reward" part of `in_thread`** stays readable. New rewards are written as parts,
  which can hold "100 gold" without making an entry for it.
- **A complication is not an event.** The event that caused it is an entry and the part points at
  it. That keeps "the bridge collapsed" (something that happened) apart from "the road north is
  cut" (what is now in the way).

---

## 6. Who is doing it: the party without an entry

The party is never an entry (owner's ruling, #3157), so a thread can't point at it.

- **A thread is the party's by default.** Nothing is stored to say so.
- **A thread is someone else's when it says so:** an `in_thread` connection with the part "takes
  it on", from a character or a group. That covers a personal quest (one player character), a
  rival party's quest, and a villain's goal.
- **"The party was hired by Hlin" is a quest,** not a connection: a quest thread whose giver is
  Hlin (`in_thread`, "quest giver") and whose target is Sephek (`in_thread`, "target"). Whether
  the party took the job is the thread's state: "Not started" while it is only offered, "Happening
  now" once they are on it.
- **Who "the party" is at a moment** is already known elsewhere: the campaign's kept player
  characters, and the game clock's groups while the party is split. A thread doesn't copy it.
- **The sample keys change to match:** the "party works for Hlin" rows become one quest row with
  its giver, target and reward, and the offer in the Lucky Liar is the event that `led_to` it.

---

## 7. Mysteries, clues and "solved so far"

A mystery is a thread whose name is the question ("Who is killing the Ten-Towners?").

**What is stored:**

- **Clues tied to it:** `clue_to` from each clue entry, marked "points to it" or "false lead".
- **Who has found each clue:** the knower lists, as today. A clue only some people know keeps its
  `audience_limited` switch.
- **The table's guesses:** beliefs held by the party or by one character (`readings.py`: a theory
  goes to the speaker's point of view, and to the party's only when the whole table agreed).
- **The DM's answer,** when the DM chooses to write it: in the thread's DM-only notes, or as a
  secret connection in the DM's own view. DMbot never writes it.

**"Solved so far" is a count, worked out when shown and never stored:**

| What the DM tied to the mystery | What DMbot shows |
|---|---|
| clues, some found by the party | "4 of 7 clues found. Not yet found: 3." |
| clues, one marked a false lead | "4 of 7 clues found (1 is a false lead)." |
| nothing listed beyond what was found in play | "4 clues found so far." Never a fraction: DMbot doesn't know the total and won't invent one |
| the table has voiced a guess | "The table's guess: the speaker's bodyguard." beside it, with who holds it |

**What DMbot never does:**

- say a guess is right, wrong, warm or cold, to anyone;
- compare the table's guess with the DM's answer and give a verdict. The DM sees both side by side
  and draws the conclusion;
- mark a mystery solved. The DM sets "Finished", usually with the event that `resolves` it;
- tell a player how many clues exist. A player sees only the clues their own characters know, with
  no total (section 10).

---

## 8. Time limits

### 8.1 What a time limit holds
- **Soft or hard.** Hard: something is lost when it passes ("by the new moon", "in three days").
  Soft: things get worse or harder ("before winter closes the pass").
- **The words,** always, exactly as said or written.
- **A game date,** only when one can be worked out, stored as a game minute with where it came
  from: `read` (DMbot worked it out) or `dm` (the DM set it).

### 8.2 How DMbot works out the date
| The words | What DMbot does |
|---|---|
| a length of time from now ("in three days", "by tomorrow's dusk") said in a session | adds it to the game time of the line it was said on, and offers the date as its read |
| a calendar date ("by the 14th of Flamerule") | reads it on the campaign's calendar, when the campaign uses one |
| something the calendar doesn't hold ("by the new moon", "before the thaw", "when the ritual ends") | keeps the words only, and offers the DM one tap to set a date |
| a length of time in lore, with no moment to count from | keeps the words only |

- **The in-game calendar is the only source of dates.** Nothing is ever read as a real-world date.
- **Weather and seasons are never stored.** "Before winter closes the pass" is words until the DM
  gives it a date.
- **A campaign with no game clock** keeps the words and counts nothing. DMbot says so once on the
  thread: "No game clock here, so I can't count this down."
- **While the party is split,** the limit is measured against the group furthest ahead in time.

### 8.3 How it relates to the timeline, the calendar and "stalled"
- **The calendar** gives a dated limit its words ("due Flamerule 14th") and "3 days left".
- **"Stalled" is a different measure and stays as it is:** real sessions in which nothing touched
  the thread. A time limit counts game time; "stalled" counts sessions at the table. Neither
  changes the other. A thread that is both stalled and running out of time is listed first.
- **The story timeline** shows a dated limit as a marker at its game date, after the latest
  session when it is still to come (section 10).
- **The DM's own "Due soon" state** for a promise stays the DM's to set.

### 8.4 Telling the DM
The owner's rule for getting the DM's attention applies (PLAN, "How DMbot gets the DM's
attention"): a private message from DMbot, one button at most, never a post on the DM screen.

- **Nearing:** one message when the game clock passes the "soon" mark: one game day before the
  limit, or halfway to it when the limit was set less than two days ahead.
  "⏳ Hlin's contract: 1 day left (due Hammer 12th). [Open]"
- **Passed:** one message when the clock passes the limit.
  "⏳ Hlin's contract: the time limit passed (Hammer 12th). Nothing has changed; your call. [Open]"
- **Once each,** for each limit. A clock set back and forward again doesn't repeat them.
- **A limit with words only** is never counted and never "passed". It appears in the pre-session
  note as a reminder.
- **The pre-session note** gains one line, "⏳ Time limits:", beside "🧵 Quiet threads" and
  "🤝 Due soon" (`memory/presession.py`): the nearest first, then the worded ones.
- **A passed limit changes nothing by itself.** The thread's state, its objectives and its reward
  stay as they are until the DM changes them.

---

## 9. How threads get into memory: the readers

### 9.1 What a reader suggests
One suggestion is a small bundle the DM keeps or turns down as a whole, with each line able to be
switched off:

- the thread: its kind, and a name built by code from its parts ("Hlin's contract: Sephek
  Kaltro"), which the DM can change;
- who gave it, who it is aimed at, and who takes it on when that isn't the party;
- its objectives, its reward and its time limit, in the text's own words;
- what led to it (the event or clue);
- the lines or passage it was read from.

### 9.2 What counts as a quest (owner, 2026-11-15)
**A quest is recognised by its shape, never by the word.** Nobody has to say "quest", "job" or
"mission". The shape is **an ask and a taker**, usually with something in exchange:

- **The ask:** someone wants something done, found, brought, protected or found out. "My brother
  went up to the cairn two nights ago and hasn't come back."
- **The taker:** someone agrees, or sets out to do it. "We'll look for him."
- **The exchange,** when there is one, of any kind:
  - **gold or goods** ("there's 50 gold in it", "take a horse from my stable");
  - **a favour or a privilege** ("you'll have a room here whenever you pass", "I'll get you in to
    see the Speaker");
  - **standing** ("the town won't forget this").

So a short exchange at the table, agreeing to search for a lost person in return for a meal and
the innkeeper's good word, is a quest suggestion with a giver (the innkeeper), a target (the lost
person), one objective ("find him") and a reward ("a meal and her good word").

**How sure DMbot must be:**

- **An ask and a taker make a quest suggestion,** started as "Happening now".
- **An ask with no taker yet makes a hook suggestion** ("a lead that could become a quest"),
  started as "Not started". If the table takes it up later, the session reader suggests the
  change, not a second thread.
- **A reward is never required.** Many quests are taken for nothing.
- **A reward of standing is only words on the reward part.** It never changes a reputation or an
  attitude by itself: those stay the DM's to set (STORY_MEMORY section 5).
- **Not a quest:** a plan the party makes with nobody asking ("let's head north tomorrow"), an
  errand done within the same scene ("pass me that rope"), a threat, or a wish nobody acts on. A
  party's own aim that lasts ("we want to clear our names") is a `goal` suggestion.
- **When DMbot can't tell,** it suggests nothing. A missed quest costs the DM one tap to add; a
  flood of false ones costs the DM's trust in the list.

### 9.3 The lore reader
- It suggests a thread only when the text has the shape in section 9.2: **an ask, and at least
  one of** who wants it, who it is aimed at, or what is offered for it. A plan, a rumour with no ask,
  or a motive DMbot would have to guess makes nothing.
- A published adventure the DM shared states its quests, clues and time limits outright. Those are
  read the same way, under the DM's existing right-to-use confirmation, and start DM-only.

### 9.4 The session reader
After a session, never during play, it suggests:

- **a new thread,** when the table's talk has the shape in section 9.2, or raises a question the
  party sets out to answer (a mystery);
- **progress on a thread the campaign has:** an objective met or failed, a complication, a reward
  paid or refused, a state change, a clue found (who now knows it), a time limit stated or moved.

It is given the campaign's open threads and their open parts, so progress is attached to the thread
it belongs to. Code, not the AI, decides whether a suggestion is a new thread or the one already
there (same giver and target, or the same name).

### 9.5 Rules for both
- **A reader writes `read`, never `confirmed`.** `MemoryStore.new_thread` and the thread writers
  learn one new source for suggestions; the DM's keep is still the only way to a confirmed row.
- **Nothing a reader suggests is added by itself,** including what lore uploads otherwise add as
  "sure".
- **The model says words; code says numbers** (the rule in `readings.py`): the AI returns the
  words of a time limit, and code works out the game minute.
- **The text is data.** Words in lore or a transcript that read like an instruction to DMbot are
  refused, as the readers already do.
- **Lengths are targets, never checks,** and the generous output limits apply.
- **Reading is metered AI use,** and every wait shows progress.

### 9.6 Scoring
Each reader slice is scored on the sample, lore first and then the session, before and after:

- **The keys gain rows for:** threads (kind, giver, target), objectives, rewards, time limits (the
  words, and the game date where the key has one), clues with the thread they are a clue to, and
  progress in the session key.
- **The keys include quests nobody calls a quest:** at least one taken in a few lines of talk, one
  with a reward that isn't gold, one with no reward, and one ask that nobody takes up (a hook). They
  also include talk that must make nothing: a plan, an errand and a threat.
- **Counted:** found, missed, wrong (a thread that isn't one, a part on the wrong thread), and
  doubles (one thread suggested twice).
- **The existing counts must not get worse:** names, connections and false flags.

---

## 10. What the DM sees, and what players see

### 10.1 The DM
| Where | What |
|---|---|
| **Suggestions to check** | after an upload or a session, under "❓ Questions for you" and on the website's Questions page: "Story threads to check", one card per bundle with Keep, Change and No. Counts only in the DM Bot channel |
| **A thread's card** (🧵 Threads in Discord; a Story threads page on the website) | state; who is in it; objectives as a checklist; complications; reward; time limit with its date and time left; clues found and not found; the table's guess; what moved it. The DM edits every part here |
| **The Story Graph** | "Cluster by quest" groups every entry tied to a kept thread: by `in_thread`, `advances`, `resolves`, `led_to`, `clue_to`, and the entries its parts point at. The Quests chip shows the thread kinds. Suggestions not yet kept are not drawn |
| **The Story timeline** | under each session: threads started, objectives met, complications, rewards paid, threads finished. Dated time limits appear as markers at their game date, with those still to come after the latest session |
| **The pre-session note** | "⏳ Time limits:" beside "🧵 Quiet threads" and "🤝 Due soon" |
| **Private messages** | a time limit nearing or passed (section 8.4). Nothing else in this design speaks to the DM unasked |

Discord and the website stay level. The Story threads page is the website's half of 🧵 Threads,
which has none today.

### 10.2 Players
- **Threads are hidden from players** unless the DM shows one ("Show to players" on its card).
- **A shown thread shows only its parts marked `table_knows`,** and only the people and places in
  it that the player may already see.
- **`table_knows` starts true** for what a session's suggestion read from the table's own talk,
  and false for anything from lore, an adventure or the DM's own typing. The DM can flip it.
- **A clue** is seen by a player only when one of their characters knows it. A player never sees
  how many clues a mystery has, or a clue's "false lead" mark.
- **A time limit** the table heard is shown with its words and date. A hidden one is never hinted
  at: a private message about it goes to the DM alone.
- **Nothing here goes into a transcript,** and nothing is pushed to players.
- **Every reader of `thread_parts` goes through one function** that applies this section, with a CI
  test like `test_relation_readers.py`.

---

## 11. Never

- **Never add a thread, a part or a clue by itself.** A reader suggests; the DM keeps.
- **Never invent** a quest, an objective, a reward, a deadline or a clue the text doesn't state, and
  never fill in a part because quests "usually have one".
- **Never finish, fail or drop a thread.** Not when every objective is met, and not when a hard
  limit passes.
- **Never judge a guess,** and never mark a mystery solved.
- **Never read a time limit as a real-world date,** and never give a date to words the calendar
  doesn't hold.
- **Never move the game clock** because of a time limit.
- **Never make the party an entry.**
- **Never show a player** a hidden thread, a part the table doesn't know, the number of clues, a
  false-lead mark or the DM's answer.
- **Never speak to the DM unasked on the DM screen,** and never ask about a thread during play.
- **Never use one campaign's threads in another.**

---

## 12. Alternatives rejected

| Alternative | Why not |
|---|---|
| **Objectives and complications as graph entries** | Many, nameless, and tied to one thread. They would crowd the names list, be matched as names when heard, and be drawn on the Story Graph |
| **Everything in the thread's description text** (today) | No state for each step, no history, no date to count down, nothing to score a reader against |
| **A table for each kind of part** | Four copies of the read/keep rule, history, Undo, the backup and the players' rule |
| **Parts as JSON on the thread entry** | No history for one step and no Undo for one change |
| **A complication as an event with a new connection** | An event is what happened; a complication is what is now in the way, and it has a state of its own. The part points at the event instead |
| **A reward as a promise thread** | A second thread for every paid job, to be kept in step with the first |
| **The party as an entry, or a stored "taken by the party"** | Ruled out on #3157. A default needs no storage |
| **New connections `hired`, `gives_quest`, `seeks`, `wants`** | `in_thread` with its part already says each, and each new term is one more thing a reader can get wrong |
| **`led_to` for "is a clue to"** | It means one thing sent the party to another. A clue can bear on a mystery without having led anywhere, and it needs the false-lead mark |
| **A percentage for a mystery** | It implies DMbot knows the answer and the total. A count of clues found is true and makes no claim |
| **Readers adding what they are sure of** | The hard rule: PlotBot records only what the DM confirmed |
| **Deadlines in real-world time or by session count** | The owner asked for the in-game calendar. Session counts are what "stalled" already measures |
| **Turning on PlotBot's recap check for this** | It reads earlier recap texts, not the stored threads. It stays a separate decision |

---

## 13. Rules developers must keep

1. **A reader writes `read`.** Only the DM's keep or the DM's own edit makes `confirmed`.
2. **Every write goes through the change log.** One keep, one upload and one session's suggestions
   are each one Undo.
3. **History, not overwriting:** a change in the story closes the old row; a mistake rejects it.
4. **Nothing worked out is stored:** "stalled", "passed", time left and clue counts are computed
   when shown.
5. **One reader function** for `thread_parts` applies section 10.2, with a CI test.
6. **A time limit's game minute is set by code or the DM, never by the AI.** The module that turns
   words into a minute is pure Python, tested without a database.
7. **Time-limit messages go to the DM's private chat,** once each, with one button at most.
8. **Per-campaign isolation, the backup and `ON UPDATE CASCADE`** for the new table.
9. **Discord and the website stay level,** and `docs/discord-web-parity.md` is updated each slice.
10. **Plain words.** The DM never sees "part", "bundle", `thread_parts` or a status word. The
    ux-critic checks each slice's words before code.
11. **Each reader slice is scored** as in section 9.6, with its numbers in the PR.
12. **The ontology change** (`clue_to`, "takes it on") is one reviewed code release, with its
    definitions, "not this" lines and examples.

---

## 14. Slice order

Each slice is its own PR with tests and a migration number claimed on #2475.

| # | Slice | Whose | Needs | What the DM gets | Scored on |
|---|---|---|---|---|---|
| 1 | **The lore reader suggests threads** with who gave them and who they are aimed at, using only today's kinds and connections. The suggestion card. The keys' "party works for" rows become quest rows | GraphEngineer | nothing new | quests from an upload, and a Story Graph whose quest clusters fill | lore key: threads, givers, targets |
| 2 | **`thread_parts` and the DM's own editing:** objectives, complications, rewards and time-limit words on the thread's card; "takes it on"; the Story threads page | GraphEngineer, WebDev | slice 1's card | a checklist, a reward and a deadline note on every thread, by hand | none (no reader) |
| 3 | **The lore reader fills the parts:** objectives, reward and time-limit words in the bundle | GraphEngineer | slice 2 | whole quests from an upload | lore key: objectives, rewards, time-limit words |
| 4 | **Time limits counted:** the date from the words, the DM's one-tap date, the nearing and passed messages, the pre-session line | GraphEngineer | slice 2; the game clock | "1 day left", told privately | the dates in the keys |
| 5 | **The session reader suggests new threads and progress** | GraphEngineer | slices 2 and 3 | after each session, "Story threads to check" | session key: new threads, progress, doubles |
| 6 | **Clues and mysteries:** `clue_to`, the false-lead mark, readers suggesting clues and who found them, the "clues found" count | GraphEngineer, WebDev | slice 5 | "4 of 7 clues found" | keys: clues and their threads |
| 7 | **The table's guesses:** the readings switched on for theories, shown on the mystery's card | GraphEngineer | slice 6, and a scored key for readings | "The table's guess: …" | a readings key |
| 8 | **The Story timeline** shows thread movements and time-limit markers | WebDev, GraphEngineer | slices 4 and 5 | the campaign's quests along its sessions | none |

Slice 1 comes first because it needs no new storage and answers the owner's first complaint: the
quest filter never shows anything DMbot read.

---

## 15. The four questions, answered

1. **Are objectives and complications entries in the graph, or rows of their own?**
   Rows of their own, in `thread_parts`, the way `map_facts` rows sit beside places. The thread
   and its clues stay entries. A complication may point at the event entry that caused it
   (sections 2, 4 and 5).
2. **How is "the party" the doer of a quest without being an entry?**
   By default and without storage: a thread is the party's unless an `in_thread` "takes it on"
   connection names a character or group. Being hired is a quest with a giver, and whether the
   party took it is the thread's state (section 6).
3. **How does a deadline relate to the timeline, the calendar and "stalled"?**
   A deadline is a part holding its words and, when one can be worked out, a game minute. The
   calendar turns that minute into a date and "time left". The timeline shows it as a marker at
   that date. "Stalled" counts real sessions and is left alone: the two are separate measures, and
   a thread that is both is listed first (section 8).
4. **How does a mystery's "solved so far" work without DMbot deciding the answer?**
   It is a count of the clues tied to the mystery that the party has found, with a total only
   when the DM (or a kept read of the adventure) listed the clues. The table's guess is shown
   beside it as the table's belief. DMbot never compares the guess with the answer and never marks
   the mystery solved (section 7).

---

## 16. Open questions

1. **A thread inside a thread.** A campaign arc that holds several quests can be said today only
   with `led_to`. A proper "part of" between threads is left until a campaign needs it. For
   Supervisor, if the sample's adventure shows it matters.
2. **When to tell the DM a limit is near.** "One game day before, or halfway when it is closer
   than two days" is a starting rule. Whether the DM can set it per thread is for the owner after
   the first live use.
3. **A time limit passing in the middle of play.** As written it is a private message, under the
   owner's rule for unasked messages. If the owner rules that live in-session advice stays on the
   DM screen (open in PLAN), the "passed" message could go there instead. For the owner.
4. **Moons and seasons.** "By the new moon" stays words until a calendar can hold moons. Adding
   moon phases to the Harptos calendar is a TimeBot question, outside this design.
5. **The words** on the suggestion card and the thread's card, for the ux-critic slice by slice.
6. **Old "reward" parts on `in_thread`.** Left as they are, or offered to the DM as a one-time
   move into reward parts. Proposed: left as they are. For Supervisor.
