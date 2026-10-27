// The words of the optional rules section on a campaign's Settings tab (#2753), the same as Discord's
// `/dmbot optionalrules` where there is one (core ui/optional_rules.py). Plain words; the rules' own names and
// summaries come from the API's catalog.
export const optionalWords = {
  heading: "Optional rules",
  lead: (main: string): string =>
    `Rules from Xanathar's and Tasha's that add to your main rules (${main}). Switch one on or off. Your house rules always win.`,
  disclaimer: "DMbot doesn't check rules yet; when it does, it only reminds you of the rules you keep on. You make every call.",
  loading: "Loading the optional rules…",
  none: (main: string): string => `DMbot has no optional rules to offer for the ${main} yet.`,
  failedToLoad: "We couldn't load the optional rules. Reload the page, or try again in a minute.",
  on: "On",
  off: "Off",
  followsDefault: "Follows the campaign's setting",
  chosen: "You chose this",
  turnOn: (name: string): string => `Turn on ${name}`,
  turnOff: (name: string): string => `Turn off ${name}`,
  saving: "Saving…",
  slow: "This is taking a little longer than usual.",
  offline: "DMbot hasn't answered yet. Your choice is waiting and will be saved when it does.",
  saved: (name: string, on: boolean): string => `${name} is now ${on ? "on" : "off"}.`,
  timedOut: "DMbot didn't answer in time, so nothing was saved. Try again.",
  problems: {
    "not-allowed": "Only a game master of this campaign can change its rules.",
    "not-found": "That campaign isn't here any more.",
    "bad-value": "That rule isn't in this campaign's list any more. Reload the page to see the list again.",
    "bad-setting": "That rule isn't in this campaign's list any more. Reload the page to see the list again.",
    "too-soon": "Too many changes at once. Wait a moment and try again.",
    server: "That didn't work, and nothing was saved. Try again in a moment.",
  } as Record<string, string>,
};
