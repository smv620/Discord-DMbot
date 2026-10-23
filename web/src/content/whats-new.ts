// What's new (#1321): what people can do now, newest first, and only what is deployed on the
// server (docs/testing-status.log, "LIVE NOW"; docs/testing-history.log has the deploys). Some
// of it is deployed but not yet tried by a person at a table (the emailed link, typed questions);
// dev1's log says which. Add an entry
// when a feature that people can see goes live, never before: "who carries what" waits for the
// deploy of #1277, and "alive or dead" for the extractor (docs/STORY_MEMORY.md, 5a). The date is
// the day it went live. Plain words, a title of six words at most and one or two sentences, with
// no technical terms (CLAUDE.md, "Simple enough for a child") and no game trademarks.
//
// One file per entry (#2468): add `whats-new/<YYYY-MM-DD>-<short-slug>.ts` exporting `entry`, and
// two pull requests never touch the same lines. A file with a `held` reason is deployed but not shown
// yet; delete the `held` line when the reason is gone. This file only loads and sorts them.
import type { Item } from "./whats-new-types";

export interface Entry {
  /** The day it went live, as YYYY-MM-DD. */
  date: string;
  title: string;
  what: string;
}

export const whatsNewHeading = "What's new";
export const whatsNewIntro =
  "What you can do with DMbot now, newest first. Only things that are live are here.";

/**
 * Entries that are deployed but not shown yet, each with the reason (Supervisor, #1322: "live"
 * means deployed and not known to fail). Move one into `entries` when its reason is gone.
 */
export interface Held extends Entry {
  reason: string;
}

const files = import.meta.glob<{ entry: Item }>("./whats-new/*.ts", { eager: true });

// Newest day first; on one day by `order` (a file without one comes first), then by file name so the
// order never depends on how the files were read.
const sorted = Object.entries(files)
  .map(([path, file]) => ({ path, item: file.entry }))
  .sort(
    (a, b) =>
      b.item.date.localeCompare(a.item.date) ||
      (a.item.order ?? 0) - (b.item.order ?? 0) ||
      a.path.localeCompare(b.path),
  )
  .map(({ item }) => item);

export const entries: Entry[] = sorted
  .filter((i) => i.held === undefined)
  .map(({ date, title, what }) => ({ date, title, what }));

export const held: Held[] = sorted.flatMap(({ date, title, what, held: reason }) =>
  reason === undefined ? [] : [{ date, title, what, reason }],
);

const months = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** "2026-10-15" as "15 October 2026": fixed words, so it reads the same on every computer. */
export function formatDate(date: string): string {
  const [year, month, day] = date.split("-").map(Number);
  const name = months[(month ?? 0) - 1];
  if (name === undefined || !year || !day) throw new Error(`Not a date: ${date}`);
  return `${day} ${name} ${year}`;
}
