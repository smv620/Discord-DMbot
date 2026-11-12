// The words of the "People you play with" page (#4226). Plain words: a name and a date, never a campaign, a character or a story,
// because DMbot keeps none of those in this list.
const date = (seconds: number): string =>
  new Date(seconds * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });

export const rememberedWords = {
  heading: "People you play with",
  notOpen: "This page is not open yet.",
  loading: "Loading…",
  failed: "Couldn't load this list. Try again, or come back in a few minutes.",
  tryAgain: "Try again",
  actionFailed: "Couldn't forget that player. Try again.",
  actionFailedBy: "Couldn't remove you. Try again.",
  signedOut: "Sign in again to see this page.",
  mine: {
    heading: "Players DMbot remembers for you",
    intro:
      "When you add a player to a character, DMbot remembers you played together. That makes it quick to add them to your next campaign. DMbot keeps only their Discord name. Only you can see this list.",
    none: "No one yet. Players show up here once you add them to a character.",
    row: (campaigns: number, since: number): string => `${campaigns === 1 ? "1 campaign" : `${campaigns} campaigns`} · since ${date(since)}`,
    unknownName: "A player (name not known yet)",
    forget: (name: string): string => `Forget this player: ${name}`,
    forgetButton: "Forget this player",
    forgotten: (name: string): string => `${name} is forgotten.`,
    already: "That player was already forgotten.",
    note: "Forgetting only removes them from this list. Their characters and past sessions stay. You can add them again later.",
  },
  by: {
    heading: "DMs who remember you",
    intro:
      "A DM you play with keeps your Discord name on a list, so they can add you to their next campaign. You can remove yourself from any DM's list at any time.",
    none: "No DM has you on a list.",
    row: (campaigns: number, since: number): string => `${campaigns === 1 ? "1 campaign" : `${campaigns} campaigns`} · since ${date(since)}`,
    unknownName: "A DM",
    clear: (name: string): string => `Remove me from ${name}'s list`,
    clearButton: "Remove me",
    clearAll: "Remove me from all",
    cleared: (name: string): string => `You are off ${name}'s list.`,
    clearedAll: "You are off every DM's list.",
    note: "This does not stop recording. To stop being recorded, use Stop recording me in Discord.",
  },
} as const;
