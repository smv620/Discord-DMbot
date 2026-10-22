// The words of a campaign's own page (#2416; docs/PLAN.md, "A page for each campaign"): one place
// for everything the website holds about one campaign, linked from My Campaigns. Plain words. The
// DM's tools show only to the campaign's DMs, as the API says; a player sees the pages that are
// for the whole table.
export const campaignPage = {
  loading: "Loading…",
  signedOut: "You were signed out. Sign in again.",
  notFound: "We couldn't find that campaign. Open it from My Campaigns.",
  down: "We couldn't reach DMbot. Check your connection and try again.",
  tryAgain: "Try again",
  backToCampaigns: "← My Campaigns",
  /** On each of the campaign's own pages: back to the campaign's page. */
  backTo: (name: string): string => `← ${name}`,
  backToThis: "← This campaign",
  lead: (server: string): string => `Your campaign on the ${server} server.`,
  dmHeading: "DM tools",
  tableHeading: "Everyone at the table",
  recaps: "📜 Recaps",
  recapsNote: "Read, edit and post session recaps.",
  houseRules: "🏠 House rules",
  houseRulesNote: "The rules this campaign plays by.",
  homebrew: "🧪 Homebrew",
  homebrewNote: "The homebrew this campaign uses.",
  story: "🕸️ Story map",
  storyNote: "How the names in this campaign are connected.",
  names: "📇 Names",
  namesNote: "Every name DMbot remembers here, to search and fix.",
  transcripts: "🗂️ Transcripts",
  transcriptsNote: "Read and download what was said. Everyone in the server can read these.",
  importSessions: "📥 Import a past session",
  importNote: "Add a session you played before using DMbot.",
  // The recap list, showing one campaign's recaps (and a way to all of them).
  onlyThisCampaign: "Showing only this campaign's recaps.",
  showAllRecaps: "Show all my recaps",
  noneHere: "This campaign has no recaps yet.",
};
