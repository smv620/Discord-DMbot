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
  // The wait for the bot's answer (#2564): DMbot's stage, how long, when it last moved.
  stage: {
    waiting: "Waiting for DMbot to pick it up.",
    working: "DMbot is saving it.",
  } as Record<string, string>,
  quiet: "DMbot hasn't answered yet. Your choice is waiting and will be saved when it does. You can leave this page and come back later to see where it stands.",
  gaveUp: "DMbot hasn't answered in an hour. Your choice may still be saved: reload the page later to see where it stands, or press the switch to try again.",
  saved: (name: string, on: boolean): string => `${name} is now ${on ? "on" : "off"}.`,
  problems: {
    "not-allowed": "Only a game master of this campaign can change its rules.",
    "not-found": "That campaign isn't here any more.",
    "bad-value": "That rule isn't in this campaign's list any more. Reload the page to see the list again.",
    "bad-setting": "That rule isn't in this campaign's list any more. Reload the page to see the list again.",
    "too-soon": "Too many changes at once. Wait a moment and try again.",
    server: "That didn't work, and nothing was saved. Try again in a moment.",
  } as Record<string, string>,
};
