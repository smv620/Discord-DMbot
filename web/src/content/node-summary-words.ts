// The words of the summary panel for a picked name on the Story Graph (#3243). Plain words, checked by the
// ux-critic: the page never says how important a name is, its rank or its role, only what the bot wrote.
export const nodeSummaryWords = {
  loading: "Reading up on this name…",
  slow: (n: number): string => `Still reading (${n}s). This is taking longer than usual.`,
  stalled: (n: number): string => `Nothing new yet after ${n}s. You can close this and try again.`,
  dmHint: "Built from what you told DMbot. It only reminds you; you decide.",
  spoilers: "This may contain spoilers.",
  showAnyway: "Show it anyway",
  error: "DMbot can't read up on this name right now. Try again in a minute.",
  tryAgain: "Try again",
  notFound: "That name is not on the graph any more. Close this and pick another.",
};
