# The story graph: design philosophy

This page explains **why** DMbot's story memory is shaped the way it is, with worked examples. The rules
themselves live in [STORY_MEMORY.md](STORY_MEMORY.md) (sections 9, 9a and 9b), and the tables are listed in
[MEMORY_TABLES.md](MEMORY_TABLES.md). When this page and those disagree, those win. All names in the examples
are invented.

## The problem it solves

A tabletop campaign is not a database of facts. At any moment:

- the DM knows the truth, and players know only part of it;
- each player character knows different things, and keeps secrets from the others;
- NPCs lie, items are forged, and illusions show what isn't there;
- players form theories, some right and some wrong;
- what was true in session 3 may not be true in session 12.

A plain graph of "facts" ("Lucian works for the Bishop") can't hold any of that. It has to either throw away
history, or show players what their characters don't know. DMbot's graph is built so that **disagreement,
doubt and secrecy are normal data, not errors**.

## Seven ideas

### 1. A connection is a statement, not a truth

Every connection in `memory_relations` (Bruno → desires → Shela) is a **statement that someone could make**.
Its row id is the statement's identity. Nobody is assumed to believe it just because the row exists.

That one choice makes the rest possible: the same statement can be believed by one character, doubted by
another, and denied by the DM, all at once, without three copies of it.

### 2. Points of view hold beliefs, each with a confidence

A **point of view** (`memory_perspectives`) is someone who can believe things:

- **the DM's view** (`truth` in code);
- **the party**;
- **each player character**;
- **an NPC whose beliefs matter**.

A **belief** (`memory_beliefs`) is one point of view's confidence in one statement, from 0.0 (certainly
false) to 1.0 (certainly true), with a status (`theory`, `accusation`, `revealed_fact`, `false_evidence`,
`secret_plot`, `magically_verified`, `evasion_under_magic`) and the evidence it rests on.

> **Example: a love triangle.**
> - "Bruno → desires → Shela": Bruno 1.0 (he knows his own heart), Shela 0.95 (she has noticed).
> - "Shela → desires → Bruno": Bruno 0.3 (he hopes so), Shela 0.0 (she doesn't).
> - Tran has no belief in either. He doesn't know of them at all.
>
> Two statements and four beliefs describe exactly what each person thinks. Nothing contradicts anything:
> people simply disagree, which is the story.

**Different points of view disagreeing is never a contradiction.** A contradiction is only two clashing
statements held by the **same** point of view at overlapping times (STORY_MEMORY §9a). That keeps the DM's
warnings for real mistakes ("Kira is dead and also buying bread"), not for plot.

### 3. Nothing is overwritten: changing your mind adds a row

When a belief changes, DMbot **adds** a new belief row with a higher `seq`; the newest row is the current
belief, and the old ones stay as history. Every change the DM or DMbot makes is also written to the change log
(`memory_changes`, with before and after), and **Undo is itself a new change**, never an erased one.

> **Example: the doppelganger.**
> - **Session 1:** Cerric meets his sister. "Valyra → is the sister of → Cerric": Cerric 1.0, `revealed_fact`.
> - **Session 15:** in a hidden chest Cerric finds a disguise kit that smells of Valyra's perfume. DMbot adds:
>   - a new belief, Cerric 0.6, `theory`, on the same sister statement (his certainty drops, and the 1.0 stays
>     in history);
>   - a second statement, "Jax → disguised as → Valyra", Cerric 0.4, `theory`, with the chest as evidence.
> - **The DM's view** already holds the second statement at 1.0, as a `secret_plot`.
>
> Ask "what did Cerric believe in session 10?" and the answer is the session 1 row. Ask "what does he believe
> now?" and it is the session 15 rows. Ask the DM's screen "where is the party wrong?" and it shows that Cerric
> half-trusts an impostor. Nothing was lost, and nothing had to be patched.
>
> Note that the impostor is a **second person**, Jax, with a disguise, not a property of Valyra. The real
> Valyra may be alive somewhere, and Jax can have his own beliefs, connections and secrets.

### 4. Two clocks: when it is true, and when it was believed

Every belief and connection carries **story time**: when the thing is true in the story, by session, and by
game time when the DM keeps a game clock. It also carries **belief time**: when that point of view started
and stopped believing it. `recorded_at` is a third, separate fact: when DMbot learned it.

> **Example: a correction made later.** In session 8 the DM says, "Actually, the bridge was already destroyed
> when you crossed the river in session 5." DMbot records the bridge's `destroyed` state from session 5 (story
> time) while `recorded_at` says session 8. So "what was true in session 5?" and "what did DMbot know in
> session 5?" get different, correct answers.

There is **one timeline per campaign**. Dreams, visions and other planes are points of view or places, never
separate timelines. Missing game time is treated as "may overlap", and warnings say so.

### 5. Who knows what is a list, not a flag

Instead of "secret: yes/no", **every statement has a list of the characters who know it**, each with a
confidence (STORY_MEMORY §9b). The words players and DMs see are worked out from that list, never stored:

