// The switch of the story thread's clues list on the quest sheet (#4625): the DM adds a clue, marks a false lead, takes one off, and
// Undoes each. It stays off until migration 0533 (#4703) is deployed with the bot's `clue-*` applier (bot first) and dev1 has added a
// clue, marked it a false lead, taken it off and undone each; it is flipped with docs/GO_LIVE.md, "THREAD_CLUES_LIVE".
export const THREAD_CLUES_LIVE = false;
