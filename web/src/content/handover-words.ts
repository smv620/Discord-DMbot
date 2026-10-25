// The words for the hand-over accept page `/handover` (#2534; core #2532; docs/PLAN.md, "Hand-over by
// email"). A campaign is handed over by an emailed link; the person opens it, signs in, sees which
// campaign and from whom, and accepts or says no. The page never says whether an address has an account
// or a plan before sign-in, and the API decides who may accept (a paid plan with a free campaign slot):
// the page only says what it is told, in plain words. Shown only while HANDOVER_LIVE is true; it moves
// with the What's new entry "Receive a campaign by email".
export const HANDOVER_LIVE = false;

/** The owner's side: handing a campaign over by email from My Account (#2534; PLAN, "Hand-over by email").
 * The owner sees only "We've emailed the offer", never whether the address has an account or a plan. */
export const handoverOwnerWords = {
  button: "Hand over by email",
  buttonFor: (name: string): string => `Hand over by email: ${name}`,
  question: (name: string): string => `Who should take ${name}?`,
  label: "Their email address",
  badEmailId: "Check the address",
  note: "We email them a link that works for 7 days. The campaign stays yours until they accept. After that you stay on as a co-DM.",
  send: "Send the offer",
  sending: "Sending…",
  cancel: "Cancel",
  emailed: "We've emailed the offer.",
  waiting: (sent: string, ends: string): string =>
    `We emailed the offer on ${sent}. It ends on ${ends} if they don't answer, and we'll tell you. The campaign stays yours until they accept.`,
  elsewhere: "To send it to someone else, use the button below. That takes back this offer.",
  sendElsewhere: "Send to someone else",
  replaces: "This takes back the open offer and sends a new one.",
  takeBack: "Take back the offer",
  takeBackFor: (name: string): string => `Take back the offer: ${name}`,
  takingBack: "Taking it back…",
  takenBack: "Done. The link no longer works. The campaign is still yours.",
  problem: {
    "bad-email": "That doesn't look like an email address. It should look like name@example.com.",
    "too-soon": "You've sent 5 offers today. You can send more tomorrow.",
    "not-found": "That campaign is no longer yours to hand over. The list is up to date now.",
    "not-allowed": "This campaign can't be handed over by email. Try It campaigns can't be handed over.",
    network: "We couldn't reach DMbot, so nothing was changed. Try again in a moment.",
    server: "That didn't work, and nothing was changed. Try again in a moment.",
  } as Record<string, string>,
};

export const handoverWords = {
  heading: "A campaign for you",
  loading: "Opening the offer…",
  notOpen: "This page isn't open yet.",
  noLink: "Open the link from the email we sent. If you can't find it, ask the DM to send it again.",
  signInLead: "Sign in to see this offer. Use Discord, or the email address the offer was sent to.",
  signInDiscord: "Sign in with Discord",
  signInEmail: "Sign in with your email instead",
  // (After signing in the person lands on My Account, which shows a link back here.)
  signInNote: "After you sign in, open the link in the offer email again.",
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
    shares_rules: "This campaign can only be handed over on the server it runs on now. Ask the DM to invite you there, then open this link again.",
  } as Record<string, string>,
  // Why the campaign can't move to one of the other servers, by the API's code (#2550).
  serverWhy: {
    shares_rules: "This campaign can't move to another server yet, but it can be handed over on this server.",
  } as Record<string, string>,
  cantMoveThere: "The campaign can't move to this server right now.",
  moving: "Moving your campaign. This takes a minute.",
  movingSlow: "This is taking longer than usual. If the campaign doesn't show up in My Account soon, open the link in the offer email again: if the move did not happen, the offer will be open again.",
  onlyHere: "This campaign can only be handed over on the server it runs on now. Ask the DM to invite you there, then open this link again.", // (the same words as why.shares_rules, for a person whose servers are all greyed)
  cantNow: "You can't take this campaign right now. Nothing was changed.",
  seePlans: "See the plans",
  myAccount: "Go to My Account",
  // On My Account, after signing in with a kept offer link.
  waitingOnAccount: "A campaign offer may be waiting for you.",
  waitingLink: "Open the offer",
  linkKept: "The link keeps working until 7 days after the offer was sent.",
  whereHeading: "Where should it run?",
  whereLead: "Pick the Discord server for this campaign. If you keep it on the server it runs on now, or play on a server where DMbot already runs this campaign, your Discord account simply becomes its DM there. Nothing needs installing.",
  current: "(where it runs now)",
  addBotFor: (name: string): string => `Add DMbot to ${name}`,
  needsBot: "(DMbot isn't there yet)",
  comeBack: "After adding DMbot, come back to this page.",
  noServers: "You need a Discord server you can add DMbot to. Add DMbot to one of your servers, then come back to this page.",
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
    "shares-rules": "This campaign can't move to another server yet, but it can be handed over on this server. Pick the server it runs on now.",
    network: "We couldn't reach DMbot, so nothing was changed. Try again in a moment.",
    server: "That didn't work, and nothing was changed. Try again in a moment.",
  } as Record<string, string>,
};
