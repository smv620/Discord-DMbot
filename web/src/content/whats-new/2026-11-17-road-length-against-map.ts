import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-11-17",
  title: "Road lengths checked against maps",
  what: "When a road's length from your lore is much shorter than your map shows, World notes puts the two side by side, and DMbot changes neither. Setting a road's length also shows what the map says.",
  held: "Held until a deploy switches DMBOT_MAP_MEASURE on, after dev1's scored map run (pins found, wrong matches, scale and north found), which waits for the picture storage keys (#4437).",
};
