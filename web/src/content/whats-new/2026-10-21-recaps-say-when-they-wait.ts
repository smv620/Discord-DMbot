import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-21",
  title: "Recaps say when they wait",
  what: "If DMbot can't finish its last check of a recap right away, the recap page now says it is written and waiting for one last check, instead of saying it is still being written. When the checks weren't sure about something, a closed section called What the checks said lists it for you.",
  held: "In review (#2297); held until dev1 deploys the recap checks (#2280), and it goes live together with \"Recaps are checked before you see them\".",
  order: 8,
};
