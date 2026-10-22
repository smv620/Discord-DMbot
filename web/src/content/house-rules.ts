// The words of the house-rules pages (#1521). They are the table's rules: anyone in the Discord
// server may read them (`/dmbot houserules`), so this is for players as much as DMs, and the DM
// changes them on the DM screen, never here.
import { houseRulesCard } from "./account-cards";

export const text = {
  heading: houseRulesCard.heading,
  link: houseRulesCard.link,
  lead: houseRulesCard.lead,
  leadOneCampaign: "These are your table's rules. Anyone in the Discord server can read them. Only your DM can change them, on the DM screen in Discord.",
  loading: "Loading…",
  empty: "No house rules yet. Your DM adds them on the DM screen in Discord.",
  emptyButSet: "No rules of your own yet. Your table follows the set below.",
  signedOut: "Sign in to read house rules.",
  signIn: "Sign in with Discord",
  account: "Back to My Account",
  allCampaigns: "All campaigns",
  open: "Read",
  openCampaign: (name: string): string => `Read: ${name}`,
  campaignLine: (server: string, rules: number, sets: number): string =>
    [server, `${rules} ${rules === 1 ? "rule" : "rules"}`, ...(sets > 0 ? ["follows another table's set"] : [])].join(
      " · ",
    ),
  numbersStay: "Rule numbers don't change when a rule is removed, so gaps are normal.",
  ruleLine: (n: number, rule: string): string => `${n}. ${rule}`,
  instead: (book: string): string => `Replaces: ${book}`,
  fromSet: (cite: string): string => `Merged in from ${cite}`,
  setRuleLine: (n: number, rule: string): string => `Set rule ${n}. ${rule}`,
  setsHeading: "Also following: rules from another table",
  setsNote: "These come from another table's set. If one clashes with your table's own rules above, your table's rule wins.",
  setLine: (cite: string): string => cite,
  download: "Save as a text file",
  downloadNote: "The file has your table's own rules only, not the sets below.",
  downloading: "Saving…",
  downloadFailed: "That didn't save. Try again.",
  notFound: "We can't find those rules. Go back to All campaigns and pick again.",
  failed: "That didn't load. Try again in a moment.",
  tooMany: "Too many tries at once. Wait a moment, then try again.",
};

export function problem(kind: string): string {
  if (kind === "not-found") return text.notFound;
  if (kind === "slow-down") return text.tooMany;
  return text.failed;
}
