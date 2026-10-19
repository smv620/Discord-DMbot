// Which campaigns My Account shows at first, and which wait behind "Show N older campaigns" (#1991).
// Pure, so the order and the "never hide what needs attention" rule are tested without a page.
import type { Campaign } from "./api";

/** How many campaigns show before the button. */
export const SHOWN_AT_FIRST = 5;

/** Newest first by last session; a campaign never played comes after the played ones, by name. */
export function byRecentlyPlayed(campaigns: Campaign[]): Campaign[] {
  return [...campaigns].sort((a, b) => {
    if (a.lastPlayedAt && b.lastPlayedAt) {
      return b.lastPlayedAt.localeCompare(a.lastPlayedAt) || a.name.localeCompare(b.name);
    }
    if (a.lastPlayedAt) return -1;
    if (b.lastPlayedAt) return 1;
    return a.name.localeCompare(b.name);
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
  const sorted = byRecentlyPlayed(campaigns);
  const needsYou = (c: Campaign): boolean => c.status === "paused" || offeredIds.has(c.id);
  const newest = new Set(sorted.slice(0, limit).map((c) => c.id));
  const first = sorted.filter((c) => newest.has(c.id) || needsYou(c));
  const older = sorted.filter((c) => !first.includes(c));
  return { first, older };
}
