import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-30",
  title: "Pause a campaign on the website",
  what: "A campaign's owner can pause or unpause it from the campaign's page. A paused campaign keeps everything and can't start, and it doesn't count toward your plan; unpausing needs a free place on it.",
  order: 60,
  held: "Waits for the bot step that applies a pause choice to be deployed (#2985), then the server switch DMBOT_WEB_PAUSE.",
};
