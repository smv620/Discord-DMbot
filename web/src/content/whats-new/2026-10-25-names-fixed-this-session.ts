import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-25",
  title: "Names fixed this session, with Undo",
  what: "Under a session's recap, only you (the DM) can open a list of the names DMbot corrected in the cleaned transcript. Each name shows how many times it was fixed and the spellings it replaced, with an Undo that puts them back as they were heard. DMbot only fixes names it is sure of, and you can take any of it back.",
  held: "Waits for CloudDev's routes (#2684), which are not built yet, to be deployed; until then the page is as it was. Then move this up.",
  order: 8,
};
