import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-11-21",
  title: "Leaving the party?",
  what: "When a player character says goodbye to the party in the story, DMbot asks the DM privately after the session whether that character has left the party, and changes nothing until the DM answers.",
  order: 138,
  held: "It waits until the story extractor's goodbye reading has been checked on the sample session and shows no false questions (DMBOT_PARTY_LEAVES, #4537).",
};
