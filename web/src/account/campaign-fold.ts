// Which campaigns My Account shows at first, and which wait behind "Show N older campaigns" (#1991).
// Pure, so the order and the "never hide what needs attention" rule are tested without a page.
import type { Campaign } from "./api";

/** How many campaigns show before the button. */
export const SHOWN_AT_FIRST = 5;

/** Newest first by when the person last used the campaign (#5037): the latest of their own last visit, its last
 * session and its creation, as the API says in `lastAccessedAt`. An older API without it falls back to the last
 * session, then the creation. Never by name. */
export function byRecentlyPlayed(campaigns: Campaign[]): Campaign[] {
  // Parsed, not compared as text: "…Z" and "…+00:00" are the same moment. The id breaks a tie, so the order never
  // depends on the browser.
  const at = (text: string | null | undefined): number => (text ? Date.parse(text) : Number.NaN);
  const when = (c: Campaign): number => {
    const found = [at(c.lastAccessedAt)].filter((n) => !Number.isNaN(n));
    if (found.length > 0) return found[0]!;
    const older = [at(c.lastPlayedAt), at(c.startedAt)].filter((n) => !Number.isNaN(n));
    return older.length > 0 ? Math.max(...older) : Number.NEGATIVE_INFINITY;
  };
  return [...campaigns].sort((a, b) => {
    const diff = when(b) - when(a);
    return (Number.isNaN(diff) ? 0 : diff) || a.id.localeCompare(b.id);
  });
}

/** The campaigns shown at first, and the rest. A paused campaign, or one with an unanswered hand-over
 * offer, always shows at first even when it is old: the person has something to do there, and a
 * button is easy to miss. They keep their place in the order, after the newest five. */
export function foldCampaigns(
  campaigns: Campaign[],
  offeredIds: ReadonlySet<string>,
  limit: number = SHOWN_AT_FIRST,
): { first: Campaign[]; older: Campaign[] } {
  // One order, newest use first, whether you run the campaign or only play in it (#5037; the run-before-play split
  // of #2460 went at the owner's request). A campaign that needs you still shows at first, below.
  const sorted = byRecentlyPlayed(campaigns);
  const needsYou = (c: Campaign): boolean => c.status === "paused" || offeredIds.has(c.id);
  const newest = new Set(sorted.slice(0, limit).map((c) => c.id));
  const first = sorted.filter((c) => newest.has(c.id) || needsYou(c));
  const older = sorted.filter((c) => !first.includes(c));
  return { first, older };
}
