import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-19",
  title: "Stops listening when no one's there",
  what: "DMbot stops listening by itself when nobody has been in the voice channel for a few minutes, or nobody has spoken or typed for a while, so a forgotten game doesn't use up your listening hours. It warns you first on your DM screen, and you can press Keep listening. In ⚙️ Manage DMbot you choose how long \"a while\" is: 5, 10, 15 or 30 minutes (10 to start). When it stops, it sends you a private message saying why and where to change it, and tells each person still in the voice channel.",
  held: "Merged with #2038 (#2041); held until dev1 deploys it.",
  order: 1,
};
