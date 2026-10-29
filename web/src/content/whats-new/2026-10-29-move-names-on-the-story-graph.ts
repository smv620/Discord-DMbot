import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-29",
  title: "Move names on the Story Graph",
  what: "On the Story Graph you can drag a name where you want it, group names by kind, and show names up to 8 steps from the middle. Pointing at a name shows the words on its lines, and your layout is saved in this browser only.",
  held: "Waits for the web-api deploy that raises the steps limit to 8 (core MAX_HOPS, #2934); the rest works already.",
  order: 10,
};
