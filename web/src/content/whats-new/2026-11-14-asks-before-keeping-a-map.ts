import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-11-14",
  title: "DMbot asks before it keeps a map",
  what: "When a map is posted in the table's chat, DMbot now asks whoever posted it, privately, whether they're allowed to use and save it in the campaign. Yes keeps it as a map; no and DMbot doesn't keep it. A map a player keeps waits for a DM to say it can be used.",
  order: 112,
  held: "Waiting for the kept-pictures switch (PICTURES_LIVE) and the DM's side of the question",
};
