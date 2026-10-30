import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-20",
  title: "Tell DMbot what to fix",
  what: "On a recap's page, type what you'd like changed. DMbot sorts your note into points, you keep the ones you want, and it writes the recap again with them. That uses one Try again, you approve the new text again, and style notes you keep are saved for this campaign's later recaps too (the newest 10 are kept). To remove them, go to ⚙️ Manage DMbot → 📜 Recaps → ✍️ Recap style.",
  held: "Waits for the website's new routes (#2113) to deploy and for dev1 to see it work. When it is live, move this up and turn on RECAP_FIX_LIVE in recap-words.ts (it needs RECAPS_LIVE too).",
  order: 10,
};
