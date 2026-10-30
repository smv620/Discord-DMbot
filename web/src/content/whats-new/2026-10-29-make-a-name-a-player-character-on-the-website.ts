import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-29",
  title: "Make a name a player character on the website",
  what: "On the Names page you can now mark a name as a player character, even if you don't know who plays it yet. If you do, paste their Discord ID: DMbot shows the name Discord has for it, and you confirm it's them, even if they haven't joined the server yet. Tap the small (i) to see how to find it.",
  held: "Waits for the deploy of migrations 0282 and 0285 and the bot that applies it (#2985); until then the page says \"That didn't work\" if you choose it.",
  order: 10,
};
