// The words of the website's search bar (#3855). Plain words; the page you are sent to does the work, DMbot only points.
export const searchWords = {
  open: "Search the website",
  close: "Close search",
  label: "What are you looking for?",
  hint: "Type a few words, like “how much does it cost”.",
  best: "Best match:",
  none: "No page matches that. Try other words.",
  // Line 2 (#3855): the short answer. The failure line is Supervisor's, word for word.
  ask: "Ask",
  thinking: "[... thinking ...]",
  stillThinking: "Still thinking…",
  seconds: (n: number): string => `${n}s`,
  cant: "DMbot can't answer right now. The page above is the best match.",
  // Over a limit: 429 with the hour or the day. Plain words, and what to do next.
  limit: {
    hour: "That's all the questions for this hour. Try again in a bit, or use the page above.",
    day: "That's all the questions for today. Try again tomorrow.",
  },
} as const;
