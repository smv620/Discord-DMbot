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

  // The breadcrumb and the four tabs on every page of a campaign (#2461; docs/PLAN.md, "Where
  // everything is on the website"). Page names are the ones Discord and the PLAN use.
  crumbsLabel: "You are here",
  tabsLabel: "Parts of this campaign",
  myAccount: "My Account",
  thisCampaign: "This campaign",
  groups: {
    story: "Story",
    table: "At the table",
    rules: "Rules",
    settings: "Settings",
  },
  pages: {
    recaps: "Recaps",
    timeline: "Story timeline",
    graph: "Story graph",
    names: "Names and NPCs",
    transcripts: "Transcripts",
    import: "Import a past session",
    houseRules: "House rules",
    homebrew: "Homebrew",
  },
  pageNotes: {
    recaps: "Read, edit and post session recaps.",
    timeline: "Every session in order, with its recap and the names that came out.",
    graph: "How the names in this campaign are connected.",
    names: "Every name DMbot remembers. Search them and fix any that are wrong.",
    transcripts: "Read and download what was said. Everyone in the server can read these.",
    import: "Add a session you played before using DMbot.",
    houseRules: "The rules this campaign plays by.",
    homebrew: "The homebrew this campaign uses.",
  },
  sharedRules: "Shared rules",
  sharedRulesNote: "Share these, or follow another table's.",
  // The Settings group: the current choice of each, read-only for now. Where the website cannot
  // change it yet, it says where to in Discord, with the names of Discord's own menus.
  settingsLead: "These are your campaign's choices. Only game masters see this. To change one, follow the Discord steps under it.",
  settingsLabels: {
    visibility: "Who can see the DM screen",
    level: "How much DMbot says",
    retention: "Transcript retention",
    gameMasters: "Game masters",
    handOver: "Hand over",
    pause: "Pause",
    delete: "Delete",
  },
  visibility: {
    // Discord's own button names (dm_screen/messages.py VISIBILITY_BUTTONS).
    private: "Only the DM",
    peek: "Players can peek",
    open: "Everyone in the server",
  },
  // "chatty" is an old stored choice that Discord no longer offers; it reads as Normal.
  level: { quiet: "Quiet", normal: "Normal", chatty: "Normal" },
  // Discord's own words (transcript/keep.py KEEP_LABELS): a year is "1 year", not 365 days.
  retentionForever: "Forever",
  retentionDays: (days: number): string => (days === 365 ? "1 year" : `${days} days`),
  gameMastersCount: (n: number): string => (n === 1 ? "1 game master" : `${n} game masters`),
  pausedYes: "Paused",
  pausedNo: "Not paused",
  doThisInDiscord: (path: string): string => `Do this in Discord: ${path}`,
  deleteWarning: "This cannot be undone. Discord asks you to confirm first.",
  paths: {
    visibility: "⚙️ Settings → 👁️ Who can see the DM screen",
    level: "⚙️ Settings → the Quiet and Normal buttons",
    retention: "⚙️ Settings → 🗂️ Campaign management → 🗄️ Transcript retention",
    gameMasters: "⚙️ Settings → 🗂️ Campaign management → 👑 Game masters",
    handOver: "⚙️ Settings → 🗂️ Campaign management → 🤝 Hand over",
    pause: "⚙️ Settings → 🗂️ Campaign management → ⏸️ Pause this campaign (or ▶️ Unpause)",
    delete: "⚙️ Settings → 🗂️ Campaign management → 🗑 Delete this campaign",
  },
  listeningNow: "Listening now",
};
