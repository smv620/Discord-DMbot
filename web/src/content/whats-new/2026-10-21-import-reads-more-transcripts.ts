import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-21",
  title: "Import reads more transcripts",
  what: "When you bring in older sessions, DMbot now also reads a Google Doc transcript that writes each name in [square brackets], and a caption file (a .vtt or .srt) that says who is speaking. If it still can't read a file, it now says why: the format is one it doesn't know, or the captions don't say who is speaking.",
  held: "In review (#2302); held until dev1 deploys it.",
  order: 9,
};
