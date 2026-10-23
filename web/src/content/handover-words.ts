// The words for the hand-over accept page `/handover` (#2534; core #2532; docs/PLAN.md, "Hand-over by
// email"). A campaign is handed over by an emailed link; the person opens it, signs in, sees which
// campaign and from whom, and accepts or says no. The page never says whether an address has an account
// or a plan before sign-in, and the API decides who may accept (a paid plan with a free campaign slot):
// the page only says what it is told, in plain words. Shown only while HANDOVER_LIVE is true; it moves
// with the What's new entry "Receive a campaign by email".
export const HANDOVER_LIVE = false;

export const handoverWords = {
  heading: "A campaign for you",
  loading: "Opening the offer…",
  notOpen: "This page isn't open yet.",
  noLink: "This page opens from the link in the email. Open that link again.",
  signInLead: "Sign in to see this offer. You can sign in with Discord, or with the email address the offer went to.",
  signInDiscord: "Sign in with Discord",
  signInEmail: "Or sign in by email on My Account",
  // (After signing in the person lands on My Account, which shows a link back here.)
  signInNote: "After you sign in, you'll see a link back to this offer. It works for 7 days.",
  ended: "This offer has ended. Ask the DM to hand the campaign over again.",
  loadFailed: "We couldn't open the offer just now. Nothing was changed. Open the link again in a minute.",
  offer: (campaign: string, from: string): string => `${from} wants to hand you the campaign ${campaign}.`,
  runsOn: (server: string): string => `It runs on ${server} now.`,
  // Why this account can't take it, by the API's code.
  why: {
    try_it: "Try It can't receive a campaign. You need a paid plan with a free campaign slot.",
    no_plan: "You need a paid plan with a free campaign slot to take a campaign.",
    no_slot: "Your plan has no free campaign slot. Free one up or pick a bigger plan, then open this link again.",
    owner: "This is your own campaign, so there is nothing to accept.",
  } as Record<string, string>,
  seePlans: "See the plans",
  // On My Account, after signing in with a kept offer link.
  waitingOnAccount: "A campaign offer is waiting for you.",
  waitingLink: "Open the offer",
  linkKept: "The link still works for 7 days.",
  whereHeading: "Where should it run?",
  whereLead: "Pick the Discord server for this campaign.",
  current: "(where it runs now)",
  addBot: "Add DMbot to a server",
  addBotFor: (name: string): string => `Add DMbot to ${name}`,
  needsBot: "DMbot isn't there yet.",
  noServers: "We can't see a server of yours that DMbot could run on. Add DMbot to a server first.",
  accept: "Accept",
  accepting: "Accepting…",
  decline: "No thanks",
  declining: "One moment…",
  acceptNote: "The campaign moves to your account and uses your hours. The old DM stays a co-DM.",
  accepted: "It's yours. In Discord, type /dmbot start to begin a session.",
  openCampaign: "Open the campaign page",
  declined: "You said no thanks. The campaign stays with the DM who offered it.",
  problem: {
    ended: "This offer has ended. Ask the DM to hand the campaign over again.",
    "no-paid-plan": "You need a paid plan with a free campaign slot to take a campaign.",
    "no-free-slot": "Your plan has no free campaign slot. Free one up or pick a bigger plan, then try again.",
    "owner-offer": "This is your own campaign, so there is nothing to accept.",
    "bad-server": "DMbot can't run it on that server. Pick another, or add DMbot to it first.",
    network: "We couldn't reach DMbot, so nothing was changed. Try again in a moment.",
    server: "That didn't work, and nothing was changed. Try again in a moment.",
  } as Record<string, string>,
};
