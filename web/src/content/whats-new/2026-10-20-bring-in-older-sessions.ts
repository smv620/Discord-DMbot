import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-20",
  title: "Bring in older sessions",
  what: "On the website, a game master can load text transcripts (or paste a Google Doc link) of sessions DMbot didn't record, such as a Craig session, and have DMbot read them the way it reads a live session, asking you about any new names. You match each name in a file to a member of your server. DMbot keeps a player's lines only if they have already agreed to be recorded; your word that they agreed is not enough. Everyone else's lines are dropped before anything is saved. Imported sessions can be read by everyone in your server, like any transcript. You choose each session's date and order, and see the estimated cost before you start. Paid plans only.",
  held: "Waits for the importer (#2105, #2106) and the Google Doc link support (#2143) to be merged and deployed, and for the page (#2107). When it is live, move this up and turn on IMPORT_LIVE in import-words.ts.",
  order: 9,
};
