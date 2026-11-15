# Design basis: one tagger for a session's transcript

**Status:** revision 1, 2026-11-15, written at the owner's request. **Not binding until Supervisor
approves it.**

**Who builds it:** GraphEngineer owns the tagger, the record of what was checked and the readers it
feeds (`core/src/dmbot/memory/`). CloudDev-lead owns the transcript and its jobs. WebDev builds the
website's half of the one line the DM sees.

**Read with:** `docs/STORY_MEMORY.md` section 3 ("AI roles that maintain the graph") and section 8;
`core/src/dmbot/memory/extractor.py`; `docs/costs.md` ("Story memory"); `docs/PROMPT_CACHING.md`;
`docs/design/quests.md` and `docs/design/mapbot_layers.md`.

## Contents
1. [Problem and context](#1-problem-and-context)
2. [The decision](#2-the-decision)
3. [The tags](#3-the-tags)
4. [What the tagger reads and returns](#4-what-the-tagger-reads-and-returns)
5. [How tags are used: routing](#5-how-tags-are-used-routing)
6. [Several things in one moment](#6-several-things-in-one-moment)
7. [Nothing left behind: the record of what was checked](#7-nothing-left-behind-the-record-of-what-was-checked)
8. [After the session, and during it](#8-after-the-session-and-during-it)
9. [Privacy, consent and safety](#9-privacy-consent-and-safety)
10. [Scoring](#10-scoring)
11. [Never](#11-never)
12. [Alternatives rejected](#12-alternatives-rejected)
13. [Rules developers must keep](#13-rules-developers-must-keep)
14. [Slice order](#14-slice-order)
15. [Open questions](#15-open-questions)

---

## 1. Problem and context

A session holds many kinds of thing DMbot should notice: a quest taken, an item changing hands, a
move to a new place, time passing, a clue found, a fight. The owner asked whether each needs its
own small model listening in parallel (a TimeBot, QuestBot, NPCBot, MapBot, LootBot), and then
proposed the simpler shape this document adopts: **one model that only says which kinds are
present,** so nothing in a session is left behind.

**What exists today:**

- **After a session, the transcript is read in batches of 10 lines** (`extractor.BATCH_LINES`,
  about 30 to 60 seconds of talk).
- **Each batch is already asked several questions, one call each:** the claims, then the readings
  (theories and reveals), then the deeds, then who learned what (`extractor.py`: "a second
  question of the same lines", "a third", "a fourth"). Every new reader adds one more call for
  every batch.
- **One reader already has a pre-check:** "only batches that could hold a reveal are asked"
  (`reveals_precheck`, Supervisor's ruling on #2996, kept only if kept reveals don't drop).
- **More readers are coming:** quests and their parts (`docs/design/quests.md`), places and routes
  (#4173), possessions, map questions.
- **The measured cost of one question per batch** on the fast model is about $0.0015 a call, so
  about $0.36 to $0.73 for a four-hour session of 240 to 480 batches (`docs/costs.md`). Most of
  each call is input: the instructions and the lines.
- **Nothing records what was looked at.** If a reader is switched off, runs out of AI allowance or
  simply misses something, nobody can tell which part of a session went unread.

**The two problems this solves:**

1. **Cost and overlap grow with every reader.** Eight readers each reading every batch is eight
   times the input for the same words, and eight chances to suggest the same thing twice.
2. **No one can say a session was fully checked.**

---

## 2. The decision

1. **One small model tags each batch** with the kinds of thing present in it. It returns tags and
   line numbers, and nothing else.
2. **The tagger finds nothing and writes nothing to the campaign's memory.** It never names a
   quest, an item or a place. The readers do that, as today.
3. **Each reader reads only the batches tagged for it,** with the lines around them. This replaces
   "every reader reads every batch" and the one-off pre-checks.
4. **Code tags what code can see,** before the model is asked: a fight, a clock phrase, a posted
   picture.
5. **Every tag is accounted for.** For each tagged batch and kind, DMbot records whether a reader
   looked and what came of it. The DM gets one counts-only line saying the session was checked,
   or what wasn't.
6. **The "bots" stay names for areas of the product,** each owning its data and its reader. None is
   an always-on model of its own.

**Why one tagger and not a model per bot:**

- **One read of the words, not many.** The tagger's call has the same input as any one reader's
  and a far smaller answer.
- **It can be tuned to over-tag.** A wrong tag costs one reader one look. A missed tag loses
  something. A reader that writes suggestions can't be tuned that way without flooding the DM.
- **It gives the check the owner asked for.** A tag is a claim that something is there; a record
  of what happened to each tag shows what was left behind.
- **The readers get better input.** A reader sent the stretches about its own subject, with their
  surroundings, reads less noise than one combing every batch.
- **Reading after the session beats listening live.** A quest taken late may have had its reward
  named an hour earlier. Parallel live listeners each see 30 to 60 seconds.

---

## 3. The tags

A batch can carry any number of tags, or none.

| Tag | Present when the batch has | Read by | Code can set it when |
|---|---|---|---|
| `place` | a move, an arrival, a departure, travel, or a new place described | the story-chain and place readers (#4173); MapBot's answers | a place with a map pin is named |
| `time` | time passing, a rest, a date, a deadline said | TimeBot's suggestions; quest time limits | a clock phrase matched; the DM changed the clock |
| `quest` | an ask, a taker, a pursuit, progress, a setback, an objective met | the quest reader (`docs/design/quests.md`) | never |
| `hands` | something given, taken, found, bought, lost, lent or returned; a reward or help | the possessions reader; quest rewards and help | never |
| `people` | something learned about a person or group: who they are to someone, what state they are in | the claims reader | never (names themselves are always read, below) |
| `learned` | a clue found, a secret told, a theory voiced, a lie exposed | the reveal and readings readers | never |
| `deed` | something the party or a character did that others would remember | the deeds reader | never |
| `fight` | a fight starting, running or ending | the fight summary | the initiative tracker is running |
| `map` | a map, a layout or a route described or shown | MapBot | a picture was posted and read as a map |

- **The list is fixed in code.** A new tag is a reviewed change with its definition, its reader and
  its key rows, like a new term in the ontology.
- **No tag for names.** The name pass reads every line, always. A missed name can't be allowed to
  depend on a tag.
- **No tag for table talk.** The off-topic filter already labels it, and the tagger is sent only
  game content (section 15, question 1).
- **A tag says "look here", never "this is one".** `quest` on a batch is not a quest.

---

## 4. What the tagger reads and returns

**It reads:** one batch of lines, as the readers get them today (speaker, line number, words), and
the two lines before it for context. It is given no campaign memory: no names list, no open
quests, no secrets. Tagging needs none, and a prompt with nothing secret in it has nothing to leak.

**It returns,** for each tag it sets: the tag, the line numbers that made it set it, and one of two
words, "yes" or "maybe". Both count as tagged. Code keeps the word for scoring.

**Rules:**

- **The fast model,** one call a batch, with the instructions cached (`docs/PROMPT_CACHING.md`: the
  same instructions sent once a batch is exactly where caching pays).
- **The model says words; code says everything else:** which batch, which session, which reader.
- **An answer that can't be read is retried once,** then the batch is marked "tagger failed", which
  sends it to every reader (section 7). It is never read as "nothing here".
- **A cut answer is retried,** never treated as complete. The output limit follows the generous
  limits rule.
- **Code's tags are added to the model's,** never replaced by them.

---

## 5. How tags are used: routing

- **A reader is sent each batch tagged for it, with the batch before and the batch after.**
- **Readers whose subject runs across a session get their batches together.** For `quest` and
  `hands`, every batch with that tag in the session goes to the reader in one reading, in order
  (in parts when long). A reward named at one point and a job accepted an hour later are then in
  front of the same reader, as long as both were tagged.
- **Readers run in a fixed order,** so later ones build on earlier ones:
  1. names (every line, as today);
  2. places and time;
  3. possessions;
  4. quests, people, learned, deeds, in parallel.
- **A reader is told what earlier readers suggested from the same lines,** so it points at the item
  or place already found instead of describing it again (section 6).
- **A reader may still find nothing.** That is a normal outcome and is recorded.
- **Each reader moves to tagged-only reading by itself,** and only when the sample shows its kept
  suggestions don't drop (the rule Supervisor set for the reveals pre-check on #2996). Until then
  it keeps reading every batch, and the tags are only compared with what it found.

**What it costs,** as a formula to be measured in slice 1, not a promise:

- today: batches × readers;
- with the tagger: batches × 1, plus, for each reader, only its tagged batches and their
  neighbours.

It pays as soon as the average reader is tagged for fewer than about two batches in three, and it
pays more with each reader added.

---

## 6. Several things in one moment

One line often holds several things: accepting a quest and being handed an item to help with it is
a new quest, a change of possessions, and help given for that quest.

- **A batch carries every tag that fits.** The tagger never picks one.
- **One place decides whether a thing is new.** Names and items are settled before the other
  readers run, so two readers can't each make the same item.
- **Later readers point at what is already there.** The quest reader suggests the quest and ties
  the item to it; it doesn't suggest the item a second time.
- **Suggestions that cite the same lines are shown to the DM together,** as one card for the
  moment: "New quest: find Garret. New item: the route map, held by Belleros, given to help with
  this quest." Each line can be switched off; keeping or undoing the card is one action.
- **The record wants both** (section 7): a batch tagged `quest` and `hands` isn't checked until
  both readers have looked.

---

## 7. Nothing left behind: the record of what was checked

**What is kept,** one row for each batch of a session (`session_tags`):

| Field | Meaning |
|---|---|
| campaign scope, session | as every table |
| the batch | its first and last line, by the lines' own times |
| tags | each tag, whether code or the model set it, and "yes" or "maybe" |
| tagger version | so a change to the tagger's words can be told apart in the scores |
| for each tag, what came of it | one of the outcomes below, and when |

**The outcomes, for each tag on each batch:**

| Outcome | Means |
|---|---|
| **suggested** | the reader looked and made at least one suggestion from these lines |
| **nothing to keep** | the reader looked and found nothing |
| **not looked at** | no reader has read it, with the reason: the reader is switched off, the AI allowance ran out, the read stopped, or the lines were removed |

**What the DM sees:** one line with counts only, with the after-session results in the DM Bot
channel and on the website:

> Session checked: 38 parts had something in them (6 quests, 9 things changing hands, 11 places,
> 12 others). Nothing skipped.

or

> Session checked: 38 parts had something in them. 5 not checked yet: the AI allowance ran out.
> **[Check them now]**

- **Counts only,** never the words, as every post in that channel is.
- **"Check them now" reads only what was not looked at.** A finished part is never sent twice.
- **A reader switched off is said plainly** ("9 things changing hands: not checked, this isn't
  switched on yet"), so the DM is never told a session was fully checked when a reader didn't run.
- **It makes no claim about what the tagger missed.** The line says what was checked, not that the
  session holds nothing else. Section 10 is how misses are measured.

**Storage rules:** per campaign with forced row-level security; foreign keys that carry `guild_id`
are `ON UPDATE CASCADE`; listed in `campaigns/tables.py`. It is **not in the backup:** it holds
nothing of the story, and a restored campaign's sessions can be tagged again. It is deleted with
the session or the campaign.

---

## 8. After the session, and during it

- **After the session is the default.** The tagger is the first step of the after-session job, and
  the readers follow.
- **During the session, only if live reading is switched on,** the same tagger runs on each batch
  as it settles. Live tags do two things and no more:
  1. they give the after-session job a head start (the batch is already tagged);
  2. they let code offer the DM something at the right moment, such as a map's link when `place`
     or `map` is tagged and a pinned place was named (`docs/design/mapbot_layers.md`, section 9).
- **Live tags never start a reader during play,** and never ask the DM anything. Suggestions wait
  for the after-session review, as now.
- **A live tag is never held against the transcript's path:** a line is written whether or not its
  batch was tagged (STORY_MEMORY section 8).

---

## 9. Privacy, consent and safety

- **Consent is checked before each batch is sent and again after the answer,** as every reader
  does. A person who stopped being recorded has their lines left out, for good.
- **When a person deletes their lines,** the rows for the batches those lines were in are deleted.
  A later run tags what is left.
- **Tags hold no words of the transcript,** only line positions and tag names. Logs hold counts.
- **One campaign at a time.** A prompt never holds two campaigns' lines, so a cached prefix never
  does either.
- **The transcript is data.** Lines go in as quoted data. The answer is checked against the fixed
  tag list and the batch's own line numbers: a tag not on the list, or a line not in the batch, is
  dropped. Words in a line that read like an instruction to DMbot change nothing, because the
  tagger can do nothing but name tags.
- **The DM's private channels and the DM sidebar are never tagged,** as they are never read by the
  session readers.
- **Tagging is metered AI use** and the wait shows progress: the stage ("checking what's in this
  session"), how far (batch 40 of 310), how long, and when it last moved.

---

## 10. Scoring

The tagger is judged on what it lets through, not on how tidy its tags are.

- **Reach, per tag:** of the sample key's rows of that kind, how many have their lines inside a
  batch carrying the tag. This is the ceiling on what that kind's reader can find, so it must be
  high. A key row's own line numbers and kind give the expected tag, so the keys need no hand
  labelling beyond what the readers' keys already hold.
- **Load, per tag:** what share of batches carry it. This is the cost. A tag on nearly every batch
  routes nothing and should be dropped or redefined.
- **The pair that matters:** for each reader, its kept suggestions when it reads only tagged
  batches against when it reads every batch. Tagged-only is switched on for a reader only when
  the first doesn't fall below the second.
- **Reported, not gated:** how many batches were tagged "maybe", the cost of a session's tagging,
  and how often the tagger failed.
- **The quest rows that nobody calls a quest** (`docs/design/quests.md`, section 9.3) are part of
  the `quest` tag's reach: the Garret job, taken in a few lines, must fall in a tagged batch.

---

## 11. Never

- **Never let a tag become a fact,** a suggestion or a question to the DM. A tag only sends a
  reader to look.
- **Never read "no tag" as "nothing happened"** in what the DM is told.
- **Never drop a batch because tagging failed.** A failed batch goes to every reader.
- **Never make names depend on a tag.**
- **Never give the tagger the campaign's memory or secrets.**
- **Never start a reader, or ask the DM anything, during play because of a tag.**
- **Never tell the DM a session was fully checked** when a reader was off or a part was skipped.
- **Never send lines from someone who has not agreed,** and never keep a batch's row after its
  lines are deleted.

---

## 12. Alternatives rejected

| Alternative | Why not |
|---|---|
| **A small model for each bot, all listening live** | The same words read many times; overlapping suggestions to reconcile; each sees only seconds of talk; and almost nothing they find is wanted during play |
| **One large reader that finds everything in one call** | One long prompt doing nine jobs does each worse, can't be scored or switched on a reader at a time, and one cut answer loses all nine |
| **Every reader reads every batch** (today) | Cost and overlap grow with each reader, and nothing records what was looked at |
| **A pre-check inside each reader** (as reveals has) | One more call for each reader for each batch: the tagger is the same idea paid for once |
| **Keywords only** | A quest nobody calls a quest, or a loan ("bring them back unharmed"), has no keyword. Code tags what code can see and the model does the rest |
| **The tagger also pulls out the facts** | Then a miss or a mistake is in the memory, and it can no longer be tuned to over-tag |
| **Tags as part of the campaign's memory or its backup** | They are a work record about a transcript, not story |

---

## 13. Rules developers must keep

1. **The tagger's answer is tags, line numbers and "yes" or "maybe".** Anything else in it is
   dropped.
2. **The tag list lives in code,** each tag with its definition, its reader and its key rows.
3. **The name pass reads every line,** tagger or no tagger.
4. **A reader goes tagged-only by its own switch,** on its own numbers (section 10).
5. **Every tag on every batch ends with an outcome,** and "not looked at" always has a reason.
6. **A failed or unreadable tagging sends the batch to every reader.**
7. **Consent before and after each call;** rows go when their lines go.
8. **Counts only** in logs and in the DM Bot channel.
9. **The record is per campaign,** with forced row-level security and `ON UPDATE CASCADE`, and is
   on the written "not in a backup" list with its reason.
10. **The parsing and routing are pure Python,** tested without a database or a network.
11. **Discord and the website stay level** for the one line the DM sees, and
    `docs/discord-web-parity.md` is updated.
12. **Plain words.** The DM never sees "tag", "batch" or "tagger": the line says "parts" of a
    session and what was "checked". The ux-critic checks the words before code.

---

## 14. Slice order

Each slice is its own PR with tests; a migration number is claimed on #2475 where one is needed.

| # | Slice | Whose | Changes what users see? | Proves |
|---|---|---|---|---|
| 1 | **Measure only:** the tagger's prompt and parser, and a tool that runs it on the sample session and prints reach and load for each tag, the "maybe" share and the cost | GraphEngineer; dev1 runs it | no | whether the idea holds: reach high enough, load low enough |
| 2 | **The record:** `session_tags`, tagging as the first step of the after-session job, code's own tags, and the counts-only line. Readers still read every batch | GraphEngineer, CloudDev-lead, WebDev | yes: the "Session checked" line | on real sessions, what tagged-only reading would have missed (compared, not acted on) |
| 3 | **Routing, one reader at a time:** each reader gets its tagged-only switch, starting with reveals (replacing its pre-check), then deeds, then the new readers as they are built | GraphEngineer | no | cost down with kept suggestions not down, reader by reader |
| 4 | **One card for one moment:** suggestions that cite the same lines are grouped, and later readers are told what earlier ones suggested | GraphEngineer, WebDev | yes | no doubled items; fewer cards |
| 5 | **Live tags,** only where live reading is switched on: the head start and the map link | GraphEngineer | yes, for those campaigns | the same tags live as after the session |

Slice 1 comes first because it costs little and can end the idea: if reach is poor on the sample,
the later slices are not built.

---

## 15. Open questions

1. **One pass with the off-topic filter?** A light AI pass already labels each stretch as game or
   table talk (`transcript/topic_ai.py`, #52). The tagger could be the same call with more labels,
   which would make tagging nearly free. They are kept apart here because the off-topic pass runs
   live on every line and decides what readers ever see. For Supervisor, after slice 1's numbers.
2. **How much around a tagged batch a reader gets.** One batch each side is a starting point; the
   sample will show whether quests need more.
3. **A check on the tagger's misses in real use.** Proposed: for a small share of sessions, one
   reader also reads the untagged batches, and what it finds there is counted (never shown as an
   error). For Supervisor, since it costs AI use the DM didn't ask for.
4. **Whether "maybe" should ever be dropped** to cut cost. Not before the numbers say so.
5. **The words of the "Session checked" line,** for the ux-critic.
