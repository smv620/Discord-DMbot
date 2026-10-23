// Which campaigns My Account shows at first, and which wait behind "Show N older campaigns" (#1991).
// Pure, so the order and the "never hide what needs attention" rule are tested without a page.
import type { Campaign } from "./api";

/** How many campaigns show before the button. */
export const SHOWN_AT_FIRST = 5;

/** Newest first by last session; a campaign never played comes after the played ones, by name. */
export function byRecentlyPlayed(campaigns: Campaign[]): Campaign[] {
  // Parsed, not compared as text: "…Z" and "…+00:00" are the same moment. The id is the last tiebreak
  // so the order never depends on the browser.
  const when = (c: Campaign): number => (c.lastPlayedAt ? Date.parse(c.lastPlayedAt) : Number.NEGATIVE_INFINITY);
  return [...campaigns].sort((a, b) => {
    const diff = when(b) - when(a);
    return (Number.isNaN(diff) ? 0 : diff) || a.name.localeCompare(b.name) || a.id.localeCompare(b.id);
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
  // The campaigns you run come before the ones you only play in, so a busy player's table never
  // pushes your own campaign (and its hand-over) behind the button (#2460).
  const run = byRecentlyPlayed(campaigns.filter((c) => c.role !== "player"));
  const played = byRecentlyPlayed(campaigns.filter((c) => c.role === "player"));
  const sorted = [...run, ...played];
  const needsYou = (c: Campaign): boolean => c.status === "paused" || offeredIds.has(c.id);
  const newest = new Set(sorted.slice(0, limit).map((c) => c.id));
  const first = sorted.filter((c) => newest.has(c.id) || needsYou(c));
  const older = sorted.filter((c) => !first.includes(c));
  return { first, older };
}
