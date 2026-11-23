# Design basis: finding names in what was said

**Status:** revision 1, 2026-11-23, written at the owner's request. **Not binding until Supervisor
approves it.**

**Who builds it:** GraphEngineer owns the name readers and the scan (`core/src/dmbot/memory/`).
CloudDev-lead owns the transcript, the Cleaner and speech-to-text (`core/src/dmbot/transcript/`,
`transcription/`). dev1 runs the sample.

**Read with:** `core/src/dmbot/memory/scan.py`, `transcript/cleaner.py`, `transcript/name_pass.py`;
issue #5055 (which names a sample run missed, and why) with #5082 and #5086;
`docs/design/session_tagger.md`.

## Contents
1. [Problem and context](#1-problem-and-context)
2. [The decision](#2-the-decision)
3. [Where a new name can come from](#3-where-a-new-name-can-come-from)
4. [The name reader](#4-the-name-reader)
5. [One name heard several ways](#5-one-name-heard-several-ways)
6. [What the DM is asked](#6-what-the-dm-is-asked)
7. [Names DMbot already has, heard wrong](#7-names-dmbot-already-has-heard-wrong)
8. [Scoring: three versions of the sample session](#8-scoring-three-versions-of-the-sample-session)
9. [Never](#9-never)
10. [Alternatives rejected](#10-alternatives-rejected)
11. [Rules developers must keep](#11-rules-developers-must-keep)
12. [Slice order](#12-slice-order)
13. [Open questions](#13-open-questions)

---

## 1. Problem and context

**The owner (2026-11-23):** "The STT engine will not know to capitalize words so relying on
capitalization is a flaw."

**Three places decide "this word might be a name" by its capital letter:**

| Where | What it does | Its rule |
|---|---|---|
| **The after-session scan** (`memory/scan.py`) | suggests names DMbot doesn't know yet | a word or run of words written with a capital in the middle of a sentence, **never written in lower case** anywhere in the session, not a common word or game term, heard at least twice (`MIN_TIMES`), at most three words long |
| **The Cleaner, live** (`transcript/cleaner.py`) | fixes a known name that was heard wrong | "a word counts as unknown when speech-to-text gave it a capital" |
| **The name pass** (`transcript/name_pass.py`) | after a session, asks which known name a misheard word means | its candidates are "the capitalised words and runs" |

The scan says why: "Speech-to-text writes names it doesn't know with capitals." Nothing promises
that. The service is asked for punctuation and tidy formatting (`transcription/deepgram.py`), which
gives a capital to the start of a sentence and to names it recognises. An invented name is what it
will not recognise.

**For a name the service has never seen, each part of the scan's rule is likely to fail:**

- it comes out in lower case, or with a capital one time and not the next, and **one lower-case
  occurrence rules it out for the whole session**;
- it is spelled differently each time, so no spelling is heard twice;
- it is split into pieces, or written as ordinary words.

**What is already measured** (the sample, 15 runs, #3545):

- "Dwarven Valley" is stored as "Valley" in 15 of 15 runs, because "dwarven" is also said in lower
  case.
- "Kelvin's Cairn", "Explorer's League" and "Aurora's Wind" are stored as "Kelvin Cairn",
  "Explorer League" and "Aurora".
- Things the story never gives a capital are never found: the gemstone mine, the azure tome, the
  ancestral journal.

**And what is not measured at all:** the sample session is a typed transcript that is imported, so
its names arrive with a person's capitals and one spelling each. The numbers above are the scan at
its best. No run has tested it on what speech-to-text writes.

**What limits the harm today:** names DMbot already has are sent to speech-to-text as hints, so
they are heard right and matched as known names. The scan is the fallback for names nobody gave
DMbot first, such as an NPC the DM makes up at the table, and that is where speech-to-text is
least reliable.

**Checked in the code with GraphEngineer (2026-11-23):**

- **With no capitals in the middle of a sentence the scan offers nothing.** `scan._is_name_word`
  needs the word to start with a capital, and a word capitalised only at a sentence start never
  counts. `name_longer` only widens what the scan found, so it adds nothing either.
- **The name pass gets nothing too:** it takes its candidates from the same scan. The Cleaner's
  unknown-word test is the same check on the first letter.
- **One source does not look at capitals:** the story reader's own new names (up to 20 a session).
  So the no-capitals baseline will not be zero, and whatever it finds is that reader's.
- **Spellings are grouped after the "heard twice" test today,** not before. "Rothgar" heard once
  and "Hrothgar" heard once are both dropped before they can be joined.
- **A typed import and a no-capitals import run the same code;** only the text differs. An import
  has no speech-to-text, and so no hints.

**Not covered here: names missed in uploaded lore.** Whether the reader never listed them or a rule
dropped them is not known until the per-name trace exists (#5055). That is not designed from a
guess, and the trace comes before any change to the lore read.

---

## 2. The decision

1. **A name is found by what is said about it, never by how it is written.** After a session, an
   AI reader goes through the transcript and lists who and what is named, from the words around
   it: an introduction, a form of address, a place travelled to, a thing the story treats as
   particular.
2. **A capital letter is a hint and nothing more.** It is never needed, and a lower-case
   occurrence never rules a name out.
3. **Spellings that sound alike are one name.** They are grouped by sound before anything is
   counted, so three spellings of one name are three hearings of it.
4. **The DM confirms the name and its spelling.** Speech-to-text's spelling of a new name is a
   guess, and the suggestion says so.
5. **Rules check; they do not find.** Every suggested name must appear, as written, in the lines it
   is cited from. That guard stays exactly as it is.
6. **It is tested on three versions of the sample session:** as typed, with the capitals taken
   out, and spoken through speech-to-text.

**Why:**

- **Speech has no capitals.** Any rule built on them measures the transcription service's habits,
  not the story.
- **Context is what a person uses.** "A dwarf called …", "we ride for …", "she gives her name as
  …" say a name is a name whatever its letters look like.
- **The guard already stops invention.** The AI reader can only point at words that are there.
- **A name the DM confirms once is fixed for good:** it becomes a hint to speech-to-text, and its
  misheard spellings become spellings the Cleaner corrects.

---

## 3. Where a new name can come from

A candidate may come from any of these. They are joined, and one name found by several is one
suggestion.

| Source | What it gives | How far it is trusted |
|---|---|---|
| **The name reader** (section 4) | a name with the words that show it is one | the main source |
| **Typed lines** (the table's chat, the DM's typed notes to DMbot) | a name as a person typed it | **the best source of spelling:** a typed spelling beats any heard one |
| **The story reader's own claims** | names that appear in what it found (up to 20 a session) | as today. It is the only source today that doesn't look at capitals |
| **The capital-letter scan** | a run of capitalised words | a hint only. It adds a candidate; it never removes one, and it gets no say in spelling over a typed line |

**The scan's rule is loosened, since it no longer decides alone:**

- **"Never in lower case" is dropped.** A word also said in lower case may be a name (#5086 already
  lets it lead a longer one).
- **A run may include small joining words** ("Spine of the World", "Heart of Chardalyn"). The
  joining words don't count toward the scan's three-word limit (`MAX_WORDS`), which today drops a
  longer run whole.
- **A possessive inside a name is kept** when the longer form is what was said ("Kelvin's Cairn").
- **A name that is also an ordinary word** ("Hope", "Boy", "Valley") needs the name reader's
  evidence that it is used as a name. The scan alone never suggests it.

---

## 4. The name reader

**It reads** every batch of the finished session, in order, with a few lines before each for
context. Names are never routed by the tagger (`docs/design/session_tagger.md`: "no tag for
names"), so it reads every line.

**It is asked** for each person, place, group, creature or particular thing the lines **name or
single out**, and for each:

| It returns | Rule |
|---|---|
| the name, **exactly as the lines write it** | checked by code: it must be in the lines cited, letter for letter apart from capitals |
| what kind of thing it seems to be | one word from the fixed kinds; a guess the DM corrects |
| the lines it is in | must be lines of this batch |
| why it is a name, as one fixed word | **introduced** ("called", "named", "this is"), **addressed** (spoken to by it), **place** (gone to, come from, pointed at), **spoken of** (talked about as someone or something particular), **singled out** (a thing or place the story treats as one of a kind, with no name of its own) |

**What it must not list:** the people at the table, a known name of the campaign (those are given
to it so it can leave them out), a rules word (a spell, a creature of the rules, a condition), a
word used only as an ordinary word, and anything it would have to guess.

**Singled out, with no name** ("the gemstone mine", "the azure tome", "the woman with the sad
look"):

- a person goes to the existing unnamed entries, as lore does (`unnamed_read.py`);
- a thing or a place is suggested under the story's own words, a few for each session at most,
  only when the story comes back to it or builds on it. This is the rule lore already has for
  special objects (#3157), applied to sessions.

**How it is run:**

- the fast model, one call a batch, with the instructions cached;
- consent is checked before each batch and after each answer;
- the lines are data: a line that reads like an instruction changes nothing, because the answer is
  checked against the fixed kinds, the fixed reasons and the batch's own lines;
- an unreadable or cut answer is retried, and a batch that still fails is reported as not read,
  never as "no names";
- it is metered AI use, and the wait shows its stage and how far it has got.

---

## 5. One name heard several ways

Before anything is counted or suggested, in this order:

1. **Is it a known name heard wrong?** Each candidate is compared with the campaign's names by
   sound and by letters (the scan's `NEAR` thresholds, as today). If it is close to exactly one,
   it is not a new name: it goes to the name pass (section 7). If it is close to two, it is asked
   as "which one?", as today.
2. **Group what is left by sound.** Candidates that sound alike and are of one kind become one
   name with several spellings (`scan.sound_keys` and `group_alike` exist for this). "Hrothgar",
   "Rothgar" and "hroth gar" are one name heard three times. **This changes the order:** today the
   "heard twice" test runs first and grouping after it, so spellings heard once each are lost
   before they can be joined. Whether a two-word spelling passes the fold test against a one-word
   one is to be proved by a test, not assumed.
3. **Join split names.** Two short words that together sound like a spelling in the group belong
   to it.
4. **Count hearings for the group,** not for each spelling.
5. **Choose the spelling to show:** a typed spelling if there is one; otherwise the spelling heard
   most; otherwise the first heard. The others are kept as "also heard as".

**How many hearings are needed:**

| How it was found | Needed |
|---|---|
| introduced by name ("a dwarf called …") | once |
| typed by a person | once |
| addressed, a place, or spoken of | twice, in the group |
| the capital-letter scan alone | twice, and it is not an ordinary word |

**A name close to a secret name** is dropped whole and never asked about or written, as the name
pass does today.

---

## 6. What the DM is asked

After the session, in "📝 Check new names", as now. The suggestion changes in three ways.

> 📝 **Hrothgar**: a new name? Heard 5 times, also as *Rothgar*, *hroth gar*.
> "…a dwarf called hrothgar steps out of the smoke…"
> **[Yes, spelled Hrothgar]** **[Spelled another way…]** **[Not a name]**

1. **It says the spelling is a guess** whenever it came from speech and not from a typed line, by
   showing the other spellings heard and offering "Spelled another way…".
2. **It shows the words that made it a name,** one short line, with the same consent rule as
   every quoted line.
3. **It is ordered by how sure DMbot is:** introduced or typed first, then by times heard.

**When the DM says yes:**

- the name is kept under the spelling the DM chose;
- every other spelling heard becomes a "said as" spelling of it, so the Cleaner corrects it from
  then on and the session's cleaned transcript is fixed (never the raw one);
- the name goes to speech-to-text as a hint from the next session.

**When the DM says "Not a name":** every spelling in the group is remembered as not a name, so none
of them is asked again.

**Limits:** a session still sends a bounded number of new names at once, and the rest wait behind
"📝 More names" (#5082). Nothing is dropped for being over the number.

**Nothing is asked during play.**

---

## 7. Names DMbot already has, heard wrong

The same flaw sits in the two places that fix a known name.

- **The name pass** (after the session, AI decides): its candidates stop being "capitalised words".
  They become every candidate from section 5, step 1: anything the name reader or the scan found
  that is close to a known name. The decision is unchanged: which of the offered names it means, a
  new name, or not sure, and only a sure answer is written.
- **The Cleaner** (live, code only, silent when near-certain): it treats only a capitalised word as
  unknown. A known name heard in lower case and slightly wrong is left as heard.
  - It can't simply drop the capital test: without it, an ordinary word that sounds like a name
    ("baron" beside a character called Barron) would be rewritten.
  - So the Cleaner gets a second way to know a word is unknown: **it is not in a list of ordinary
    words.** That needs a word list DMbot may bundle (section 13, question 1). Until then the
    Cleaner stays as it is, and the name pass, which has context, catches these after the session.
  - Its other rules don't change: silent only when near-certain, a note and Undo for a close
    look-alike, and a wrong fix is worse than a missed one.

---

## 8. Scoring: three versions of the sample session

The same session and the same answer key, read three ways.

| Version | What it is | What it tests |
|---|---|---|
| **Typed** | the sample session as it is today | that nothing found today is lost |
| **No capitals** | the owner's edit of it, with the capital-letter clues taken out | names found from context alone |
| **Spoken** | the session read aloud and put through speech-to-text | the real case: no reliable capitals, and spellings that vary |

**Counted for each version, per run:**

- **found:** key names stored, under the key's name or one of its accepted wordings;
- **found, wrong shape:** a part of the name ("Valley"), or a name with a piece missing;
- **missed;**
- **extra:** suggested and not in the key;
- **for the spoken version only:** how each key name was heard (below).

**A description of the input, printed beside every score** (GraphEngineer's count): for each key
name, how the transcript writes it: with a capital in the middle of a sentence, with a capital only
at the start of a sentence, or in lower case. This describes the transcript, not the reader. In
the no-capitals version every key name should show as lower case or sentence-start only; if one
still has a capital mid-sentence, the baseline is not what it claims to be.

**How the spoken version is measured: by lining it up with the script** (worked out with
GraphEngineer, 2026-11-23). The spoken version is a reading of a text DMbot already holds, the
typed sample session, so where each key name is said is known.

- **The speech-to-text output is aligned to the script,** turn by turn, word against word, with
  capitals ignored. For each place the script has a key name, what speech-to-text wrote there is
  read off.
- **Each occurrence of a key name is then one of:** as written; heard another way (with the
  spelling heard); dropped; or not aligned (the reader left the script in that turn). Whether it
  carried a capital is noted beside it.
- **"Not aligned" is never counted as missed,** and a name the recording never says is known from
  the alignment, not guessed.
- **From that come the spoken counts:** how many key names were heard under more than one
  spelling, how many of those the product grouped as one, and how many it found at all.
- **The measurement shares no threshold with the code it measures.** The alignment needs none.
  Where a turn can't be aligned, a likeness-and-sound match may be used as a fallback, with its own
  looser thresholds, never the scan's `NEAR` values.
- **The list of (key name, spelling heard) pairs is checked by hand.** It is short, it holds public
  sample names only and never a transcript line, and it is the measurement's own margin of error.
- **This needs the recording to follow the script** (section 13, question 2).
- **None of this is needed for the typed or the no-capitals versions:** each has one typed spelling
  for each name, so "heard another way" is nil by construction.

**The bar:**

- **Typed:** no fewer found, and no more extra, than before the change.
- **No capitals:** as many found as the typed version finds today. This is the direct test of the
  owner's point, and the first slice is judged on it.
- **Spoken:** reported at first, with no bar, since it has never been measured. A bar is set once
  two deploys' numbers are in.

**Other rules:**

- Each version is its own series in the reports; numbers are never mixed across versions.
- The per-name trace (#5055) covers this path too: every candidate ends with a fixed reason
  (listed, not in the lines, a known name, grouped into another, too few hearings, an ordinary
  word, over the number, kept, turned down), so a miss can be named and explained.
- The spoken version is audio of the public sample only, read by people who agreed to it, as the
  speech-to-text test recordings are.

---

## 9. Never

- **Never suggest a name that is not in the lines,** letter for letter.
- **Never need a capital letter,** and never rule a name out for a lower-case occurrence.
- **Never present a heard spelling as certain.**
- **Never count spellings that sound alike as different names.**
- **Never write a new name by itself.** The DM confirms the name and its spelling.
- **Never change the raw transcript.**
- **Never ask about, or write, anything close to a secret name.**
- **Never drop a name for being over a number:** it waits.
- **Never ask during play.**
- **Never loosen the live Cleaner's "near-certain" rule** to catch more.

---

## 10. Alternatives rejected

| Alternative | Why not |
|---|---|
| **Keep the capital rule and patch its cases** (#5086 and after) | Each patch fixes one shape. The premise, that speech-to-text marks unknown names with capitals, is what is wrong |
| **Ask speech-to-text to do better** (more hints, another service) | Hints only help names DMbot already has. A new name is new to any service |
| **A dictionary: anything not an English word is a name** | It finds mishearings of ordinary words by the hundred, and misses every name that is also a word. Useful only as the Cleaner's narrow test (section 7) |
| **Let the name reader's answer stand without the check** | A model can write a name that isn't there. The check is why DMbot never invents one |
| **One spelling, the most common, with no question** | The most common mishearing would become the campaign's spelling for good |
| **Find new names live, during play** | Nothing is asked during play, and a few seconds of talk is the worst place to judge a name |
| **Route names through the tagger** | A missed tag would be a missed name. Names read every line |
| **Put the name reader inside the story reader's call** | A name must not depend on the story reader finding a fact about it |

---

## 11. Rules developers must keep

1. **The check is code and comes last:** a suggested name is in its cited lines, compared without
   regard to capitals. Anything else is dropped and counted with its reason.
2. **No test anywhere requires a capital letter for a word to be a name,** and none rules a word
   out for appearing in lower case. A CI test lists the functions that look at a letter's case in
   the name paths, and the list may only shrink.
3. **Grouping by sound happens before counting and before any limit.**
4. **A typed spelling outranks a heard one.**
5. **A reader suggests; the DM confirms the name and the spelling.**
6. **Every candidate ends with a fixed reason** in the dev-tools trace; production keeps counts.
7. **Consent before and after each call;** a quoted line follows the consent rule for quotes.
8. **Counts only** in logs and in the DM Bot channel.
9. **The three versions are scored apart,** and a change to a name reader posts all it has.
10. **Discord and the website stay level** for the changed suggestion, and
    `docs/discord-web-parity.md` is updated.
11. **Plain words.** The DM sees "heard as" and "spelled", never "candidate", "cluster" or
    "variant". The ux-critic checks the words before code.

---

## 12. Slice order

Each slice is its own PR with tests, and its numbers on the versions that exist.

| # | Slice | Whose | What changes for the DM | Judged on |
|---|---|---|---|---|
| 1 | **The "no capitals" version in the sample run,** scored beside the typed one, with today's code: a version choice on the sample run (typed, no capitals, spoken) with its own campaigns and summaries, the "wrong shape" count, and the input description | dev1, GraphEngineer | nothing | the baseline: how far today's readers fall with no capitals, and which reader found what is left |
| 2 | **The name reader,** joined with the scan and the story reader's names; the check; the loosened scan rule; the trace's reasons | GraphEngineer | new names found from context | no capitals: at least today's typed count. Typed: nothing lost |
| 3 | **Grouping by sound, and the new suggestion** ("heard as", "spelled another way"); other spellings become "said as" on a yes | GraphEngineer, WebDev | one question for one name, with its spelling | no doubled names; the typed version unchanged |
| 4 | **The name pass's candidates** come from the readers, not from capitals. It first settles whether the name pass runs on an imported session at all: if it doesn't, the sample can't score this slice until it does | GraphEngineer, CloudDev-lead | known names heard in lower case are fixed after the session | the no-capitals version: known names corrected |
| 5 | **The spoken version of the sample** | dev1, with the owner's recording | nothing | the first real numbers |
| 6 | **Singled-out things and places** from sessions | GraphEngineer | "the gemstone mine" offered | extra names stay low |
| 7 | **The live Cleaner's second test,** if question 1 is answered | CloudDev-lead | known names fixed live in lower case | no wrong live fixes on the spoken version |

Slice 1 is first because it costs almost nothing and turns the owner's point into a number.

---

## 13. Open questions

1. **A list of ordinary words for the Cleaner** (section 7). It must be one DMbot may bundle in a
   public repository, with its licence stated. For Supervisor, before slice 7.
2. **Who records the spoken version, and how.** Proposed: the table's own voices, reading the
   sample session's script as written, with no ad-libbing, so the recording can be lined up with
   the script (section 8). One reader would do, but several voices test what a real table sounds
   like. For the owner.
3. **The cost of one more call a batch.** The measured figure for a comparable call is about $0.36
   to $0.73 for a four-hour session (`docs/costs.md`). If slice 1 of the tagger design shows the
   name reader and the tagger could share one read safely, that is a later saving, not a reason to
   wait.
4. **An ordinary word used as a name with no introduction** ("Boy", the dog). It is found only when
   the name reader says it is addressed or spoken of as a name. Whether twice is enough for these
   is for the sample to show.
5. **#5086: answered.** Supervisor ruled that it merges as it is. It helps the typed version now,
   and section 3 builds on it.
6. **The words** of the new suggestion, for the ux-critic.
