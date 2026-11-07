// The words of "Words DMbot stopped asking about" on a campaign's Settings page (#3577, the website half of #3033). After a
// session DMbot may ask the DM whether words the table keeps using for a connection should become a kind of connection; a
// Never or Not now is kept, and this list lets the DM lift it. The words are the table's own, shown in quotes and to the DM
// only. The sentences are the issue's. Plain words, no technical terms.
export const kindOffersWords = {
  heading: "Words DMbot stopped asking about",
  lead: "DMbot offered to make words your table keeps using a kind of connection. You said never, or not now, so it stopped asking.",
  empty: "DMbot hasn't been told to stop asking about any words.",
  never: "Never",
  later: "Asked again after 3 more",
  /** The words in curly quotes, as everywhere the table's own words are shown. */
  quoted: (words: string): string => `“${words}”`,
  lift: "Ask about these again",
  liftFor: (words: string): string => `Ask about “${words}” again`,
  working: "Saving…",
  lifted: (words: string): string => `DMbot may ask about “${words}” again after your next session.`,
  loadFailed: "DMbot couldn't check which words it stopped asking about. Reload the page to try again.",
  problems: {
    "not-found": "Those words are not on the list any more. Reload the page.",
    "still-waiting": "DMbot is still saving this. Reload the page in a minute to see it.",
    "not-allowed": "Only this campaign's DM can do this.",
    server: "That didn't save. Try again. If it keeps failing, ask on the Help page.",
  } as Record<string, string>,
} as const;
