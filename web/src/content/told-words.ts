// The words of "Tavin now knows the guard is Ken" and Take back in the Story Graph's name panel (#3542, the website half
// of #3089). A secret other name stays secret; the DM can tell ONE character that it is this entry, and take that back.
// Same words as the Discord menu. The DM's own choices only: a player never sees this. Plain words, no technical terms.
export const toldWords = {
  heading: "Tell a character a secret name",
  lead: "The name stays secret from everyone else.",
  /** The choice: "Tavin now knows the guard is Ken". */
  tell: (who: string, secret: string, entry: string): string => `${who} now knows ${secret} is ${entry}`,
  /** The line once told: "Tavin was told that the guard is Ken". */
  told: (who: string, secret: string, entry: string): string => `${who} was told that ${secret} is ${entry}`,
  takeBack: "Take back",
  takeBackFor: (who: string, secret: string): string => `Take back what ${who} was told about ${secret}`,
  /** Asked once, before it is taken back. */
  sure: (who: string, secret: string, entry: string): string => `Take back? ${who} will see ${secret} and ${entry} as two different names again.`,
  yesTakeBack: "Yes, take back",
  keep: "Keep it",
  saidTold: (who: string, secret: string, entry: string): string => `${who} now knows ${secret} is ${entry}.`,
  saidBack: (who: string, secret: string, entry: string): string => `${who} sees ${secret} and ${entry} as two different names again.`,
  working: "Saving…",
  loadFailed: "DMbot couldn't check who knows these secret names. Reload the page to try again.",
  problems: {
    "not-found": "That name changed. Reload the page and look again.",
    "bad-target": "That character doesn't know that name. Reload the page and look again.",
    "not-allowed": "Only the DM can do this.",
    "still-waiting": "DMbot is still saving this. Reload the page in a minute to see it.",
    server: "That didn't save. Try again. If it keeps failing, ask on the Help page.",
  } as Record<string, string>,
} as const;
