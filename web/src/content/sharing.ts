// The words of the Shared rules page (#2460 part 2, docs/PLAN.md "Where everything is on the
// website"): the house-rules sets and homebrew packs your campaigns share with other tables, and the
// ones they use from other tables. Read-only for now; each line says where to change it in Discord,
// with the same names as the Discord menus (⚙️ Settings → 📚 Shared rules → 📦 Homebrew packs).
export const sharingCard = {
  heading: "Shared rules",
  lead: "The house rules and homebrew your campaigns share with other tables, and the ones they use from other tables.",
  link: "Open Shared rules",
};

export const text = {
  heading: sharingCard.heading,
  lead: "Rules you share with other tables, and rules you use from other tables, for the campaigns you run. Only rules are shared. Never your story, names or notes.",
  loading: "Loading…",
  account: "My Account",
  crumbsLabel: "You are here",
  openHouseRules: "Open house rules",
  openHomebrew: "Open homebrew",
  signedOut: "Sign in to see your shared rules.",
  empty: "You don't share or use any rules yet. To start, in Discord, open ⚙️ Settings, then 📚 Shared rules.",
  failed: "That didn't load. Try again in a moment.",
  tooMany: "Too many tries at once. Wait a moment, then try again.",
  server: (name: string): string => `On ${name}`,
  sharesHeading: "This campaign shares",
  followsHeading: "This campaign uses",
  kinds: { "house-rules": "House rules", homebrew: "Homebrew pack" } as Record<string, string>,
  version: (n: number): string => `Version ${n}`,
  sharing: "Sharing now",
  stopped: "You stopped sharing this",
  codeOn: "Anyone with the share code can use it",
  codeOff: "No share code on",
  from: (publisher: string): string => `from ${publisher}`,
  updating: "Gets new versions",
  noLongerUpdated: "No longer updated",
  // Where to change it. Read-only here for now, so each line names the exact Discord path.
  changeRules: "Change it in Discord: ⚙️ Settings → 📚 Shared rules",
  changePacks: "Change it in Discord: ⚙️ Settings → 📚 Shared rules → 📦 Homebrew packs",
};

export function problem(kind: string): string {
  return kind === "slow-down" ? text.tooMany : text.failed;
}
