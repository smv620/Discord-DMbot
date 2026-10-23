import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-23",
  title: "Go off the record for a while",
  what: "Press ⏸️ Pause recording me in your 🗂️ Menu (or on the reminder DMbot sends when a session starts) and choose 5 minutes, 15 minutes or Until I resume. While you are off the record DMbot doesn't record you, and the table is told with one plain line. Press ▶️ Resume now to come back sooner. The DM can take the whole table off the record for a break. You can also say 'Hey DMbot, off the record' (or type it in the DMbot chat): that takes only you off the record for 5 minutes, and what you said is not saved. Saying it in the story, without 'Hey DMbot', does nothing. The transcripts show a note like '(Mia was off the record, nothing recorded, 20:14–20:19)' (times are UTC), so a gap is clearly on purpose. A pause ends by itself, when you resume, or when the session ends, and it never changes your yes.",
  held: "In review (#2466); held until dev1 deploys it and checks it at a live table.",
};
