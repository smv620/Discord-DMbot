// The words of the card summary (#5051): plain, and the same lines as Discord's card.
export const cardSummaryWords = {
  pending: "DMbot is still writing this.",
  firstMet: (scene: string): string => `First met: ${scene}`,
  lastMentioned: (scene: string): string => `Last mentioned: ${scene}`,
} as const;
