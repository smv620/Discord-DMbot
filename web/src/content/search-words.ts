// The words of the website's search bar (#3855). Plain words; the page you are sent to does the work, DMbot only points.
export const searchWords = {
  open: "Search the website",
  close: "Close search",
  label: "What are you looking for?",
  hint: "Type a few words, like “how much does it cost”.",
  best: "Best match:",
  none: "No page matches that. Try other words.",
  // Line 2 (#3855): the short answer. These words are the owner's and docs/PLAN.md's ("Search the website"), exactly.
  ask: "Ask",
  thinking: "[... thinking ...]",
  thinkingFor: (n: number): string => `[... thinking ... ${n} s]`,
  cant: "DMbot can't answer right now. Try again in a little while.",
  limit: {
    hour: "Too many requests to process, try again in about an hour.",
    day: "Too many requests to process, try again tomorrow.",
  },
} as const;
