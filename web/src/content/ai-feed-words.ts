// The live, read-only windows on what DMbot asked the AI, on the lore upload and import pages (#3710). The owner
// ruled that these show the real prompts and replies, each cut to its first 500 characters then "[...]" (the cut is made
// before anything is stored), in two code-style windows. Plain text only; only a campaign's DMs and game masters see it.
export const feedWords = {
  heading: "What DMbot is asking the AI",
  lead: "This is what DMbot sent to the AI and what it said back, as it happens. Only you can see it. Long messages are cut after 500 characters and end with [...]. Question 3 and answer 3 go together.",
  promptsHeading: "What DMbot asked",
  repliesHeading: "What came back",
  nonePrompts: "DMbot hasn't asked the AI anything yet.",
  noneReplies: "No answers yet.",
  waitingReply: "Waiting for an answer…",
  noReply: "No answer came back before it ended.",
  failedReply: "No answer came back. The progress above says what to do.",
  lost: (n: number): string => `${n} ${n === 1 ? "message isn't" : "messages aren't"} shown. There were too many to keep.`,
  jump: "Jump to newest",
  stalled: "Can't reach DMbot right now, so this may be out of date. Trying again…",
  question: "Question",
  answer: "Answer",
  windowLabel: (which: string): string => `${which}, newest at the bottom`,
} as const;
