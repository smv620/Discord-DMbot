import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-11-03",
  title: "Import shows the connections being read",
  what: "After a past session is imported, DMbot reads the story connections in it. The Import page now shows that step: which piece it is on, how long it has run and when it last moved. If DMbot's daily limit stops it, the page says so and that it carries on by itself.",
  held: "The page shows this only once the bot that reads the connections (#3304, #3308) is deployed.",
};
