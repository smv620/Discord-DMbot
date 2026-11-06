import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-30",
  title: "DMbot asks before it adds another name",
  what: "When your lore gives a character you already have another name, DMbot asks \"Is this another name of them?\" with the line from your text. Nothing uses the name until you say yes, and a no is remembered.",
  order: 24,
  held: "Waits for the deploy of the lore reading (#3022).",
};
