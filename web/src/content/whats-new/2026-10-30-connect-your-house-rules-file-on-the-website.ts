import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-30",
  title: "Connect your house-rules file on the website",
  what: "A game master can connect a house-rules file, use another, stop using it, compare it now, or upload a text file once, from the House rules page. DMbot shows how many rules differ and tells you where to review them in Discord.",
  order: 62,
  held: "Waits for the bot step that applies a house-rules file request to be deployed (#2985), then the server switch DMBOT_WEB_HOUSE_FILE.",
};
