// The words of the game masters section on a campaign's Settings tab (#2768), the same as Discord's 👑 Game masters
// (core dm_screen/game_masters.py) where there is one. Plain words. Who can be added is whoever agreed to be
// recorded, and the page says so where nobody is listed.
export const gameMasterWords = {
  heading: "Game masters",
  lead: (max: number): string => `Game masters see the DM screen and change this campaign's settings. Up to ${max}.`,
  loading: "Loading the game masters…",
  failedToLoad: "We couldn't load the game masters. Reload the page, or try again in a minute.",
  plan: "plan",
  planNote: "Their plan runs this campaign, so they can't be removed. They can hand the campaign over first.",
  you: "you",
  remove: "Remove",
  removeLabel: (name: string): string => `Remove ${name}`,
  cancel: "Cancel",
  confirm: (name: string): string => `${name} will no longer see the DM screen or change this campaign. Remove?`,
  confirmSelf: "You will no longer see the DM screen or change this campaign. Remove yourself?",
  addHeading: "Add a game master",
  searchLabel: "Find a member by name",
  add: "Add",
  addLabel: (name: string): string => `Add ${name}`,
  searching: "Looking…",
  full: (max: number): string => `A campaign can have at most ${max} game masters. Remove one before adding another.`,
  nobody:
    "Nobody to offer. DMbot lists the members who agreed to be recorded. To add someone else, use ⚙️ Settings → 🗂️ Campaign management → 👑 Game masters in Discord.",
  noMatch: "Nobody with that name. Try fewer letters.",
  searchFailed: "We couldn't look that up. Try again in a moment.",
  // The wait for the bot's answer (#2564): DMbot's stage, how long, when it last moved.
  stage: {
    waiting: "Waiting for DMbot to pick it up.",
    working: "DMbot is saving it.",
  } as Record<string, string>,
  quiet: "DMbot hasn't answered yet. Your change is waiting and will be saved when it does. You can leave this page and come back later to see where it stands.",
  gaveUp: "DMbot hasn't answered in an hour. Your change may still be saved: reload the page later to see the list, or try again.",
  saved: (name: string, added: boolean): string => `${name} is ${added ? "now" : "no longer"} a game master.`,
  problems: {
    "not-allowed": "Only a game master of this campaign can change this list.",
    "not-found": "That campaign isn't here any more.",
    "bad-value": "DMbot doesn't know that person any more. Search again.",
    "bad-setting": "DMbot doesn't know that person any more. Search again.",
    "plan-holder": "This game master holds the campaign's plan, so they can't be removed. They can hand the campaign over first.",
    "too-many-masters": "A campaign can have at most 5 game masters. Remove one before adding another.",
    "unknown-person": "DMbot can't add that person. Search for them again.",
    "too-soon": "Too many changes at once. Wait a moment and try again.",
    server: "That didn't work, and nothing was saved. Try again in a moment.",
  } as Record<string, string>,
};
