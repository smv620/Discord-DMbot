# Design documents

Each file here is a **design basis**: the problem, the decision and its reasons, the alternatives
rejected, the rules developers must keep, the slice order and the open questions. The Expert
session writes them, Supervisor rules on them, and **once merged they are binding** for whoever
builds the feature (CLAUDE.md, "Expert"). A design note or PR that departs from one says so and
needs Supervisor's ruling.

They say what is decided. What is built and switched on is in
[`../discord-web-parity.md`](../discord-web-parity.md), [`../GO_LIVE.md`](../GO_LIVE.md) and
[`../testing-status.log`](../testing-status.log).

| Document | What it decides | Status |
|---|---|---|
| [`mapbot_layers.md`](mapbot_layers.md) | MapBot: maps kept as maps after a right-to-use yes; positions per map, with each map's own scale and north; distances, directions and travel-time suggestions; the layers (terrain, climate, route condition, control, culture, population) in `map_facts`; which map tools DMbot reads from | **Binding** (revision 2, #4274). Issue #4236 |
| `quests.md` | Quests and story threads: readers suggest them from lore and sessions; objectives, complications, rewards promised and given, help given or lent, and time limits as rows beside the thread; branches; clues and "clues found"; time limits on the game clock | **In review** (#4450). Issue #4448 |
| `session_tagger.md` | One small model tags each part of a session with the kinds of thing in it; each reader reads only its own parts; every tag is accounted for, so the DM can be told what was checked | **In review** (#4455) |
| `session_names.md` | Finding names in what was said: an AI reader finds names from the words around them, a capital letter is only a hint, spellings that sound alike are one name, and the DM confirms the spelling. Scored on the sample session as typed, with no capitals, and spoken | **Binding** (revision 1, #5087); revision 2 in review. Issues #5055, #5133, #5173 |
| [`fact_times.md`](fact_times.md) | A visit carries when it happened and when it was learned; DMbot keeps the story's own time words and counts a day only from the game clock; "last known in" is worked out by when it happened, and a visit learned late never moves someone back | **In review** (#5359) |

**The rules they share:**

- DMbot suggests and the DM decides. A reader writes its read; only the DM confirms.
- Nothing is invented: a missing value is "not known", said plainly.
- Everything is one campaign's, behind the same isolation as the rest of the memory.
- What can be worked out (a distance, time left, clues found, "stalled") is worked out when shown
  and never stored.
- What DMbot says to the DM unasked is a private message with one button at most.
- Each reader is scored on the sample keys before and after.

The wider story-memory design is in [`../STORY_MEMORY.md`](../STORY_MEMORY.md),
[`../STORY_GRAPH_DESIGN.md`](../STORY_GRAPH_DESIGN.md) and
[`../graph_design_basis.md`](../graph_design_basis.md). Decisions are recorded with their dates in
[`../PLAN.md`](../PLAN.md).
