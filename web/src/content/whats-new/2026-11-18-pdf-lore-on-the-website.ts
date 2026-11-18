import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-11-18",
  title: "Upload a lore PDF on the website",
  what: "On the Upload lore page you can now choose a PDF. DMbot counts its pages and pictures first and tells you what reading it will cost, and reads nothing until you say yes. Then it finds the names and connections, pictures included, for you to check.",
  held: "Held until the bot and website API carrying it are deployed, and flipped with picture reading (DMBOT_PICTURE_READING) in the same deploy (LORE_PDF_LIVE).",
  order: 130,
};
