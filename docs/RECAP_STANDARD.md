# Recap standard: the "must haves" of a session recap

Owner, 2026-11-03. Recorded by Supervisor. **Owner of the standard's implementation: CloudDev-lead**
(the recap writer, its instructions and checks); WebDev-lead builds the page where a DM edits the list.

## Purpose

This standard is the framework every session recap is written to and judged by. It names what a recap
must capture so that someone who missed the session can follow:
- what moved forward;
- what the characters went through;
- where the party went.

It also keeps the recap useful to DMbot's own campaign memory.

It works with the recap rules already in `docs/PLAN.md` ("A recap is a short story someone who missed
the session can follow", "Every claim is checked against the beats"). Two of those rules always come
first:
- **DMbot never invents story.** Every item below is included **when the session gave it**: in the
  beats, the cleaned transcript or confirmed story memory. Where the session didn't say something (a
  compass direction, how long a journey took, how a character felt), the recap leaves it out rather than
  guessing. A must-have the session didn't give is skipped, never filled in.
- **Lengths are targets, never checks** (CLAUDE.md, #2597). The must-haves decide *what* goes in, not
  how long it is.

## The default must-haves

These are the defaults every campaign starts with (five lines reworded in plainer words, owner,
2026-11-03, from the ux-critic's suggestions on #3328). They are a list the DM can view, edit, remove lines
from and add lines to, on the website page where the DM gives recap examples (the Recap style page).
Removing every line brings the defaults back, the same way the style examples work.

### 📑 Framing (the bookends)
1. **The launchpad:** the scene, the location and the party's emotional state at the very start of
   the session.
2. **The destination:** the party's physical position and situation when the session ended,
   described clearly.
3. **The cliffhanger:** if the session ended on a cliffhanger or a moment of tension, the recap ends
   on it.

### 🧭 Navigation and travel
4. **Precise travel logs:** a short, accurate account of every change of place, with the route, the
   compass direction and the time the journey took, as the session stated them.
5. **Scene changes:** whenever the place or the focus changes, say so, so the reader always knows
   where the new scene is.
6. **Scouting and searching:** scouting, tracking or investigating that gave the party information
   worth knowing.

### ⚔️ Action and inventory
7. **Combat summaries:** every combat, summarised, saying how easy or hard the fight was.
8. **Close calls:** any moment a player character came close to death or was in great danger.
9. **Loot and treasure:** every major treasure, magic item or other prize gained or lost. A large
   hoard is summed up in one short line.
14. **Every encounter ends:** any encounter the recap mentions (a fight, ambush, threat spotted,
    creature or stranger met) says how it ended, even a trivial one: fought off, fled, avoided,
    talked down, or came to nothing. If the session stopped first, the recap says so, and that is
    the cliffhanger (3). (Owner, 2026-11-06, #3632. It is number 14 because it was added last; it
    sits with the action lines. Worded to fit the 300-letter line limit.) It works with "never
    invented": if the session did not say how an encounter ended, the recap leaves the encounter
    out or says plainly that the session did not say. It never guesses "they drove them off".

### 🧠 Plot and mystery
10. **The element of surprise:** unexpected twists, betrayals and shocking dice rolls are always
    included.
11. **Narrative puzzle pieces:** the new clue given, mystery presented or puzzle solved this session.
    Say only the new step, not the whole history of the mystery.

### 🎭 Social and character (high priority)
12. **PC-to-PC development:** moments between the player characters come first: backstory reveals,
    flaws shown, new skills learned, and shifts in ideals, bonds or motivations.
13. **Characters the party meets (NPCs):** include the ones who matter to the story and leave out
    the ones who only pass by.
15. **Every name earns its place:** anyone the recap names gets a nod to why they matter in this
    scene: what they did, or how they tie in. A name that played no part in the session's events is
    left out, not dropped in as a label. If one is used, it is tied to the moment ("Cerric loosed
    magic upon their foes; Syllar Sparkwalker taught him well."). Nothing is invented to give a
    name a reason: if nothing in the session or the players' view supports one, the name goes.
    (Owner, 2026-11-06, #3664. It is number 15 because it was added last, after 14.)

## Secret connections in a recap (owner, 2026-11-06, #3664; changes #2591)

A pair of characters with a connection the DM keeps secret may appear in the same scene. The recap
may give a **cryptic nod** ("a familiar look passed between them") but never states the connection
and never explains how they know each other; the DM reads the script before the table does. A
**secret name** never appears at all: the name checks, near-secret spelling refusals and retries are
unchanged.

## Must-haves and recap examples (owner, 2026-11-03)

The **must-have list decides what goes in** a recap. The **recap examples** on the same page (the DM's
own, or the four built-in ones) **decide only how it sounds**: tone, rhythm and word choice. A stand-alone
example can't show whether a recap caught a session's key points, so examples are never used to judge
coverage, and the page labels them "How your recaps should sound".

## How the standard is used

- **Writing.** The campaign's current list goes to the recap writer with its instructions. Each line
  is a thing to include when the session gave it, so the writer covers the must-haves before anything
  else and in the story's own order.
- **Checking.** After the draft, the existing coverage check also reads the list. For each must-have
  the session gave (for example, a combat in the beats), it confirms the draft covers it, and a missed
  one goes to the one revision, as missed beats already do. Because missing a must-have is a coverage
  gap, it never fails a recap for length (#2597) and never outranks the fact check.
- **Scoring (how quality is judged).** CloudDev-lead keeps a fixed set of sessions from the owner's
  table (counts and invented names only on GitHub) and scores every change to the recap writer
  before and after: for each must-have the session gave, covered or missed, plus the fact-check
  result. A change that covers fewer must-haves, or adds unsupported sentences, doesn't merge without
  a reason.
- **Isolation.** Each campaign's list is its own. It goes in that campaign's backup and is never
  shared with another campaign.
