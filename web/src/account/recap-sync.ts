// The rules that keep a recap's voice and music in step, ported from the test review page
// (core/src/dmbot/devtools/recap_run/review.py, #2012) so the website and the test page agree.
// Pure functions, so they are tested without a browser.

/** How far apart (seconds) the follower may be before it is moved back to the lead. */
export const DRIFT_SECONDS = 0.15;

/** The follower is moved only when it has drifted further than DRIFT_SECONDS from the lead. */
export function needsFollow(leadTime: number, followerTime: number): boolean {
  return Math.abs(followerTime - leadTime) > DRIFT_SECONDS;
}

/** Where a seek lands: never at the very end, where the lead would stop at once. */
export function seekTarget(wanted: number, leadDuration: number): number {
  const end = Number.isFinite(leadDuration) && leadDuration > 0 ? leadDuration : Infinity;
  return Math.max(0, Math.min(wanted, end - 0.05));
}

/** "1:05" for 65 seconds. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
