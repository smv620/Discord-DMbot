import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-30",
  title: "Choose how your recaps sound",
  what: "On the website, a game master can paste up to three recaps they like and add short notes. DMbot copies how they sound, never what happens in them; delete your examples any time and DMbot goes back to its own.",
  order: 50,
  held: "Waits for the bot step that applies the change to be deployed (#2985), then the server switch DMBOT_WEB_RECAP_STYLE.",
};
