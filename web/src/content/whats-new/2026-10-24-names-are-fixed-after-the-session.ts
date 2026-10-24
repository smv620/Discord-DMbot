import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-24",
  title: "DMbot fixes misspelled names after each session",
  what: "After a session, PlotBot looks again at words the transcript wrote the way they sound and fixes the ones that plainly mean a name on your list, for example a character's name. It only changes the cleaned text, never what was heard. The DM screen shows a short report for each name (what was fixed and how many times, what it wasn't sure of, and any new names) with an Undo for every name. Names it isn't sure of go to your names to check, and the recap waits for your answers.",
  held: "In review (#2628); held until dev1 deploys it.",
};
