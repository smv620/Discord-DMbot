// "Private scenes" on the Names page (#4102), in plain words; the same as Discord's list (core ui/private_scenes_list.py). DMbot keeps
// no words of what was said, so none are shown: names, the day, about how long and how many lines.
const w_began = (into: number): string => (into >= 60 ? `${Math.floor(into / 60)}h ${String(into % 60).padStart(2, "0")}m in` : `${into} min in`);

export const sceneWords = {
  heading: "🤫 Private scenes",
  lead: "Times two player characters were alone together while the party was split. DMbot found them from the game clock and who spoke, and keeps no words, and treats each one as mattering to the story. Only DMs see this list. If DMbot got one wrong (someone else was there, or the party wasn't really apart), pick it and press Not private.",
  empty: "No private scenes yet. After each session, DMbot adds here any time two player characters were alone together.",
  loading: "Loading the private scenes…",
  failed: "We couldn't load this list. Reload the page to try again.",
  who: (a: string, b: string): string => `${a} and ${b}`,
  someone: "someone",
  detail: (date: string, into: number, minutes: number, lines: number): string => `${date} · ${w_began(into)} · about ${minutes} min, ${lines === 1 ? "1 line" : `${lines} lines`} spoken`,
  notPrivateOf: (a: string, b: string): string => `Not private: ${a} and ${b}`,
  notPrivate: "Not private",
  saving: "Saving your answer",
  done: (a: string, b: string): string => `${a} and ${b}: not private. DMbot won't count it again.`,
  gone: "That one was already gone.",
  notAllowed: "Only a DM of the campaign can do this.",
  signedOut: "You were signed out. Sign in again, then try again.",
  couldNot: "DMbot couldn't save that. Nothing changed. Try again in a minute.",
} as const;
