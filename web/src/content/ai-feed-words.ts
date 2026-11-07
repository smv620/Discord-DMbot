// The live, read-only windows on what DMbot asked the AI, on the lore upload and import pages (#3710). The owner
// ruled that these show the real prompts and replies, each cut to its first 500 characters then "[...]" (the cut is made
// before anything is stored). Plain text only; only a campaign's DMs and game masters ever see it.
export const feedWords = {
  heading: "What DMbot asked the AI",
  lead: "A live look, for you only. Each is the first 500 characters; “[...]” means there was more. Nothing here can be changed.",
  promptsHeading: "What DMbot asked",
  repliesHeading: "What came back",
  none: "Nothing asked yet.",
  waitingReply: "Waiting for an answer…",
  failedReply: (why: string): string => `No answer: ${why}`,
  failedNoReason: "No answer. DMbot will tell you what to do.",
  lost: (n: number): string => `${n} ${n === 1 ? "call isn't" : "calls aren't"} shown.`,
  jump: "Jump to the newest",
  stopped: "This look stopped. Reload the page to see more.",
  entry: (n: number, time: string, service: string, model: string, step: string): string =>
    `#${n} ${time} ${[service, model].filter(Boolean).join(", ")}${step ? `, ${step}` : ""}`,
  windowLabel: (which: string): string => `${which}, newest at the bottom`,
} as const;
