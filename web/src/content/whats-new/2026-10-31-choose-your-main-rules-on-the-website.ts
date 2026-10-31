import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-31",
  title: "Choose your main rules on the website",
  what: "A game master can pick the campaign's main rules and what to use when they don't cover something, from the campaign's page. DMbot only tells you what the rules say; you decide.",
  order: 64,
  held: "Waits for the bot step that applies a rules choice to be deployed (#2985), then the server switch DMBOT_WEB_RULESETS.",
};
