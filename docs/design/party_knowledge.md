# Design basis: who knows what when the party changes

**Status:** revision 1, 2026-11-15, in answer to issue #4535 (the owner's question, put by
Supervisor). **Not binding until Supervisor approves it.**

**Who builds it:** GraphEngineer owns the gate, the knowers and the party record
(`core/src/dmbot/memory/`). WebDev builds the website's halves. CloudDev-lead owns the game clock
it sits beside.

**Read with:** `docs/STORY_MEMORY.md` section 9b (who knows what); `core/src/dmbot/memory/gate.py`
and `knowers.py`; issues #4523 (the sample's who-knows keys), #4524 (public never becomes secret by
itself) and #4534 (👥 Some know on the question cards).

## Contents
1. [Problem and context](#1-problem-and-context)
2. [The decision](#2-the-decision)
3. [The party record](#3-the-party-record)
4. [Common knowledge](#4-common-knowledge)
5. [When a character joins: catching them up](#5-when-a-character-joins-catching-them-up)
6. [When a character leaves or comes back](#6-when-a-character-leaves-or-comes-back)
7. [The icons and the words](#7-the-icons-and-the-words)
8. [What is read to the whole table](#8-what-is-read-to-the-whole-table)
9. [From the flags to the lists](#9-from-the-flags-to-the-lists)
10. [What #4523, #4524 and #4534 must keep in mind](#10-what-4523-4524-and-4534-must-keep-in-mind)
11. [Never](#11-never)
12. [Alternatives rejected](#12-alternatives-rejected)
13. [Rules developers must keep](#13-rules-developers-must-keep)
14. [Slice order](#14-slice-order)
15. [The five questions, answered](#15-the-five-questions-answered)
16. [Open questions](#16-open-questions)

---

## 1. Problem and context

**The owner (2026-11-15):** 🤫 is secret to all player characters, 👤 is known to one, 👥 to two or
more but not all in the current party. When a new player character joins, "they wouldn't
automatically know everything the other party members knew before, so the icons would need
updating if the party comp changed."

**What the design already says** (STORY_MEMORY 9b): every statement has a list of the characters
who know it; the words are worked out from the list, never stored; "the party" is never a knower.

**What the code does today:**

- **The lists exist** (`memory_knowers`, one row for each statement and character) and one gate
  reads them (`gate.visible_to`).
- **"The party" means every confirmed entry whose role is player character** (`gate.load`,
  `gate.party_entries`). There is no record of who was in the party when, and no way to say a
  character has left.
- **A newcomer is given everything automatically.** When a character becomes a player character,
  `knowers.inherit_table_rows` copies to it every statement all the other player characters know
  (Supervisor's ruling of 2026-11-01, #2995; migration 0316 did the same for older campaigns).
  **This is the opposite of what the owner now asks.**
- **The "shown" flags still drive the lists.** A statement that becomes shown is written as known
  to every player character at that moment (`knowers.follow_flags`), and some readers still ask the
  flags, which mean "every player".
- **World lore is stored the same way as the party's own story:** the upload's "world" and "party"
  kinds both become rows for each player character (`lore_knowledge.py`, #3963). A newcomer would
  have neither, though any inhabitant of the world would know the first.

**So three things are missing:** a record of the party over time, a way to say "everyone knows
this" that includes people who arrive later, and a deliberate step for what a newcomer knows.

---

## 2. The decision

1. **The party has a record.** Each player character has spans in the party: when they joined and
   when they left. "The current party" is whoever has an open span. Nothing else changes about
   characters: a character who leaves stays a character and keeps what they know.
2. **Common knowledge is a list of its own.** One point of view, "everyone", holds what any
   inhabitant of the world knows. Every character has it, including one who joins next year. It is
   the group mechanism 9b already describes, with everyone as its members.
3. **A newcomer starts with common knowledge and their own backstory, and nothing else, until the
   DM says.** When a character joins, the DM is asked once what they know. This reverses the
   automatic copy of #2995.
4. **Words and icons are worked out against the current party when shown,** never stored. A
   character joining or leaving changes them with no write.
5. **Anything about a past session is worked out against the party as it was at that session.**
6. **The flags stop answering "may this player see it".** The lists do, through the gate. The flags
   stay as the DM's switch for "shown to the table now".
7. **A change of party never takes knowledge away from anyone.** Leaving removes no rows. Joining
   removes none from the others.

**This is about what a character knows, for the story's sake.** It keeps recaps, answers and the
DM's cards true to the fiction. It is not a wall between players: a new player may read the
campaign's transcripts like anyone in the campaign. What stays truly hidden is what was never said
at the table, such as another character's secret backstory.

---

## 3. The party record

**One row is one span of one character in the party** (`party_spans`):

| Field | Meaning |
|---|---|
| campaign scope, `id` | as every memory table |
| character | the player character's entry |
| from | the session it starts at (`from_session_at`), and the game minute when the campaign has a game clock |
| to | the session and game minute it ends at. Empty while they are in the party |
| why it ended | one word: left, retired, died, away |
| `source` | `dm`, or `start` for a span written by the backfill |

- **A character can have several spans** (left and came back). They never overlap.
- **The current party** is every confirmed player character with an open span.
- **The party at a session** is every character whose span covers that session's start.
- **The party is still never an entry and never a knower.** The record is about characters.
- **History, not overwriting:** a span is closed, never deleted, when someone leaves. "That was
  wrong" rejects a span through the change log, as everywhere else.
- **Storage rules:** per campaign with forced row-level security; `ON UPDATE CASCADE`; in
  `campaigns/tables.py` and in the backup; every write through `memory_changes`. A merged or
  erased character takes its spans with it.

**Who writes it:**

- **Making a character a player character opens a span,** from now by default. The DM can instead
  pick "from the start" or a session.
- **The DM closes a span** with one tap on the character's card: "Left the party", with the reason.
- **Readers only suggest.** A session in which a character says goodbye, or a player character
  marked dead, gives the DM an after-session question ("Has Okthandom left the party?"). Nothing
  closes a span by itself.
- **Older campaigns:** each confirmed player character gets one open span from the time it became
  one. No one is treated as having left.

**Beside the game clock:** the clock's groups say where the current party's members are while the
party is split. Only a character with an open span can be placed in a group. A span's game minute
lets "as of" questions (`memory/as_of.py`) use the party of that moment.

---

## 4. Common knowledge

**One new point of view for each campaign, "everyone"** (a new kind beside truth, party, pc and
npc, with no entry of its own).

- **A row held by "everyone" is known to every character,** player character or NPC, in the party
  or not, now or later. It follows 9b's group rule: each member's view includes the group's rows,
  and a character's own row wins.
- **It is for what any inhabitant would know:** "Bryn Shander is the largest of the Ten-Towns".
- **It is not for the party's own story.** What the party did, saw or was told together is known to
  the characters who were in the party then, by their own rows. A newcomer does not know it.

**What writes a common row:**

| Source | Result |
|---|---|
| a lore upload read as **World general knowledge** | each statement gets a row for "everyone" |
| a **smart guess** upload | its "world" statements get a row for "everyone"; its "party" statements get rows for the current party's members; its spoilers get none |
| the DM, on any statement | "Everyone knows this", one tap, with Undo |
| a session | never by itself. What is said at the table is known to the characters there (9b rule 4), which is not the whole world |

**Moving what is already stored:**

- A statement that came from an upload read as World, or that a smart guess sorted as "world",
  gets a row for "everyone". This only adds.
- Where the stored data can't tell a world statement from one shown at the table, nothing is
  added. Such statements stay known to the characters who have them, and the DM can mark any of
  them "Everyone knows this".

**Rules:**

- **Common knowledge is public** for #4524: it never becomes secret, and never loses its row, by
  itself.
- **Learning a common statement includes learning that its entries exist,** as for any statement.
- **The fast path must count it:** `gate.party_entries` answers without reading every row, and
  today falls back to the full gate when a group holds a list. Common rows are the usual case, so
  the fast path handles them directly.
- **No exceptions yet:** a character who is a stranger to the region still has common knowledge.
  Section 16 holds the question.

---

## 5. When a character joins: catching them up

**On joining, a character knows:**

1. common knowledge (section 4);
2. their own backstory, when one is uploaded (9b: a backstory is known to its character);
3. their own entry, and whatever is said at the table from then on (9b rule 4).

**Nothing else, until the DM answers one question.** It is a private message from DMbot when the
span opens, under the owner's rule for getting the DM's attention:

> **Mira has joined the party. What does she know of what came before?**
> **[Only what everyone knows]** **[What the party knows]** **[Let me choose]**

| Answer | What is written |
|---|---|
| **Only what everyone knows** | nothing. This is also what holds until the DM answers |
| **What the party knows** | every statement that all the other current members know, at the least sure of theirs. This is today's `inherit_table_rows`, run only on the DM's word |
| **Let me choose** | a link to the DM Bot channel or the website, where the DM ticks by session, by quest, by person or place, or one by one |

- **Never copied, whichever answer:** a statement only some members know, one only the DM or NPCs
  know, another character's backstory, or a row a hide put to sleep.
- **One change, one ↩ Undo,** counted to the DM ("Mira now knows 212 things the party knew").
- **Later, a little at a time:** any statement's "Who knows this?" already lets the DM add a
  character. A quest's or an entry's card gains "Tell Mira", which adds her to everything on that
  card the rest of the current party knows.
- **In play it happens by itself:** when the table tells the newcomer something, or it is said in
  front of them, they learn it (9b rule 4), with no question.
- **Several characters joining together** get one question, answered once for all of them.
- **A campaign's first characters** have no "before", so nothing is asked.

---

## 6. When a character leaves or comes back

- **Leaving closes the span and nothing else.** Every row the character holds stays.
- **A character who has left is not counted** in "the party", in the icons, or in what is read to
  the table. Their player still sees what their own characters know.
- **What a departed character alone knew is shown as such:** "No one in the party knows. Okthandom,
  who left, does." It is not the same as a secret only the DM knows.
- **A dead character** is a character whose span ended with "died". The dead keep what they knew;
  the story may still need it.
- **Coming back opens a new span.** They keep everything from before. For the gap, the same
  question is asked: "What does Okthandom know of what happened while he was away?"
- **A guest for one session** is a span that opens and closes around it.

---

## 7. The icons and the words

Worked out when shown, from the statement's knowers and the **current** party. Never stored.

| Icon | Rule | Words on the card |
|---|---|---|
| 🌍 | "everyone" holds it | "Everyone knows" |
| ✅ | every current member knows it, and it is not common knowledge | "The whole party knows" |
| 👥 | two or more current members know it, not all | "Known to: Cerric, Belleros" |
| 👤 | exactly one current member knows it, and the party has more than one member | "Known to: Cerric" |
| 🤫 | no current member knows it | "No one in the party knows", then who else does: an NPC, or a character who left |

- **A party of one:** that character knowing it is ✅, never 👤.
- **No party yet:** no icon is worked out; the card says "No player characters yet".
- **A guess counts as knowing** (Supervisor's ruling D on #4524): a member's row at any confidence.
- **The four answers on a question card** (#4534) write lists, never a flag alone:
  - ✅ true: a row for each current member;
  - 👥 some know: a row for each character ticked;
  - 🤫 secret: no player character's row;
  - ❌ not true: the statement is rejected.
- **🌍 is not one of those four answers.** It is set by a World read or by the DM's "Everyone knows
  this" on the statement's own card. "The whole table heard it" is ✅.
- **A message already posted in Discord may be out of date** after the party changes. A card works
  its icons out again whenever it is opened or a button on it is pressed, and the lists and pages
  always show the current answer.
- **9b's words stay** (secret, hidden from the players, partly revealed, known to the party), read
  against the current party. "Known to everyone" is added for common knowledge.

---

## 8. What is read to the whole table

9b rule 6: anything read to the whole table holds only what every player character at that session
knows. With the party record, "at that session" has a meaning.

- **A recap of session N** is written for the party as it was at session N. Someone who joins
  later doesn't shrink an old recap.
- **A table answer today** is for the current party. Something a newcomer doesn't know is not said
  to the whole table; a player's private answer is still built from their own characters.
- **This is a real cost, and the DM is told it.** A newcomer who knows only common knowledge makes
  table answers and the next recap leave out, or keep vague (9b rule 7), what they don't know. The
  join question says so beside its first answer: "Answers to the whole table will leave out what
  she doesn't know yet (212 things)."
- **It mends itself in play:** whatever is said at the table from then on is known to all present.
- **See as… the party** shows the same: what every current member knows.

---

## 9. From the flags to the lists

The flags (`entry_shown`, `alias_shown`, `relation_shown`, `hidden_from_players`,
`audience_limited`) each mean "every player". With a newcomer that is wrong.

**No big rewrite, and no backfill beyond what exists.** The rows are already there: every flag
change has been writing them since 0283, and 0316 filled the older ones.

1. **The flag keeps one job: the DM's switch for "shown to the table now".** Turning it on writes
   a row for each **current** member (today: each confirmed player character). Turning it off is
   the DM's choice (#4524).
2. **The flag is never the answer to "may this player see it".** The gate is. A newcomer's player
   is shown what the newcomer knows.
3. **Readers move to the gate one at a time,** in this order: every AI prompt and table answer,
   the recap writer and its critics, the Story Graph, then the names and other pages.
4. **A test lists the readers that still ask a flag,** as `test_relation_readers.py` does for
   connections. The list may only shrink.
5. **Until a page has moved,** it may show a newcomer's player what the flags show. That is no
   worse than the transcripts the same player may read, and it never includes what was not shown
   to the table. It is why the AI readers move first.
6. **"Shown" and the lists can now differ on purpose:** a statement shown to the table last year
   is still "shown", and a newcomer still doesn't know it. No reader may "repair" that by adding
   rows.

---

## 10. What #4523, #4524 and #4534 must keep in mind

**#4523, the sample's who-knows keys:**

- **Three values for who knows,** not two:
  - a character by name (a backstory: its own character; an event between two: both);
  - `"party"`: each member of the party at that point, by name once expanded;
  - `"everyone"`: common knowledge, which is what the public lore's world statements are.
- **A backstory piece the public lore reveals** becomes `"everyone"` only when the lore states it
  as something the world knows. If the lore tells it as the party's own story, it is `"party"`.
- **Leave room for a join.** A later stage should add a fifth character and score that they know
  common knowledge and their own backstory and nothing of the others'. It is not needed for the
  first two stages.

**#4524, public never becomes secret by itself:**

- **"Public" gains a third case:** a row held by "everyone".
- **A change of party is not a narrowing.** A statement going from ✅ to 👥 because someone joined
  takes knowledge from no one, raises no question and must not trip the guard.
- **The guard protects the rows of characters who left,** as it does current members'.
- **The join question's writes only add,** so the guard has nothing to hold back.
- **Ruling C there** (a World read reveals what it states plainly) should write the revealed
  statement as common knowledge, since it is a World read.

**#4534, 👥 Some know:**

- **The picker lists the current party,** then "others" (characters who left, NPCs with a point of
  view) folded away.
- **✅ writes a row for each current member.** It must not rely on a flag meaning "every player".
- **The icons on the card are worked out,** by section 7's rule, and the "Known to:" line names
  current members in the party list's order.
- **🌍 is not offered on a question card.**

---

## 11. Never

- **Never give a newcomer the party's knowledge by itself.**
- **Never take a row from anyone because the party changed.**
- **Never store an icon, a word or "known to the party".**
- **Never make the party an entry or a knower.**
- **Never put the party's own story, or anything said at the table, in common knowledge by itself.**
- **Never close a span by itself:** not on a goodbye, not on a death.
- **Never use a flag to decide what a player may see** in a reader that has moved to the gate.
- **Never add rows to make the flags and the lists agree again.**
- **Never say to the whole table what one current member doesn't know.**
- **Never ask the DM about a join during play:** the question is one private message and waits.

---

## 12. Alternatives rejected

| Alternative | Why not |
|---|---|
| **Keep copying the party's knowledge to a newcomer** (#2995) | The owner: they wouldn't automatically know it. It is kept as one of the DM's three answers |
| **Read each flag as "the party as it was at time T"** | Every reader would have to rebuild old parties to answer a simple question, and the lists already hold the answer |
| **A big backfill to turn flags into rows** | The rows have been written with every flag change since 0283, and 0316 filled the rest. What is missing is the record of who was in the party, not the rows |
| **"Does not know" rows for a newcomer** | A second kind of row, in every reader, to say what the absence of a row already says |
| **Common knowledge as a row for every character** | Thousands of rows, and a newcomer or a new NPC still has none |
| **Common knowledge as a flag on the statement** | It would be a sixth flag beside the lists. A point of view fits the gate and 9b's groups as they are |
| **"The party" as a knower** | Ruled out in 9b. A newcomer would inherit everything again |
| **Removing a leaver's rows, or their role** | It loses what they knew, which the story may still turn on, and blinds their player |
| **Asking nothing and letting the DM tick each thing** | Hundreds of ticks. Three answers cover nearly every table |
| **Icons stored on the statement** | They would be wrong the moment the party changed, which is the owner's point |

---

## 13. Rules developers must keep

1. **"The party" is one function:** the current members, or the members at a given session. No
   reader lists player characters by role to mean the party.
2. **Words and icons are worked out when shown.**
3. **A span is opened and closed only by the DM,** through the change log, with Undo.
4. **A join writes no knowers** except by the DM's answer, and that answer only adds.
5. **Common rows are written only by a World read, a smart guess's "world" statements, or the
   DM.**
6. **`follow_flags` writes for the current members,** and nothing reads a flag in place of the
   gate once its reader has moved.
7. **The flag-readers test only shrinks.**
8. **The guard of #4524 covers common rows and the rows of characters who left.**
9. **Per-campaign isolation, the backup and `ON UPDATE CASCADE`** for the new table, and the new
   point-of-view kind is carried by backups and merges.
10. **The join question is a private message with one row of buttons;** "Let me choose" is a link.
11. **Discord and the website stay level:** the party list with joined and left, the join
    question's answers, "Everyone knows this" and "Tell Mira"; `docs/discord-web-parity.md` is
    updated each slice.
12. **Plain words.** The DM never sees "span", "point of view" or "knower". The ux-critic checks
    each slice's words before code.
13. **One test walks a campaign through joins and leaves** and compares the gate's answer for every
    kind of statement at each step, replacing the test that compared the flags with the party's
    view as characters joined (#2995).

---

## 14. Slice order

Each slice is its own PR with tests and a migration number claimed on #2475.

| # | Slice | Whose | What the DM gets | Proved by |
|---|---|---|---|---|
| 1 | **Common knowledge:** the "everyone" point of view; World reads and a smart guess's "world" statements write it; the DM's "Everyone knows this"; the move of stored world statements; the gate and its fast path; the 🌍 icon | GraphEngineer, WebDev | world lore that a later character will also know | the sample's public lore scored with `"everyone"` |
| 2 | **The party record and the join question:** `party_spans`, the backfill, joined and left on the character's card, "the party" as one function, the question with its first two answers, and the end of the automatic copy | GraphEngineer, WebDev | a party list with who joined and left; a newcomer who doesn't know everything | the joins-and-leaves test (rule 13) |
| 3 | **Icons and what is read to the table:** section 7's rule on every card and list; recaps and table answers use the party at that session | GraphEngineer | icons that follow the party | a recap of an old session unchanged by a later join |
| 4 | **"Let me choose", "Tell Mira", coming back** | GraphEngineer, WebDev | catching someone up a piece at a time | the choices are one Undo each |
| 5 | **Readers off the flags,** in section 9's order, with the shrinking test | GraphEngineer, WebDev | nothing new to see | the test's list reaching nothing |
| 6 | **Suggestions:** a goodbye or a death becomes an after-session question; the sample gains a join | GraphEngineer | "Has Okthandom left the party?" | the sample's join stage |

Slice 1 comes first because without it slice 2 would leave a newcomer not knowing which town is
the largest, and because #4523's keys and #4524's meaning of "public" need it now.

**Until slice 2 is in,** the automatic copy of #2995 stays as it is. It must not be removed before
the join question exists, or a newcomer would silently know nothing.

---

## 15. The five questions, answered

1. **How do we get from the flags to per-character rows without a big rewrite?**
   The rows are already written with every flag change, so no backfill is needed and no flag is
   re-read as "the party at time T". The flag keeps one job (the DM's "shown to the table now",
   writing rows for the current members), the gate becomes the only answer to "may this player see
   it", and readers move to it one at a time behind a test that only shrinks (section 9).
2. **Where is party membership over time kept, and how does it relate to the game clock?**
   In `party_spans`: one row for each stretch a character is in the party, with the session and,
   where the campaign has a clock, the game minute at each end. The clock's split groups place only
   current members, and "as of" questions use the party of that moment (section 3).
3. **Is common knowledge a group point of view or a separate marker?**
   A point of view, "everyone", whose members are every character: 9b's group rule as it stands.
   World reads write to it; the party's own story never does. It is a third kind of "public" for
   #4524, and `"everyone"` in #4523's keys beside `"party"` and names (sections 4 and 10).
4. **How is a newcomer caught up?**
   They start with common knowledge and their own backstory. The DM is asked once, privately:
   only what everyone knows, what the party knows, or let me choose. One change, one Undo; nothing
   is copied until the DM answers (section 5).
5. **Is the icon rule right?**
   Yes, against the current party and worked out when shown, with three additions: 🌍 for common
   knowledge, a party of one shows ✅ and never 👤, and 🤫 says who else knows when an NPC or a
   character who left does. A character who left keeps what they knew and is not counted
   (section 7).

---

## 16. Open questions

1. **The default while the join question waits.** As written, a newcomer knows only common
   knowledge until the DM answers, which is the safe side for spoilers and the costly side for
   table answers (section 8). The owner may prefer the question to block the first session's
   table answers less, for example by a reminder at session start. For the owner.
2. **Common knowledge of a region or a people.** "Everyone in Ten-Towns knows" is narrower than
   "everyone". The same mechanism could take a place or a people as the group, with members worked
   out from where a character is from. Left until a campaign needs it. For Supervisor.
3. **An exception to common knowledge** (a stranger from far away who wouldn't know). Not
   supported in this revision. For the owner, if it comes up at the table.
4. **What stored data can tell a world statement from a shown one,** for section 4's move.
   GraphEngineer to say what each upload's record keeps before slice 1.
5. **A player with two characters, one in the party and one who left.** Their own view is the
   union, as today. Whether a departed character's knowledge should colour that player's private
   answers is left as it is.
6. **The words** of the join question and the icons' lines, for the ux-critic.
