import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-11-18",
  title: "Catching up a new party member",
  what: "When you make a character a player character, DMbot asks since when the character has been in the party; From the first session also gives the character what the party knows. When a character joins a party that has already played, DMbot asks you privately after the session what the character knows about what came before: only what everyone knows, or what the party knows. Until you answer, the character knows only what everyone knows and what is said at the table from then on. Undo puts it back.",
  order: 125,
  held: "It stays off until dev1 checks it on the test server after a deploy (the DMBOT_PARTY_JOIN setting).",
};
