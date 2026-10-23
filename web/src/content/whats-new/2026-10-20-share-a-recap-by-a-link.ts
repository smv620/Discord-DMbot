import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-20",
  title: "Share a recap by a link",
  what: "On a posted recap's page, a game master can choose Make a link to share it. Anyone who has the link can read the recap without signing in, even people outside your Discord server, and they can pass it on. You can turn the link off or make a new one whenever you like; the old link then stops opening the recap, though anyone who already read it may keep a copy. Treat the link like a password.",
  held: "Waits for the link API (#2118) to deploy and for the page (#2119). When it is live, move this up and turn on RECAP_SHARE_LIVE in recap-words.ts (it needs RECAPS_LIVE too).",
  order: 13,
};
