import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-31",
  title: "Campaign invite from dndbeyond.com",
  what: "A game master can paste, change or remove their online campaign's invite link on the campaign's page. Players in your Discord server then get a Join button.",
  order: 63,
  held: "Waits for the bot step that applies an invite link choice to be deployed (#2985), then the server switch DMBOT_WEB_DNDBEYOND_INVITE.",
};
