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
export const olderHeading = "Older changes";
export const olderIntro = "Everything that came before, newest first.";

/** The main page shows what is from the last RECENT_DAYS days, and always at least the newest RECENT_MIN, whichever is more (#4200:
 * the page lists every entry ever made, so it grew past its size budget; the rest is on /whats-new/older). Supervisor ruled 60 days,
 * but at 2026-11-12 every entry is under 60 days old (the site is 4 weeks old and gains about 10 a day), so 60 days moved nothing and
 * would have grown past the budget again within days; 14 days keeps the page near 20 KB at today's pace. */
export const RECENT_DAYS = 14;
export const RECENT_MIN = 30;

/** Splits entries (newest first) into what the main page shows and what goes to the older page. Pure: `now` is the build day. */
export function splitEntries(all: Entry[], now: Date): { recent: Entry[]; older: Entry[] } {
  const cutoff = new Date(now.getTime() - RECENT_DAYS * 86_400_000).toISOString().slice(0, 10);
  const within = all.filter((e) => e.date >= cutoff).length;
  const keep = Math.max(RECENT_MIN, within);
  return { recent: all.slice(0, keep), older: all.slice(keep) };
}
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

export { formatDate } from "./whats-new-date";