| Who knows it | Word shown to the DM |
|---|---|
| nobody but the DM | secret |
| some NPCs, no player character | hidden from the players |
| some player characters, not all | partly revealed |
| every player character | known to the party |

> **Example: a check that finds something.** Okthandom rolls Investigation and finds the forged signature. That
> is a reveal **to Okthandom only**. When his player says "I show the others", it becomes known to every
> player character. Mira's player, who missed the session, still sees it, because her **character** was in the
> scene.
>
> The next recap mentions it without spoiling anything for the others: "Okthandom learned something about the
> forged letter."

### 6. One gate decides what each viewer sees

Every reader of story memory goes through **one gate**: the Story Graph, names and NPC pages, table answers,
recaps and every AI prompt. A test fails if a new reader skips it.

- **A player** sees what their own characters know.
- **The DM** sees everything, and can look **as** any character.
- **Anything read to the whole table** (recaps, `dmb-chat`) shows only what every player character knows.
- **An AI prompt** gets only the slice for whoever the answer is for.

> **Example: one person, two names.** The party meets "the guard at the north gate". Weeks later the DM
> confirms the guard was Ken, the duke's spy, and joins the two entries. Okthandom only ever met the guard.
>
> On his player's Story Graph, the guard stays **a separate person**, carrying the links Okthandom learned
> through the guard. Ken appears only with what is publicly known about Ken. The player sees two people,
> because that is what Okthandom believes. The DM sees one. When the DM makes "the guard" a public other name
> of Ken, the two fold into one for everyone. (Built in #3071, #3072 and #3085.)
>
> A label never leaks a hidden name either. If Ken were hidden, his unnamed sister shows to players as
> "someone's sister (name unknown)", never "Ken's sister".

### 7. DMbot proposes; the DM decides

The AI reads sessions and lore and **proposes**: names, connections, beliefs, who learned what. Nothing it
proposes is a fact until the DM keeps it. The only absolute truth is what the DM **pins** as 100% truth (📌),
and its only job is to warn the DM privately when play drifts from what the DM intended.

> **Example: worked-out family links.** The DM confirms that Lia is Mira's mother and Tam is Mira's brother.
> DMbot works out that Lia is Tam's mother too, and **asks**: "Worked out from Lia and Mira, and Mira and Tam,
> not stated." It is a proposal until the DM presses ✅. If the DM presses ❌, it is never suggested again. If
> one of the links it came from is removed, the suggestion goes too.

## Supporting rules

- **Stored once, read both ways.** "Mira → is the niece of → Aldric" is one row. Aldric's card reads it
  backwards as "has a niece, Mira". Family roles keep the words the DM or the story used: DMbot never guesses
  anyone's gender to choose "brother" or "sister".
- **Evidence is typed and erasable.** A belief points at the claim, transcript line, clue or event it rests on
  (`memory_evidence`). When a player deletes their lines, every link to them becomes a placeholder ("a line
  from session 12, no longer available"), and the words are gone. The append-only history never overrides a
  person's right to delete what they said.
- **Structure is enforced; story is warned about.** A connection must join two names in the same campaign.
  That is checked by the database, and a bad write is refused. "One unique sword in two hands" is only a
  **warning**: it might be a theft, a handoff or a mistake, so the fact is stored and the DM decides.
- **Every campaign is its own world.** Every table carries the server and campaign, with forced row-level
  security and composite foreign keys. Two campaigns are never compared, even on the same server and even when
  they share a name.
- **Postgres, not a graph database.** The graph is a set of ordinary tables. That keeps backups, Undo,
  isolation and deletion in one place, and the graph-shaped questions DMbot asks are small enough for SQL.

## Strengths, in short

| Strength | Because of |
|---|---|
| Lies, theories and secrets sit side by side without corrupting each other | statements are separate from beliefs (ideas 1 and 2) |
| "What did the party believe in session 10?" has an exact answer | beliefs are added, never overwritten, and carry two clocks (ideas 3 and 4) |
| Each player sees only what their character knows | the knower lists and the one gate (ideas 5 and 6) |
| Recaps carry partial reveals without spoiling them | the knower lists say who knows each thing |
| Real mistakes are caught; plot twists are not flagged as mistakes | contradictions are checked only within one point of view |
| The AI can't invent canon | it only proposes; the DM keeps or pins (idea 7) |
| A player's deleted words are really gone | evidence is typed and turns into placeholders |
| One campaign can never leak into another | everything is scoped by server and campaign in the database |

## What comes next

[#3091](https://github.com/smv620/Discord-DMbot/issues/3091) adds three things:

1. **Lineage:** each belief records the belief it came from and why (an update, a competing theory, a
   retraction, or being told by someone). So "everything the spy told us" can be traced when the spy is
   unmasked.
2. **Game-world dates:** game time is shown in the setting's own calendar ("Flamerule 14th, 1492 DR").
3. **A status for lies:** `suspected_lie` and `known_lie`, so "Cerric thinks Valyra lied" is different from
   "Cerric was mistaken".
