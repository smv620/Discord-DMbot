// The words for "Your Discord accounts" on My Account (#2534; docs/PLAN.md, "one DMbot account with
// several Discord accounts"). A DMbot account can have more than one Discord account: the plan and the
// campaigns belong to the account, and any of its Discord accounts can use them. Adding one is only ever
// by signing in with it (never by typing a number), and the list shows names, never raw numbers.
// Shown only while DISCORD_ACCOUNTS_LIVE is true; it moves with the What's new entry "Use more than one
// Discord account" and needs the account-key change (#2533) deployed.
export const DISCORD_ACCOUNTS_LIVE = false;

export const discordAccountsWords = {
  heading: "Your Discord accounts",
  lead: "Your plan and campaigns work from any of these Discord accounts. Each Discord account decides for itself whether it is recorded.",
  you: "(the one you're using now)",
  add: "Add a Discord account",
  addNote:
    "Discord signs in as whichever account it is already logged in with. To add a different one, switch accounts in Discord first. You never type a number.",
  remove: "Remove",
  removeFor: (name: string): string => `Remove ${name}`,
  confirmFor: (name: string): string =>
    `Remove ${name} from your DMbot account? Your plan and campaigns stay with your account, and ${name} can no longer use them. Its choice about being recorded stays with ${name}.`,
  confirmYes: "Yes, remove it",
  keep: "Keep it",
  lastOne: "You have one Discord account, so there is nothing to remove.",
  cantRemoveNow: "The one you're using now can't be removed. Sign in with another one first.",
  removing: "Removing…",
  removed: "Removed.",
  // What coming back from Discord says (?discord=added and the like).
  result: {
    added: "Added. That Discord account can now use your plan and campaigns too.",
    taken: "Not added. That Discord account already belongs to another DMbot account.",
    has_campaigns:
      "Not added. That Discord account runs campaigns or has its own plan. In Discord, use that account to hand its campaigns over, then try again.",
    already: "That Discord account is already on your account.",
    cancelled: "Not added. You stopped before finishing on Discord.",
    failed: "Not added. Something went wrong. Press Add a Discord account to try again.",
  } as Record<string, string>,
  problem: {
    "not-found": "That Discord account is already gone. The list is up to date now.",
    "last-account": "This is your only Discord account, so it can't be removed.",
    "slow-down": "Too many tries just now. Wait a minute and try again.",
    default: "That didn't work, and nothing was changed. Try again in a moment.",
    network: "We couldn't reach DMbot, so nothing was changed. Try again in a moment.",
    server: "That didn't work, and nothing was changed. Try again in a moment.",
  } as Record<string, string>,
};
