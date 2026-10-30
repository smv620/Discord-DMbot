import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-30",
  title: "Is this the same person?",
  what: "When the story calls someone \"the guard at the gate\" and later names a guard Ken, DMbot asks you if they are the same. If you say yes, nothing new is shown to your players.",
  held: "Waits for the per-player names slice of #3037, so each player is shown only the name they know.",
};
