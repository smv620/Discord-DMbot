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
  noLink: "Open the link from the email we sent. If you can't find it, ask the DM to send it again.",
  signInLead: "Sign in to see this offer. Use Discord, or the email address the offer was sent to.",
  signInDiscord: "Sign in with Discord",
  signInEmail: "Sign in with your email instead",
  // (After signing in the person lands on My Account, which shows a link back here.)
  signInNote: "After you sign in, open the offer from My Account. It stays there until it ends.",
  ended: "This offer has ended. Ask the DM to hand the campaign over again.",
  loadFailed: "We couldn't open the offer just now. Nothing was changed.",
  tryAgain: "Try again",
  offer: (campaign: string, from: string): string => `${from} wants to hand you the campaign ${campaign}.`,
  runsOn: (server: string): string => `It runs on the server ${server} today. You can pick a different server below.`,
  // Why this account can't take it, by the API's code.
  why: {
    try_it: "Try It can't receive a campaign. You need a paid plan with a free campaign slot.",
    no_plan: "You need a paid plan with a free campaign slot to take a campaign.",
    no_slot: "Your plan has no free campaign slot. Hand over or remove one of your campaigns, or pick a bigger plan. Then open this offer again.",
    owner: "This is your own campaign, so there is nothing to accept.",
  } as Record<string, string>,
  cantNow: "You can't take this campaign right now. Nothing was changed.",
  seePlans: "See the plans",
  // On My Account, after signing in with a kept offer link.
  waitingOnAccount: "A campaign offer may be waiting for you.",
  waitingLink: "Open the offer",
  linkKept: "The link still works for 7 days.",
  whereHeading: "Where should it run?",
  whereLead: "Pick the Discord server for this campaign.",
  current: "(where it runs now)",
  addBotFor: (name: string): string => `Add DMbot to ${name}`,
  needsBot: "(DMbot isn't there yet)",
  comeBack: "After adding DMbot, come back to this page.",
  noServers: "DMbot isn't on any of your servers yet. Add it to one, then come back to this page.",
  pickFirst: "Pick a server first.",
  accept: "Take the campaign",
  accepting: "Taking it…",
  decline: "No thanks",
  declining: "One moment…",
  acceptNote:
    "If you accept, this campaign moves to your account. Its play time comes out of your plan's hours. The DM who offered it stays on as a co-DM and can still help run it.",
  accepted: "It's yours. In Discord, type /dmbot start to begin a session.",
  openCampaign: "Open the campaign page",
  declined: "You said no thanks. The campaign stays with the DM who offered it. The DM can offer it again.",
  problem: {
    ended: "This offer has ended. Ask the DM to hand the campaign over again.",
    "no-paid-plan": "You need a paid plan with a free campaign slot to take a campaign.",
    "no-free-slot": "Your plan has no free campaign slot. Hand over or remove one of your campaigns, or pick a bigger plan. Then try again.",
    "owner-offer": "This is your own campaign, so there is nothing to accept.",
    "bad-server": "DMbot can't run it on that server. Pick another, or add DMbot to it first.",
    network: "We couldn't reach DMbot, so nothing was changed. Try again in a moment.",
    server: "That didn't work, and nothing was changed. Try again in a moment.",
  } as Record<string, string>,
};
