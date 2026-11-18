import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-11-18",
  title: "Catching up a new party member",
  what: "When a character joins a party that has already played, DMbot asks you privately after the session what the character knows about what came before. You choose only what everyone knows or what the party knows, and Undo puts it back.",
  order: 125,
  held: "It stays off until dev1 checks it on the test server after a deploy (the DMBOT_PARTY_JOIN setting).",
};
