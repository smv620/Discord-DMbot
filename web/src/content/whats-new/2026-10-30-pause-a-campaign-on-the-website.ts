import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-30",
  title: "Pause a campaign on the website",
  what: "A campaign's owner can now pause it, and unpause it, from the campaign's page. A paused campaign keeps everything but can't start, and it frees a place on your plan.",
  order: 60,
  held: "Waits for the bot step that applies a pause choice to be deployed (#2985), then the server switch DMBOT_WEB_PAUSE.",
};
