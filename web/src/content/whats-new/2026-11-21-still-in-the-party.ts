import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-11-21",
  title: "Still in the party?",
  what: "When a player character dies in the story, DMbot asks the DM privately after the session whether that character has left the party, and changes nothing until the DM answers. \"Still in the party\" is a fine answer too: characters do come back.",
  order: 137,
  held: "It waits until DMbot records who has died in the story, which needs the story extractor, like \"Who is alive, who carries what\" (STORY_STATES_LIVE, #1284).",
};
