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

These are the defaults every campaign starts with. They are a list the DM can view, edit, remove lines
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
5. **Scene segues:** a clean, logical transition whenever the place or the focus changes, so the
   reader always knows where the new scene is.
   - Wrong: paragraph 1 is at the inn; paragraph 2 opens "Then Belleros was struck with an arrow"
     when that fight happened on the road.
   - Right: say the party left the inn and was on the road before the arrow.
6. **Valuable reconnaissance:** scouting, tracking or investigation that gave the party information
   of real value.

### ⚔️ Action and inventory
7. **Combat summaries:** every combat, summarised, saying how easy or hard the fight was.
8. **The near-death gauge:** any moment a player character came close to death or faced extreme
   danger.
9. **Loot and asset tracking:** every major treasure, magic item or unique asset gained or lost. A
   large hoard is summed up in a short line.

### 🧠 Plot and mystery
10. **The element of surprise:** unexpected twists, betrayals and shocking dice rolls are always
    included.
11. **Narrative puzzle pieces:** the new clue given, mystery presented or puzzle solved this session.
    Say only the new step, not the whole history of the mystery.

### 🎭 Social and character (high priority)
12. **PC-to-PC development:** moments between the player characters come first: backstory reveals,
    flaws shown, new skills learned, and shifts in ideals, bonds or motivations.
13. **Weighted NPC interactions:** NPC interactions are chosen by how much the NPC matters to the
    story (background, recurring, significant, major). The ones that matter are included; passing
    ones are left out.

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
