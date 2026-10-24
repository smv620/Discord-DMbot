import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-24",
  title: "Make a recap for a past session",
  what: "On a campaign's recaps page, you can make a recap from what was said for a session that has none, make it again, or add one that someone else wrote. Your players see nothing until you post it.",
  held: "Waits for the recap routes to be deployed (#2613, #2614 and the past-sessions list in #2631); then turn on RECAP_MAKE_LIVE in recap-make.ts.",
  order: 3,
};
