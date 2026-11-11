// "Private scenes" on the Names page (#4102), in plain words; the same as Discord's list (core ui/private_scenes_list.py). DMbot keeps
// no words of what was said, so none are shown: names, the day, about how long and how many lines.
export const sceneWords = {
  heading: "🤫 Private scenes",
  lead: "Times two player characters were alone together while the party was split. DMbot found them from the game clock and who spoke, and keeps no words. Only you see this list. If one wasn't really private, pick it and press Not private.",
  empty: "No private scenes yet. After each session, DMbot adds here any time two player characters were alone together.",
  loading: "Loading the private scenes…",
  failed: "We couldn't load this list. Reload the page to try again.",
  who: (a: string, b: string): string => `${a} and ${b}`,
  detail: (date: string, minutes: number, lines: number): string => `${date} · about ${minutes === 1 ? "1 minute" : `${minutes} minutes`} · ${lines === 1 ? "1 line" : `${lines} lines`}`,
  notPrivate: "Not private",
  saving: "Saving your answer",
  done: (a: string, b: string): string => `${a} and ${b}: not private. DMbot won't count it again.`,
  gone: "That one was already gone.",
  notAllowed: "Only a DM of the campaign can do this.",
  signedOut: "You were signed out. Sign in again, then try again.",
  couldNot: "DMbot couldn't save that. Nothing changed. Try again in a minute.",
} as const;
