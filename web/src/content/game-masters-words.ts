// The words of the game masters section on a campaign's Settings tab (#2768), the same as Discord's 👑 Game masters
// (core dm_screen/game_masters.py) where there is one. Plain words. Who can be added is whoever agreed to be
// recorded, and the page says so where nobody is listed.
export const gameMasterWords = {
  heading: "Game masters",
  lead: (max: number): string =>
    `Game masters see the DM screen and change this campaign's settings. Up to ${max}. (plan) = whose plan runs this campaign.`,
  loading: "Loading the game masters…",
  left: "You are no longer a game master of this campaign, so the list is not yours to see any more.",
  failedToLoad: "We couldn't load the game masters. Reload the page, or try again in a minute.",
  plan: "plan",
  planNote: "Their plan runs this campaign, so they can't be removed. They can hand the campaign over first.",
  planNoteSelf:
    "Your plan runs this campaign, so you can't be removed. Hand the campaign over first, in Discord: ⚙️ Manage DMbot → 🗂️ Campaign management → 🤝 Hand over.",
  you: "you",
  remove: "Remove",
  removeLabel: (name: string): string => `Remove ${name}`,
  cancel: "Cancel",
  confirmYes: "Yes, remove",
  confirm: (name: string): string => `${name} will no longer see the DM screen or change this campaign. Remove?`,
  confirmSelf: "You will no longer see the DM screen or change this campaign. Remove yourself?",
  addHeading: "Add a game master",
  searchLabel: "Find a member by name",
  add: "Add",
  addLabel: (name: string): string => `Add ${name}`,
  searching: "Looking…",
  loadingMembers: "Loading members…",
  full: (max: number): string => `A campaign can have at most ${max} game masters. Remove one before adding another.`,
  nobody:
    "No one to add yet. Only members who agreed to be recorded show up here.",
  /** Always shown under the search: whoever is not listed is added in Discord. */
  inDiscord:
    "Not listed? Add anyone in your server in Discord: type /dmbot settings, then 🗂️ Campaign management → 👑 Game masters.",
  noMatch: "No one with that name. Only members who agreed to be recorded can be found here. Try fewer letters.",
  searchFailed: "We couldn't look that up. Try again in a moment.",
  // The wait for the bot's answer (#2564): DMbot's stage, how long, when it last moved.
  stage: {
    waiting: "Waiting for DMbot to pick it up.",
    working: "DMbot is saving it.",
  } as Record<string, string>,
  quiet: "DMbot is slow to answer. Your change is queued. You can leave this page and check the list later.",
  gaveUp: "DMbot hasn't answered in an hour. The change may still go through. Reload the list in a while before trying again.",
  saved: (name: string, added: boolean): string => `${name} is ${added ? "now" : "no longer"} a game master.`,
  problems: {
    "not-allowed": "Only a game master of this campaign can change this list.",
    "not-found": "That campaign isn't here any more.",
    "bad-value": "DMbot can't find that person. Search again.",
    "bad-setting": "DMbot can't find that person. Search again.",
    "plan-holder": "This game master holds the campaign's plan, so they can't be removed. They can hand the campaign over first.",
    "too-many-masters": "This campaign already has the most game masters it can have. Remove one before adding another.",
    "unknown-person": "DMbot can't find that person. Search again.",
    "too-soon": "Too many changes at once. Wait a moment and try again.",
    server: "That didn't work, and nothing was saved. Try again in a moment.",
  } as Record<string, string>,
};
