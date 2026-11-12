// The words of the "People you play with" page (#4226). Plain words: a name and a date, never a campaign, a character or a story,
// because DMbot keeps none of those in this list.
const date = (seconds: number): string =>
  new Date(seconds * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });

export const rememberedWords = {
  heading: "People you play with",
  notOpen: "This page is not open yet.",
  loading: "Loading…",
  failed: "Couldn't load this list.",
  tryAgain: "Try again",
  actionFailed: "Couldn't do that. Try again.",
  signedOut: "Sign in again to see this page.",
  mine: {
    heading: "Players DMbot remembers for you",
    intro:
      "When you add a player to a character in any of your campaigns, DMbot remembers that you played together, so you can add them to your next campaign. Only you can see this list, and only by their Discord name.",
    none: "No one yet. Players show up here once you add them to a character.",
    row: (campaigns: number, since: number): string => `${campaigns === 1 ? "1 campaign" : `${campaigns} campaigns`} · since ${date(since)}`,
    unknownName: "A player (name not known yet)",
    forget: (name: string): string => `Forget ${name}`,
    forgetButton: "Forget this player",
    forgotten: (name: string): string => `${name} is forgotten.`,
    already: "That player was already forgotten.",
    note: "Forgetting removes them from this list only. Their characters and transcripts stay.",
  },
  by: {
    heading: "DMs who remember you",
    intro:
      "A DM you play with remembers that you played together, by your Discord name, so they can add you to their next campaign. You can clear it any time.",
    none: "No DM remembers you.",
    row: (campaigns: number, since: number): string => `${campaigns === 1 ? "1 campaign" : `${campaigns} campaigns`} · since ${date(since)}`,
    unknownName: "A DM",
    clear: (name: string): string => `Clear ${name}'s memory of me`,
    clearButton: "Clear",
    clearAll: "Clear all",
    cleared: (name: string): string => `${name} no longer remembers you.`,
    clearedAll: "No DM remembers you now.",
    note: "Clearing removes you from their list. It does not stop you being recorded: for that, use Stop recording me in Discord.",
  },
} as const;
