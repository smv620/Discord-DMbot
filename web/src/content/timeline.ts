// The words of the Story timeline page (#2462, docs/PLAN.md "Story timeline"): the campaign's
// sessions in order, each with its number, the day it was played and its length, its recap, and the
// names shown to the players at the table that session. Read-only, and for everyone in the server.
export const text = {
  heading: "Story timeline",
  lead: "Your campaign, session by session: what was played, the recap, and the names that came out at the table.",
  loading: "Loading…",
  signedOut: "Sign in to see this campaign's timeline.",
  signIn: "Sign in with Discord",
  failed: "That didn't load. Try again in a moment.",
  notFound: "We can't find that campaign. Open it from My Account.",
  notInPlan: "This campaign's sessions aren't kept on this plan, so there is no timeline.",
  tooMany: "Too many tries at once. Wait a moment, then try again.",
  none: "No sessions yet. They show up here once DMbot has listened to one.",
  session: (n: number): string => `Session ${n}`,
  running: "Still listening",
  recapLabel: "Recap",
  recapPosted: "Posted to the table",
  recapApproved: "Approved, not posted",
  noRecap: "No recap yet.",
  revealedLabel: "Revealed at the table",
  revealedNone: "No new names came out.",
  openRecap: (title: string): string => `Open the recap: ${title}`,
};

export function problem(kind: string): string {
  if (kind === "not-found" || kind === "not-in-server") return text.notFound;
  if (kind === "not-in-plan") return text.notInPlan;
  if (kind === "slow-down") return text.tooMany;
  return text.failed;
}
