/**
 * The web API the account page talks to (#435, core/src/dmbot/web/), as the site sees it.
 *
 * Security rules (#434, #435):
 * - Sign-in happens on the API: the browser goes to /auth/discord/start and comes back to
 *   /account with an httpOnly session cookie. The site never sees a Discord token, and
 *   nothing is ever put in a URL or in localStorage.
 * - Every call sends the cookie (credentials: "include") and a custom header, so a plain
 *   cross-site form can't make one (the API rejects requests without it).
 * - Discord ids are strings: they don't fit in a JavaScript number.
 */
import type { PlanId } from "../content/pricing";

export type PlanStatus = "active" | "grace" | "lapsed";

export interface MyPlan {
  id: PlanId;
  status: PlanStatus;
  /** Listening hours used this billing month (can have a fraction). */
  hoursUsed: number;
  hoursCap: number;
  /** ISO date the hours start again (the next billing day), or null. */
  renewsOn: string | null;
  /** ISO date a failed payment must be fixed by (status "grace"), or null. */
  graceEndsOn: string | null;
  /** ISO date a cancelled plan ends (it works until then and does not renew), if it was. */
  endsOn?: string;
  /** What the plan is doing (#2165): renews, cancelled but still works, another plan starts at the
   * period's end, or stopped. Missing from an API older than that. */
  state?: "active" | "ending" | "changing" | "expired";
  /** ISO date a stopped plan ended (state "expired"). */
  endedOn?: string;
  /** The plan that starts on `nextPlanOn` (state "changing"). */
  nextPlan?: PlanId;
  nextPlanOn?: string;
}

export interface Campaign {
  id: string;
  name: string;
  serverName: string;
  /** ISO date and time of the last session, or null if never played. */
  lastPlayedAt: string | null;
  status: "active" | "paused";
  /** "owner": uses your hours. "co-dm": you help run it. "player": you play in it (#2460). */
  role: "owner" | "co-dm" | "player";
  /** Importing older sessions works in this campaign's server (#2259), even while the site's own
   * IMPORT_LIVE is off: the owner's test servers. Missing from an API older than that. */
  importsOn?: boolean;
  /** The campaign's address, `<server>.<campaign>`, as the other pages' addresses (#2416). Missing
   * from an API older than that: the campaign is then not linked. */
  ref?: string;
  /** The facts on the card (#2460), each left out when there is none: DMbot is listening there
   * now, the sessions played, hours played (two decimals), player characters claimed, NPCs the
   * person may see, and when the campaign was started (ISO). Missing from an older API. */
  live?: boolean;
  sessions?: number;
  hoursPlayed?: number;
  players?: number;
  npcs?: number;
  startedAt?: string;
  /** An offer to hand this campaign over by email that nobody has answered yet (#2534; core #2532):
   * when it was sent and when it ends, never the address. Missing from an older API. */
  handover?: { sentAt: string; expiresAt: string } | null;
  /** False when the account's server list doesn't have this campaign's server, or DMbot isn't in it any more
   * (#2555; core #2554). Only the owner's campaigns can be like that. Missing from an older API. */
  serverReachable?: boolean;
  /** Whether this person may delete the campaign: the plan holder, or a game master of a campaign that
   * has no owner yet (#2555). Missing from an older API: the owner may. */
  canDelete?: boolean;
}

/** One campaign's own page (#2416): what to call it, and whether the person runs it. */
export interface CampaignPage {
  ref: string;
  name: string;
  serverName: string;
  /** "owner" and "co-dm" are the campaign's DMs; "player" is anyone else in its server. */
  role: "owner" | "co-dm" | "player";
  importsOn?: boolean;
  /** The campaign's current choices, for its game masters only (#2461). */
  settings?: CampaignSettings;
}

/** One of the three choices a game master can change on the Settings tab (#2484). */
export type CampaignSettingName = "visibility" | "level" | "retention";

/** A choice the queue carries: the three above, an optional rule switched on or off (#2753), or a game master
 * added or removed (#2768: `value` is the person's id, `on` true to add). */
export type QueuedSetting = CampaignSettingName | "optional_rule" | "co_dm" | "pause" | "story_mode" | "dndbeyond_invite" | "rulesets";

/** One of a campaign's game masters (#2768; core web/game_masters.py). `plan` is the one whose plan runs the
 * campaign (cannot be removed); `you` is the person looking (may leave). */
export interface GameMaster {
  userId: string;
  name: string;
  plan: boolean;
  you: boolean;
}

/** The campaign's game masters and the most it can have (#2768). */
export interface GameMasters {
  max: number;
  gameMasters: GameMaster[];
}

/** Someone a game master can offer as one more: a member who agreed to be recorded (#2768). */
export interface GameMasterCandidate {
  userId: string;
  name: string;
}

/** One optional rule from another book, as the Settings tab lists it (#2753; core web/optional_rules.py).
 * `changed` is true when the game master chose it, false when it follows the campaign's default. */
export interface OptionalRule {
  id: string;
  book: string;
  name: string;
  summary: string;
  on: boolean;
  changed: boolean;
}

/** The optional rules that apply to the campaign's main rules (#2753). */
export interface OptionalRules {
  /** The main rules in words, e.g. "2024 rules". */
  main: string;
  rules: OptionalRule[];
  /** A sentence about rules left out because the main rules have their own version, or "". */
  note: string;
  default: boolean;
}

/** Where a choice sent to the bot stands. `error` is the bot's word when it failed (`needs_confirm`,
 * `refused` with Discord's words in `note`, `not_allowed`, `not_found`, `bad_value`, `failed`); `note`
 * is what Discord would say about it. */
export interface CampaignSettingAnswer {
  state: "waiting" | "working" | "done" | "failed";
  error: string;
  setting: QueuedSetting;
  value: string;
  note: string;
}

/** A campaign's shared and followed house rules, for the DM (#2783). Never a share code (they are read in Discord,
 * #2460) and never a rule's words: names, versions and counts. */
export interface RuleSharing {
  published: null | { name: string; shared: boolean; version: number; hasCode: boolean };
  following: { name: string; publisher: string; version: number; updating: boolean }[];
  /** The sets other campaigns share in the same server, to follow from a list. */
  here: { setId: string; name: string; publisher: string; version: number; rules: number }[];
  /** How many house rules of its own the campaign could share. */
  ruleCount: number;
  /** Whether the owner's plan lets the campaign share. */
  canShare: boolean;
}
export type RuleSharingChoice =
  | { action: "publish"; arg: string }
  | { action: "stop_sharing" | "unfollow" }
  | { action: "look_code" | "follow_code"; arg: string }
  | { action: "follow_here"; arg: string };
/** Where one sharing choice stands: the shared progress shape and Discord's own words (`note`). */
export interface RuleSharingAnswer {
  state: "running" | "stalled" | "done" | "failed";
  stage: "waiting" | "sharing" | "done" | "failed";
  action: RuleSharingChoice["action"];
  startedAt: number;
  updatedAt: number;
  serverNow: number;
  reason: string | null;
  error: string;
  note: string;
}

/** What ⚙️ Manage DMbot holds for a campaign today. */
export interface CampaignSettings {
  visibility: "private" | "peek" | "open";
  level: "quiet" | "normal" | "chatty";
  /** How long transcripts are kept (30, 90 or 365 days), or null for ever. */
  keepDays: number | null;
  paused: boolean;
  /** The Shared story switch (#2985); missing from an API older than that. */
  storyMode?: "off" | "watch" | "guide";
  /** The campaign's invite link from dndbeyond.com, "" when none (#2985); a game master's own, as Discord's form shows it. */
  dndbeyondInvite?: string;
  /** Discord's "Main rules" ("2024" or "2014") and "If missing" (the other one, or "none"), for game masters (#2985). */
  mainRules?: "2024" | "2014";
  fallbackRules?: "2024" | "2014" | "none";
  gameMasters: number;
}

/** A Discord server you manage. */
export interface Server {
  id: string;
  name: string;
  hasDmbot: boolean;
  /** DMbot joined through a plain invite link and nobody has said who added it. */
  canLink: boolean;
  installedByYou: boolean;
}

export interface Person {
  id: string;
  name: string;
}

/** A campaign hand-over waiting for an answer (#614, #437). It ends after 7 days. */
export interface Offer {
  id: string;
  campaignId: string;
  campaignName: string;
  serverName: string;
  /** Incoming: who offers it. Outgoing: who it's offered to. */
  personName: string;
  /** ISO date and time the offer ends if nobody answers. */
  expiresAt: string;
}

/** Free access (#771, #806): never says why someone has it. */
export interface Access {
  kind: "free" | "paid" | "none";
  /** ISO date free access ends, or missing when it doesn't. */
  endsOn?: string;
  /** Free access while a paid plan still works and is still charged. */
  stillPaying?: boolean;
  /** That plan's name, as the API words it. */
  paidPlan?: string;
  /** ISO date a cancelled paid plan under free access ends (then it is not "still paying"). */
  paidPlanEndsOn?: string;
  /** Free access with no hours or campaign limit at all (the API says so; it is not guessed). */
  unlimited?: boolean;
  /** Hours for free access that has a cap (a grant "like Guild"); missing when it has none. */
  hours?: { used: number; cap: number; renewsOn: string | null };
}

/** The narrated-recaps add-on, as the API says it (#1389, web/me.py `recapAudio`). */
export interface RecapAudio {
  /** Guild, Pro, or free access: it comes with the plan. */
  included: boolean;
  /** Bought as an add-on and still running. */
  addOn: boolean;
  /** ISO date the add-on runs to, when it is one. */
  endsOn?: string;
  /** Can be bought now: for sale, a paid plan that works, and not already had. */
  canBuy: boolean;
}

/** One server a person could take a handed-over campaign to (#2534; core #2532). */
export interface HandoverServer {
  id: string;
  name: string;
  /** Where the campaign runs now. */
  current: boolean;
  /** False: DMbot has to be added to it first. */
  botIn: boolean;
  /** Why the campaign can't move to this server: a code (today `shares_rules`), never set on the server
   * it runs on now. Missing from an API older than #2550. */
  why?: string | null;
}

/** What opening a hand-over link shows (#2534). `ended` covers expired, taken back, used and unknown
 * alike, so the page can't tell anyone which. */
export type HandoverLook =
  | { status: "ended" }
  | {
      status: "open";
      campaign: { name: string };
      from: { name: string };
      server: { id: string; name: string };
      canAccept: boolean;
      /** `shares_rules`: the campaign can only be handed over on the server it runs on now, and the person is
       * not in that server (#2550); `canAccept` is false. */
      why: null | "try_it" | "no_plan" | "no_slot" | "owner" | "shares_rules";
      servers: HandoverServer[];
      /** A plain sentence, safe to show, on why the move this person asked for last time did not happen; the
       * offer is open again. Only the person who accepted ever gets one. Missing from an API older than #2550. */
      moveFailed?: string | null;
    };

/** One Discord account on a DMbot account (#2534). `key` is an opaque handle for removing it, never the
 * Discord number; the page shows `name` only. */
export interface DiscordAccount {
  key: string;
  name: string;
  /** The one signed in right now. */
  current: boolean;
  /** False for the last one. */
  canRemove: boolean;
}

export interface Me {
  user: Person;
  plan: MyPlan | null;
  /** Missing from an API older than #804: the site can go live first. */
  access?: Access;
  campaigns: Campaign[];
  servers: Server[];
  /** The Discord accounts on this DMbot account (#2534). Missing from an API older than #2533. */
  discordAccounts?: DiscordAccount[];
  // The API also sends `installs` (servers this person added DMbot to); the servers list
  // already says "You added DMbot here", so the page doesn't use it (#497).
  /** A first payment failed and none has gone through since (#922): the page says to try again. */
  firstPaymentFailed?: boolean;
  /** Where DMbot may record you. Missing from an API older than #1151. */
  recording?: RecordingEntry[];
  /** The account number (the Discord ID in base 36) and the minutes DMbot has run for this
   * account, all time (#1231). Missing from an API older than that. */
  dmbotId?: string;
  experiencePoints?: number;
  /** A paid, active plan: a move code can be asked for. */
  canMove?: boolean;
  /** A paid plan that still works: the Support form is for this account (#1340). Missing from
   * an API older than that, which shows no form. */
  canSupport?: boolean;
  /** The narrated-recaps add-on (#1390). Missing from an API older than #1389. */
  recapAudio?: RecapAudio;
  /** Hand-overs offered to you, and the ones you offered (#614). */
  offers: { incoming: Offer[]; outgoing: Offer[] };
}

/** One of the person's own characters (#1164), read-only. Fields are missing when the sheet has
 * not been read yet (a link only) or does not hold them. */
export interface Character {
  serverName: string;
  campaign: string | null;
  name: string | null;
  classLine: string | null;
  level: number | null;
  maxHp: number | null;
  ac: number | null;
  /** The online sheet's link, if there is one. */
  sheetLink: string | null;
  typedIn: boolean;
  readAt: string | null;
}

/** A server where this person said yes to recording (#1151). `on` is false when their yes was
 * given under older wording: DMbot records nobody until they say yes again in Discord. */
/** How many lines of theirs DMbot holds in a server and in how many sessions, and their "Delete my transcripts" that
 * is still open (#2769; core web/my_lines.py). Counts only. */
export interface MyLines {
  lines: number;
  sessions: number;
  open: { id: string; state: "waiting" | "working" } | null;
}
/** Where such a deletion stands: the shared progress shape, plus what it removed once done. */
export interface MyLinesStatus extends JobProgress {
  lines: number | null;
  sessions: number | null;
}

export interface RecordingEntry {
  serverId: string;
  serverName: string;
  /** ISO date they said yes. */
  agreedOn: string;
  on: boolean;
}

/** What the API says after a ticket is kept (#1340): whether the team can reply by email, that is,
 * whether the account has an address. */
export interface SupportReceipt {
  reply: boolean;
}

/** One of the person's own questions to the team (#1373): when it was sent (Unix seconds), the
 * subject, and where it stands. Never the words, and never anyone else's. */
export interface SupportQuestion {
  at: number;
  subject: string;
  /** "escalated": a person on the team has it (the helper gave it up, or the person asked). */
  status: "new" | "answered" | "closed" | "escalated";
  /** Who wrote the latest answer, and its first line (#1432). Missing from an API older than
   * that; null before any answer. The person's own words never come back. */
  answeredBy?: "helper" | "team" | null;
  latest?: string | null;
}

/** The person's own questions, newest first, and whether the team can reply by email (the
 * account has an address), so "answered" can say where to look (#1373). */
export interface MySupport {
  tickets: SupportQuestion[];
  reply: boolean;
}

/** One part of a question's whole conversation (#2193): who wrote it, the words, and when (Unix
 * seconds). "you" is the person's own first message and emailed replies. */
export interface SupportMessage {
  who: "you" | "helper" | "team";
  text: string;
  at: number;
}

/** What the Support form sends (#1340). */
export interface SupportTicket {
  subject: string;
  message: string;
  turnstile: string;
}

/** Whose address an email goes to: `payment` is the email on the payment account, `signin` the one the
 * person signed in with, `none` no address at all (#2334, #2349). */
export type EmailSource = "payment" | "signin" | "none";

/** The address DMbot emails this person at (#2349). `pending` is a new address that has been sent a
 * confirmation link and has not been confirmed yet. */
export interface MyEmail {
  address: string | null;
  source: EmailSource;
  pending: string | null;
}

/** Which address a recap's email goes to, and whether the DM's own email choice is on. */
export interface RecapEmail {
  address: string | null;
  source: EmailSource;
  on: boolean;
}

/** One session recap the DM reviews (#1382, #1386). The words come from the API already clean of
 * the music and voice cues the bot's own tools use, so the page shows `text` as it is. */
export interface Recap {
  /** The id in the page's address: `<server>.<campaign>.<session>`. */
  ref: string;
  campaign: string | null;
  serverName?: string;
  /** Unix seconds the session started. */
  sessionAt: number;
  /** The session's number in its campaign, as the transcripts number it (#2418); missing from an
   * older API, null when it can't be found. */
  sessionNumber?: number | null;
  /** How long the session ran, in seconds; null while it is still listening or if unknown. */
  sessionSeconds?: number | null;
  gameDay: string | null;
  state: string;
  status: "draft" | "posting" | "posted" | "dropped";
  /** Still being made: nothing to read yet. */
  working: boolean;
  title: string;
  text: string;
  /** The text with its cues, which is what an edit sends back. */
  summary: string;
  housekeeping: string[];
  /** Names the recap kept that DMbot didn't know: the DM's copy only, never posted (#2027). */
  toConfirm?: string[];
  /** Held until the DM answers DMbot's questions about names (#2169, #2210). Missing from an API
   * older than that: then nothing is held. */
  waitingForNames?: boolean;
  /** How many of those questions are left. */
  namesToAnswer?: number;
  /** Where this recap's email goes, for the signed-in DM only and about themselves only (#2334, #2335).
   * Missing from an API older than that: then the page names no address. */
  recapEmail?: RecapEmail;
  /** Written, and waiting for the checks to be able to run (#2280, #2297). While `working` is also
   * true, the page says so in one steady line instead of "still writing". Missing from an older API. */
  waitingForCheck?: boolean;
  /** While the recap is going out or its audio is being made (#2419): when the work started on the
   * server (Unix seconds), the step it is on, and the server's own clock now, so the page's running
   * clock is right whatever the browser's clock says and resumes after a reload. Missing from an
   * API or bot that doesn't record them yet. */
  postingSince?: number | null;
  postingStep?: "check" | "post" | "audio" | null;
  serverNow?: number;
  /** The posted message's link in Discord, when the bot kept it. */
  postedUrl?: string | null;
  /** Notes from the checks about a recap that still had things they were unsure of: the DM's copy
   * only, never posted or in a shared link (#2280). Missing from an older API. */
  criticNotes?: string[];
  /** Unix seconds the recap first waited for its check, 0 when it isn't waiting, and the numbers of
   * the sentences nothing has read (#2280). The page for reading and approving such a draft needs
   * #2305 and is not built yet. */
  uncheckedSince?: number;
  uncheckedSentences?: number[];
  /** Once the recap has waited 3 days: its draft, a sentence at a time (the numbers above count
   * these), for the DM to read before anything is shared (#2297). Empty or missing before that. */
  uncheckedDraft?: string[];
  /** Whether the API takes "approve it as it is" for such a draft (#2305). Missing from an older API. */
  canApproveUnchecked?: boolean;
  /** A fingerprint of the words this view shows, sent back with "approve it as it is" so that only the
   * words the DM read are approved (#2305). Missing from an older API. */
  textHash?: string;
  /** While it is being made: how far along (#2564, #2608). Missing from an API older than that. */
  progress?: JobProgress;
  /** Start the recap over is possible now (#2608): not while it is written or sent. */
  canMakeAgain?: boolean;
  /** When the DM last started it over (Unix seconds, 0: never). */
  restartedAt?: number;
  /** A recap written by someone at the table (#2609): who, null for DMbot's own; when it was added
   * (Unix seconds, 0 for DMbot's own). */
  writtenBy?: string | null;
  uploadedAt?: number;
  /** Why it couldn't be made, if it couldn't. */
  error: string;
  canEdit: boolean;
  canPost: boolean;
  canTryAgain: boolean;
  triesLeft: number;
  canDrop: boolean;
  /** The narration and music (#1383, #1418). Missing from an API older than that. */
  audio?: RecapAudioState;
  /** When the session's transcript was deleted by "Keep transcripts for" (Unix seconds, as the
   * Transcripts page has it), else null; the recap stays (#2022). Missing from an older API. */
  transcriptsDeletedAt?: number | null;
  /** How many days the table keeps transcripts (30, 90 or 365), null for forever. */
  keepDays?: number | null;
  /** When a fixed spelling changed this recap's text (Unix seconds), else null (#2178). A posted
   * recap's Discord message keeps the old spelling. Missing from an older API. */
  respelledAt?: number | null;
  /** The scenes and who was in each, in the order of the text (#2577; DM's view only). Missing from an
   * older API, and then the page shows nothing. */
  beats?: RecapBeat[];
  /** The player characters the DM may pick from for a scene (names only, DM's view only; #2577). */
  characters?: string[];
  /** The words a cue may use (#2084). Missing from an API older than #2083. */
  cueWords?: CueWords;
  /** The DM's approval of the text before any audio (#2083). Missing from an older API. */
  approval?: RecapApproval;
  /** On a list row: a public link is on (#2118). Missing from an older API. */
  shared?: boolean;
  /** The public link, for the DM of a posted recap (#2118). `path` is `/r/<token>`; the page adds
   * its own address. Missing from an older API. */
  share?: RecapShare;
}

/** One scene of a recap and the player characters who were there (#2577). `n` is stable while the recap
 * isn't written again. The DM's word wins: DMbot never changes a list the DM has set. */
export interface RecapBeat {
  n: number;
  present: string[];
  /** The DM set this list on the page (#2684); making the recap again clears it. Missing from an older API. */
  saved?: boolean;
}

/** One past session as the recap maker lists it (`GET /me/campaigns/{ref}/past-sessions`, #2610). */
export interface PastSession {
  /** What "make it again" takes. */
  ref: string;
  sessionAt: number;
  sessionNumber: number | null;
  sessionSeconds: number | null;
  /** Lines are kept, so a recap can be written from them. */
  hasTranscript: boolean;
  /** A recap waits for the DM's answers about names. */
  held: boolean;
  recap: null | {
    state: string;
    status: "draft" | "posting" | "posted" | "dropped";
    working: boolean;
    approved: boolean;
    writtenBy: string | null;
    canMakeAgain: boolean;
  };
}
export interface PastSessions {
  sessions: PastSession[];
}
/** A session that has notes, as a row in the list (#3027). Game masters only; newest first. */
export interface SessionNotesItem {
  sessionAt: number;
  sessionNumber: number | null;
  beats: number;
  writtenAt: number;
}
export interface SessionNotesList {
  sessions: SessionNotesItem[];
}
/** One numbered beat of a session's notes (#3027). `who` and `where` are empty when there are none. */
export interface SessionBeat {
  number: number;
  what: string;
  who: string[];
  where: string;
  kind: string;
}
export interface SessionNotes {
  sessionAt: number;
  sessionNumber: number | null;
  writtenAt: number;
  beats: SessionBeat[];
}
/** The after-session report (#2661; STORY_MEMORY 9a; shapes agreed on the issue). DM only: a player is
 * refused and nothing here is ever in the players' view. Only DMbot's own words and placeholders, never
 * a person's words or who said them. */
export interface ReportEvidence {
  /** A "source" holds a name (who told them), never words. */
  kind: "claim" | "line" | "clue" | "event" | "source";
  session: number;
  /** False: the words are gone; the page says "a line from session N, no longer available". */
  available: boolean;
  quote?: string;
}
export interface ReportStatement {
  text: string;
  /** Whose view it is: "The DM's view", "The party", or a character's name. */
  view: string;
  session?: number | null;
  evidence: ReportEvidence[];
}
export type ReportAction = "intended" | "fix" | "not_a_problem" | "remind";
export interface ReportAnswer {
  action: ReportAction | "fixed";
  at: number;
}
export interface ReportContradiction {
  id: string;
  reason: string;
  gameTimeKnown: boolean;
  first: ReportStatement;
  second: ReportStatement;
  answer: ReportAnswer | null;
  remindedFrom?: number | null;
}
export interface ReportDifference {
  id: string;
  reason: string;
  view: string;
  believes: string;
  truth: string;
  evidence: ReportEvidence[];
  answer: ReportAnswer | null;
  /** The connection "Fix the DM's view" opens in the Story Graph; null when there is none. */
  open: { a: string; b: string } | null;
  remindedFrom?: number | null;
  /** Set only for a lie (#3123); absent from an older API or any other item. */
  kind?: "lie";
  /** Who lied: a name, or null when they are gone or unknown. Only meaningful when `kind` is `lie`. */
  liar?: string | null;
}
export interface SessionReport {
  session: { at: number; number?: number | null };
  state: "none" | "making" | "ready" | "failed";
  /** A "make it again" on a session that already has a report that did not work (#2661): the report below
   * is the old one, still good. Missing when the last try worked or there was none. */
  lastRequest?: { state: "failed"; updatedAt: number } | null;
  /** While `making` or `failed`: the shared wait shape (counts and codes only). */
  progress?: JobProgress | null;
  counts: { contradictions: number; disguised: number; misunderstood: number; false: number; open: number; answered: number };
  /** Found but not sorted or graded (the call limit stopped the run): a count, never read as "nothing found". */
  notSorted?: number;
  /** A ready report the call limit cut short: the rest is checked after the next session. */
  leftForNextTime?: boolean;
  contradictions: ReportContradiction[];
  differences: { disguised: ReportDifference[]; misunderstood: ReportDifference[]; false: ReportDifference[] };
}
/** "Names fixed this session" (#2645; shapes agreed on the issue): what the name pass wrote into a session's
 * cleaned text, for the campaign's DM only. `forms[].heard` are transcript words: shown to that DM, never
 * logged, never on a public link or in a players' view. */
export interface NameFixForm {
  heard: string;
  times: number;
}
export interface NameFix {
  /** Its Undo's id (the pass's batch). */
  id: string;
  name: string;
  times: number;
  forms: NameFixForm[];
  undone: boolean;
}
export interface NamesFixed {
  fixed: NameFix[];
  /** Words the pass left as heard and asked the DM about: a count only. */
  unsure: number;
  /** New names it offered to the DM: a count only. */
  new: number;
}
/** Adding a recap someone wrote (`POST /me/campaigns/{ref}/recaps/upload`, #2609): `text` or `link`. */
export interface AddRecapInput {
  /** When the session began (Unix seconds). */
  session: number;
  writtenBy: string;
  text?: string;
  link?: string;
  title?: string;
  /** The DM said yes to replacing the recap the session has. */
  replace?: boolean;
  /** The DM said they may use it: asked once for a campaign. */
  rights?: boolean;
}

/** A recap's public link: on or off, and where it is. */
export interface RecapShare {
  on: boolean;
  path: string | null;
}
export type ShareAction = "on" | "off" | "new";

/** A shared recap as anyone with the link sees it: the table version only (#2118). */
export interface PublicRecap {
  title: string;
  text: string;
  /** Unix seconds; null for a recap the DM approved and shared without posting it (#2419). */
  postedAt: number | null;
  /** Only the kinds that exist; each is a full address that needs no sign-in. */
  audio: { voice?: string; music?: string };
  /** When it was played (Unix seconds), which session it was and how long it ran (#2418). Left out
   * by an older API. */
  sessionAt?: number | null;
  sessionNumber?: number | null;
  sessionSeconds?: number | null;
}

/** One place in a recap's text where a cue (《a mood》 or ((a pause or pace))) is wrong, as a
 * refused save says it (#2084, #2083). The API decides what is wrong; the page only shows it. */
export interface CueProblem {
  /** Which paragraph, counting from 1. */
  paragraph: number;
  /** A few words around the cue, so the DM can find it. */
  near: string;
  /** The cue as written. */
  cue: string;
  /** One of unknown, unclosed, mismatched, empty, no_words, inside_word today; any other word
   * gets a plain "this cue is wrong" line (#2083 may add kinds). */
  kind: string;
  /** The closest allowed word, or null when none is close. */
  suggest: string | null;
}

/** The words a cue may use, as the API checks them (#2084, #2083): the page shows these and
 * never keeps a list of its own. */
export interface CueWords {
  moods: string[];
  /** Other words that stand for a mood: `{ sad: "somber" }`. */
  synonyms: Record<string, string>;
  /** Pause and pace words. */
  pacing: string[];
}

/** What the DM approved a recap's text for (#2083, #2084). `approved` is true only while the text
 * is what was approved: an edit ends it. `choice` is empty until then. */
export type ApproveChoice = "voice" | "music" | "both" | "text";
export interface RecapApproval {
  approved: boolean;
  choice: ApproveChoice | "";
  /** Unix seconds, or null. */
  approvedAt: number | null;
  /** The recap is ready and not yet approved. */
  canApprove: boolean;
}

/** Reads the allowed cue words out of a recap as the API sends it. The field is `cueWords` or
 * `cue_words` (#2083 is not final), and a value of the wrong shape gives undefined, so the help
 * is simply not shown. */
export function readCueWords(body: unknown): CueWords | undefined {
  const w = (body as { cue_words?: unknown } | null)?.cue_words as
    Partial<CueWords> | undefined;
  if (!w || !Array.isArray(w.moods) || !Array.isArray(w.pacing))
    return undefined;
  const synonyms: Record<string, string> = {};
  for (const [from, to] of Object.entries(w.synonyms ?? {})) {
    if (typeof to === "string") synonyms[from] = to;
  }
  return {
    moods: w.moods.map(String),
    synonyms,
    pacing: w.pacing.map(String),
  };
}

/** A recap as the API sent it, with its cue words in the one spelling the page reads. */
export function recapFrom(body: unknown): Recap {
  const recap = body as Recap;
  const cueWords = readCueWords(body);
  return cueWords ? { ...recap, cueWords } : recap;
}

/** Reads the cue problems out of a refused save's or approval's body (`cue_problems`, as #2083's
 * code sends it); a body with none gives []. */
export function readCueProblems(body: unknown): CueProblem[] {
  const list = (body as { cue_problems?: unknown } | null)?.cue_problems;
  if (!Array.isArray(list)) return [];
  return list.flatMap((item): CueProblem[] => {
    const p = item as Partial<CueProblem> | null;
    if (!p || typeof p.paragraph !== "number" || typeof p.kind !== "string")
      return [];
    return [
      {
        paragraph: p.paragraph,
        near: String(p.near ?? ""),
        cue: String(p.cue ?? ""),
        kind: p.kind,
        suggest: typeof p.suggest === "string" ? p.suggest : null,
      },
    ];
  });
}

/** One campaign's recaps used this month, as `GET /me/recaps` says (`recapsThisMonth`, #1428,
 * #1444). `audio*` count the narrated ones. Missing from an API older than #1428. */
export interface RecapMonth {
  serverName?: string;
  campaign: string | null;
  used: number;
  allowed: number;
  audioUsed: number;
  audioAllowed: number;
}

/** Where a recap's audio has got to. `current`: made from the text as it is now. `error` is the
 * API's own plain sentence, shown as it is. */
export interface RecapAudioState {
  state: "off" | "queued" | "ready" | "failed";
  error: string;
  current: boolean;
  canAskAgain: boolean;
  /** The recap is already posted: a new narration is for this page, and the Discord message keeps
   * its old audio (#2183). Missing from an older API. */
  postedNote?: boolean;
  /** What the last making did (#2630): `new`, `voice` (only the changed paragraphs were spoken again,
   * the music kept), `all` (everything again; `why` says why) or `same`. Missing from an older API. */
  how?: RemakeHow | "";
  why?: RemakeWhy;
  /** While the audio is made: the shared progress shape (stage audio_voice / audio_music / audio_mix,
   * paragraphs spoken `done` of `total`). Missing from an older API, or when nothing is under way. */
  progress?: JobProgress | null;
}

/** What "Make the audio again" would do for this recap now (#2630; CloudDev's
 * `GET /me/recaps/{ref}/audio/remake`): only the voice, everything, nothing, or a first making. `why`
 * is a fixed code and says why everything is made again; `changed` of `total` paragraphs differ. */
export type RemakeHow = "voice" | "all" | "same" | "new";
export type RemakeWhy = "" | "paragraphs" | "moods" | "size" | "no_parts";
export interface RecapRemake {
  how: RemakeHow;
  why: RemakeWhy;
  changed: number;
  total: number;
}

/** The DM's three recap choices for a campaign (#1468), the same as the DM screen's 📜 Recaps panel.
 * `canAudio`: the narration switch may be offered (the campaign owner's plan has it, for every DM
 * of the campaign, owner or co-DM, #1470); `audioHow`: "part of your plan" or "an add-on". */
export interface RecapChoices {
  text: boolean;
  audio: boolean;
  email: boolean;
  canAudio: boolean;
  audioHow: string;
  /** Why the narration can't be made right now (#2419), for the greyed button: "plan" (not in the
   * plan), "allowance" (this month's are used up) or "not_set_up" (nothing here makes it); "" when
   * it can be made. Missing from an older API, which the page reads as "no reason known". */
  audioUnavailable?: AudioUnavailable | "";
}

// "unavailable" is what a co-DM is told instead of "plan" or "allowance" (they never learn the owner's plan).
export type AudioUnavailable = "plan" | "allowance" | "not_set_up" | "unavailable";

export type RecapChoice = "text" | "audio" | "email";

/** One name of the campaign, as the recap page lists it (#1451). `saidAs` is the DM's spelling
 * for the narrator, or null (the narrator says the name as written). Never a secret name. */
export interface CampaignName {
  id: string;
  text: string;
  kind: string;
  saidAs: string | null;
}

/** A campaign with a saved transcript in one of the person's servers (#1501). `ref` is
 * `<server>.<campaign>`. */
export interface TranscriptCampaign {
  ref: string;
  name: string;
  serverName: string;
  sessions: number;
  /** How many days the campaign keeps transcripts (30, 90 or 365), null for until the game master
   * deletes them; missing from an older API (#1575). */
  keepDays?: number | null;
}

/** "Tell DMbot what to fix" (#2114; core #2113): the DM's note is queued, the bot sorts it into
 * points, and the page polls for them. Ids are only good for the request that made them. */
export interface FixPoint {
  id: string;
  text: string;
}
export interface FixPoints {
  corrections: FixPoint[];
  style: FixPoint[];
  /** Plain-words answers (story help, "DMbot can't do that"): shown as they are. */
  replies: string[];
}
/** One new name a recap asks the DM to check (#2113). `answering` while the bot is applying an
 * answer; `error` is the bot's word when the last answer failed. */
export interface NameCheck {
  id: string;
  name: string;
  /** What the recap says about it, in the story's words. */
  note: string;
  answering: boolean;
  error: string;
  /** Up to three longer names DMbot read from the quotes ("Harrow's keep"), for Change the words (#2593). */
  longerNames?: string[];
  /** Up to two short windows of the transcript around where the name was heard, for the DM only (#2403),
   * and how many more places there are. Missing from an API older than that. */
  places?: NamePlace[];
  morePlaces?: number;
}
/** One window: lines the door only sends for speakers who agree to be recorded; a line whose words were
 * removed after "Stop recording me" is `{ removed: true }` with no speaker, time or words. */
export type NamePlaceLine = { removed: true } | { removed?: false; speaker: string; at: number; text: string };
export interface NamePlace {
  session: number;
  /** False when nothing of the window is left to show. */
  available: boolean;
  lines: NamePlaceLine[];
}
/** The Plot check of a recap (#2124; core #2122, #2123): PlotBot's questions, shown to the DM only. */
export type PlotAnswerKind = "fix" | "changed" | "fine" | "leave";
export interface PlotIssue {
  id: string;
  kind: string;
  level: "possible" | "probable" | "likely";
  /** The recap sentence's number, and its words. */
  sentence: number;
  sentenceText: string;
  reason: string;
  /** `session` is the earlier recap's title ("" when none). */
  source: { text: string; session: string };
  answer: { kind: PlotAnswerKind; note?: string } | null;
}
export interface PlotCheck {
  /** `unavailable`: the check can't run (switched off, or the bot didn't pick it up); it never holds Approve. */
  state: "checking" | "ready" | "unavailable";
  /** As of this read; the page also counts the questions it has seen answered. */
  approveBlocked: boolean;
  version: number;
  /** A fingerprint of the words that were checked. */
  textHash: string;
  issues: PlotIssue[];
  /** Earlier approved versions. `acceptedBy` is an account number: the page never shows it. */
  versions: { version: number; acceptedBy: string; acceptedAt: number }[];
}
/** A change PlotBot suggests keeping; nothing is saved unless the DM presses Save. */
export interface PlotProposal {
  id: string;
  kind: string;
  label: string;
  /** Plain words for why a Save failed (state `failed`). */
  error?: string;
  /** `saving`: Save was pressed and the bot is making the change. */
  state: "open" | "saving" | "saved" | "declined" | "failed";
}
/** One message of the DM's talk with PlotBot about one question (#2123). A DM message is `waiting` or
 * `working` until the bot has answered it, and `failed` with a short code when it could not. */
export interface PlotMessage {
  id: string;
  role: "dm" | "bot";
  text: string;
  state: "waiting" | "working" | "done" | "failed";
  error: string;
  proposals: PlotProposal[];
  at: number;
}

export interface NameCheckList {
  names: NameCheck[];
  /** The known names an answer can say a name is the same as. */
  known: { id: string; name: string; kind: string }[];
  /** The kinds a name can be said to be. */
  kinds: { key: string; label: string }[];
}
export type NameCheckAnswer =
  | { action: "confirm"; kind: string }
  | { action: "same"; known: string }
  | { action: "respell"; text: string }
  | { action: "not_a_name" }
  | { action: "explain"; text: string }
  // The DM widens or corrects the name itself (#2593; core #2570). `heard` is how it was written down.
  | { action: "change_words"; words: string; heard?: string };
export interface VoiceResult {
  state: FixState;
  /** Only when `done`: what was heard. */
  text?: string;
  /** Only when `failed`: "bad_clip", "too_long", "no_words", "transcribe_failed" or "no_consent". */
  error?: string;
}
export type FixState = "waiting" | "working" | "done" | "failed";
export interface FixResult {
  state: FixState;
  /** Only when `done`. */
  points?: FixPoints;
  /** Only when `failed`: "sort_failed", "not_allowed" or "too_soon". */
  error?: string;
}
export interface Rewritten {
  recap: Recap;
  triesLeft: number;
  needsApproval: boolean;
}

/** Importing older sessions (#2107; core #2105). The files travel as text; nothing a speaker said
 * comes back, only names and counts. */
export interface ImportFileIn {
  name: string;
  text: string;
}
export interface ImportSuggestion {
  userId: string;
  name: string;
  /** Why this person: "remembered" from an earlier import, or the "display_name" matched. */
  why: string;
}
export interface ImportSpeaker {
  /** Exactly as written in the file. */
  label: string;
  lines: number;
  suggested: ImportSuggestion | null;
}
export interface ImportPreviewFile {
  name: string;
  format: string | null;
  /** Why the file or doc can't be used, or null when it can. One of the codes the import API
   * returns: from the reader (`unreadable`, `unknown_format`, `no_speaker_names`), from the file
   * (`not_txt`, `too_big`, `empty`, `too_many_lines`, `too_many_speakers`) or from a Google Doc
   * link (`not_a_google_doc`, `repeated_link`, `doc_not_shared`, `doc_too_big`, `doc_unreachable`).
   * Each has plain words in `importPage.fileError`; test/import-error-codes.test.ts keeps the three
   * lists (core, this comment and those words) in step. */
  error: string | null;
  suggestedDate: string | null;
  lineCount: number;
  /** Lines of talk that is not the game (Craig's placeholders). */
  leftOut: number;
  speakers: ImportSpeaker[];
}
/** Why a recap can't be written for this campaign (#2229); absent when it can, or when the API
 * could not tell. `off` is a switch for the whole server, which the DM can't change. */
export type RecapsWhy = "off" | "campaign_off" | "plan";
export interface ImportPreview {
  files: ImportPreviewFile[];
  estimate: { sessions: number; fastCalls: number };
  /** Whether the bot would write a recap for this campaign; missing from an API older than #2227,
   * which the page reads as "no" so it never offers a tick the bot would refuse. */
  recapsPossible?: boolean;
  recapsWhy?: RecapsWhy;
}
export interface ImportMember {
  userId: string;
  name: string;
  /** Whether they are recorded now: lines of a member who has not agreed are dropped. Missing when
   * only a suggestion has named them: Check says what is dropped. */
  agreed?: boolean;
}
export interface ImportDropped {
  not_matched: number;
  not_agreed: number;
  bot: number;
  left_out: number;
}
export interface ImportCheckFile {
  name: string;
  error: string | null;
  leftOut: number;
  speakers: { label: string; kept: number; dropped: ImportDropped }[];
}
export interface ImportCheck {
  files: ImportCheckFile[];
  estimate: { sessions: number; fastCalls: number };
}
/** The mapping is `{ label: userId | "drop" }`; `order` starts at 1. */
export interface ImportPlan {
  mapping: Record<string, string>;
  /** `writeRecap` is sent only as `true`, and only for a session the DM ticked (#2221); the API
   * counts nothing else. */
  sessions: { file: string; date: string; order: number; writeRecap?: true }[];
}
export type ImportState = "waiting" | "working" | "done" | "failed";
/** How far a job has got, the same shape wherever a job is shown (#2564; CloudDev). Times are Unix seconds on
 * the server's clock; `stalled` is the server's word, worked out per stage; only counts and codes. */
export interface JobProgress {
  stage: string;
  done?: number | null;
  total?: number | null;
  /** What done and total count: a code (`sessions`, `parts` or `lines`). */
  unit?: string | null;
  startedAt?: number | null;
  /** The last time it moved forward (not a heartbeat). Only grows. */
  updatedAt?: number | null;
  serverNow?: number | null;
  state: "running" | "stalled" | "failed" | "done";
  reason?: string | null;
}
/** What a DM gives for Upload lore (#3022): words (typed, or read from a text file in the browser) or a link, never
 * both. `choice` is how the lore is known: `world` general knowledge, `spoilers`, or `smart` (DMbot guesses). `speaker` is
 * a character's id ("whose backstory"), or "" for none. `rights` is the right-to-use box. */
export interface LoreInput {
  source: "paste" | "file" | "link";
  text?: string;
  link?: string;
  choice: "world" | "spoilers" | "smart";
  speaker: string;
  rights: boolean;
}
/** Where an upload stands: stages and counts only, never the words (#3022). Times are the server's seconds. */
export interface LoreUpload {
  id: string;
  /** The names the document found that are still waiting for the DM's OK, by kind: counts only (#3218). */
  waiting?: Record<string, number>;
  state: "waiting" | "working" | "done" | "failed";
  stage: string;
  error: string;
  source: "paste" | "file" | "link";
  choice: "world" | "spoilers" | "smart";
  namesFound: number;
  linksFound: number;
  done: number;
  total: number;
  createdAt: number;
  updatedAt: number;
  serverNow: number;
}
/** The bot's answer to "Add all" or its Undo (#3222): counts and progress only, never a name. */
export interface ConfirmAnswer {
  state: "running" | "done" | "failed";
  stage: string;
  error?: string;
  /** The counts that were added, as the bot sends them: "place:5 character:7". */
  counts?: string;
  progress?: { done: number; total: number; at: number; createdAt: number; now: number };
  /** True when this answer can be taken back with Undo. */
  undo?: boolean;
}
/** One request about a campaign's house-rules file (#2985): stage and counts only, never the link or a rule. */
export interface HouseFileRequest {
  id: string;
  action: "link" | "unlink" | "check" | "upload";
  state: "waiting" | "working" | "done" | "failed";
  stage: string;
  error: string;
  toAdd: number;
  toChange: number;
  toRemove: number;
  rulesRead: number;
  createdAt: number;
  updatedAt: number;
}
/** Whether a house-rules file is connected, the site it is on (never the address) and a request's progress. */
export interface HouseFileState {
  linked: boolean;
  site: string;
  request: HouseFileRequest | null;
  serverNow: number;
}
export interface HouseFileInput {
  action: HouseFileRequest["action"];
  link?: string;
  text?: string;
}
/** The campaign's recap style (#2985): ids are the stores' own, to remove by. */
export interface RecapStyle {
  examples: { id: string; text: string }[];
  notes: { id: string; text: string }[];
  /** True when the campaign has no examples of its own, so DMbot's own are used. */
  defaults: boolean;
}
export interface RecapStyleChange {
  action: "add_example" | "remove_example" | "use_defaults" | "add_note" | "remove_note";
}
export interface RecapStyleAction {
  state: "waiting" | "working" | "done" | "failed";
  error: string;
  action: RecapStyleChange["action"];
  createdAt: number;
  updatedAt: number;
  serverNow: number;
}
export interface ImportStarted {
  importId: string;
  sessions: { file: string; state: ImportState; writeRecap?: boolean }[];
  estimate: { sessions: number; fastCalls: number };
  /** Asked again at the moment of starting, since the setting can change after Preview. */
  recapsPossible?: boolean;
  recapsWhy?: RecapsWhy;
}
/** Where one imported session has got to (#2230); an older API sends none and the page falls back
 * to the plain `state`. */
export type ImportStage =
  | "cleaning"
  | "checking_names"
  | "finding_names"
  | "waiting_for_names"
  | "starting_recap"
  | "writing_recap"
  | "recap_ready"
  | "recap_failed"
  | "recap_not_written"
  | "done"
  | "failed";
export interface ImportProgress {
  /** The whole import's progress (#2564); missing from an API older than that. */
  import?: JobProgress;
  sessions: {
    file: string;
    date: string;
    state: ImportState;
    reason: string | null;
    stage?: ImportStage;
    /** This session's own progress in the shared job shape (#2564), nested because the session's `state`
     * above keeps its old words. Missing from an API older than that. */
    progress?: JobProgress;
    /** This session's own recap (its address is `/account/recaps/<recap>`), sent for the stages that
     * have a recap or a held one; null otherwise, missing from an API older than #2240. */
    recap?: string | null;
    /** The name questions its recap waits on. */
    namesToAnswer?: number;
    /** True when the name check for this session was given up. */
    namesUnchecked?: boolean;
  }[];
}

/** One saved session of a campaign (#1501); times are Unix seconds, `endedAt` null when it was
 * still running or stopped early. */
export interface TranscriptSessionItem {
  number: number;
  startedAt: number;
  endedAt: number | null;
  lines: number;
  /** When its transcript was deleted (Unix seconds), else null; missing from an older API (#1548). */
  transcriptsDeletedAt?: number | null;
}

export type TranscriptVersion = "cleaned" | "heard";

/** One session to read: the file's header lines (`notes`) and its transcript, one string per line,
 * both made by the bot's own export, with the file's name for the download. */
export interface TranscriptView {
  number: number;
  version: TranscriptVersion;
  fileName: string;
  notes: string[];
  lines: string[];
  /** The offset to ask for the next page of lines (#1512), or null on the last (or missing from an
   * older API: there is no more). */
  next?: number | null;
  /** Set when this session's transcript was deleted: no lines, nothing to save (#1548). */
  transcriptsDeletedAt?: number | null;
  /** Where each "[… of off-topic chat skipped]" line is in THIS page's `lines`, and which stretch it stands
   * for (#2568; core #2567). Only the cleaned reading has them; no words are in them. */
  skips?: TranscriptSkip[];
  /** Lines kept, left out, and put back by a DM and still in force (cleaned reading). */
  filter?: { kept: number; leftOut: number; putBack: number };
  /** Who is reading, for a line fix (#2858; core #2882): a DM, or the player whose lines these are. */
  viewer?: { id: string; isDm: boolean };
  /** The lines this viewer may fix, by their index in THIS page's `lines`, with the speaker and start to name them to
   * the API (#2858). Only the cleaned reading has them. */
  fixable?: FixableLine[];
}

/** A line the viewer may fix: its index in the page's lines, and the speaker and start that name it (#2858). */
export interface FixableLine {
  line: number;
  speaker: string;
  startedMs: number;
}

/** A line fix's answer: a suggestion to the DM, or a change made at once (#2858). */
export type FixMode = "suggest" | "rewrite";

/** A line fix as the bot reports it (#2858): the shared progress shape, plus what was done. */
export interface FixAnswer extends JobProgress {
  mode: FixMode;
  /** "sent" for a suggestion to the DM, "changed" for a rewrite; null while it is still going. */
  result: "sent" | "changed" | null;
  /** True when a rewrite can be undone from the fix notes in Discord. */
  undo: boolean;
  /** Why it failed, as the words key it (`line_changed`, `secret`, …); null otherwise. */
  reason?: string | null;
}

/** One stretch of off-topic chat left out of the cleaned transcript. `line` is its marker's index in the
 * page's `lines`; `speaker`, `fromMs` and `toMs` name the stretch to the API (never a Discord id). */
export interface TranscriptSkip {
  line: number;
  speaker: number;
  fromMs: number;
  toMs: number;
  seconds: number;
  lines: number;
  /** True when a DM put it back. */
  putBack: boolean;
}

/** One line of a left-out stretch, as heard (rendered as text only). */
export interface StretchLine {
  when: string;
  who: string;
  text: string;
}

/** A campaign with house rules or a followed set in one of the person's servers (#1521). `ref` is
 * `<server>.<campaign>`. */
export interface HouseRuleCampaign {
  ref: string;
  name: string;
  serverName: string;
  rules: number;
  sets: number;
}

/** One of the campaign's own house rules, with its own number. `supersedes`: the book rule it
 * replaces; `fromSet`: the set it was merged in from, cited as the bot cites it. */
export interface HouseRuleItem {
  number: number;
  rule: string;
  supersedes: string | null;
  fromSet: string | null;
}

/** A house-rules set the campaign follows: its rules, cited as "<set> house rules, v4". */
export interface HouseRuleSet {
  name: string;
  version: number;
  cite: string;
  rules: { number: number; rule: string; supersedes: string | null }[];
}

export interface HouseRules {
  name: string;
  /** What the Download button saves the file as. */
  fileName: string;
  /** The campaign's own rules, newest number first (the order of the bot's list). */
  rules: HouseRuleItem[];
  sets: HouseRuleSet[];
}

/** A house-rules set or homebrew pack a campaign publishes (#2460). `hasCode`: a share code is on
 * (the code itself is read in Discord, never here). */
export interface SharedRuleSet {
  kind: "house-rules" | "homebrew";
  name: string;
  version: number;
  shared: boolean;
  hasCode: boolean;
}

/** A set or pack a campaign uses from another table. `updating` is false for one that stopped being
 * shared: its last version stays, marked "no longer updated". */
export interface FollowedRuleSet {
  kind: "house-rules" | "homebrew";
  name: string;
  publisher: string;
  version: number;
  updating: boolean;
}

/** One campaign you run that shares or uses rules. `ref` is `<server>.<campaign>`. */
export interface SharedRulesCampaign {
  ref: string;
  name: string;
  serverName: string;
  publishes: SharedRuleSet[];
  follows: FollowedRuleSet[];
}

/** One session on the Story timeline (#2462): its number, when it started and ended (Unix seconds),
 * its recap if one is approved (a player sees posted ones only; `ref` is its page, for a game master
 * only) and the names shown to the players at the table that session. */
export interface TimelineSession {
  number: number;
  startedAt: number;
  endedAt: number | null;
  recap: { title: string; posted: boolean; ref: string | null } | null;
  revealed: string[];
}

export interface Timeline {
  ref: string;
  name: string;
  /** Oldest first. */
  sessions: TimelineSession[];
}

/** A campaign with homebrew in one of the person's servers (#1525).
 * `ref` is `<server>.<campaign>`. */
export interface HomebrewCampaign {
  ref: string;
  name: string;
  serverName: string;
  entries: number;
  /** How many homebrew packs the campaign follows; missing from an older API (#1586). */
  packs?: number;
}

/** One homebrew entry. */
export interface HomebrewEntry {
  kind: string;
  name: string;
  summary: string | null;
  text: string;
  /** From a rulebook the game master shared, as the Discord players' card tags it (#1579);
   * missing from an older API, which shows no tag. */
  fromBook?: boolean;
  /** A 2014 book while the campaign plays a newer ruleset: tagged [Legacy 2014]. */
  legacy?: boolean;
  /** False when the game master turned it off (#1629): kept in the list, marked (off). Missing
   * from an older API, which means on. */
  enabled?: boolean;
  /** Where it came from, in the bot's words: "from dndbeyond.com", "credited to dndbeyond.com" or
   * "by Sam" (#1653). Null for the DM's own entries; never who added it. Missing from an older API. */
  source?: string | null;
}

/** A homebrew pack the campaign follows, with its entries. */
export interface HomebrewPack {
  name: string;
  version: number;
  entries: HomebrewEntry[];
}

export interface HomebrewPage {
  name: string;
  /** The packs the campaign follows; missing from an older API (#1586). */
  packs?: HomebrewPack[];
  /** What the Download button saves the file as. */
  fileName: string;
  /** Each kind's entries by name, kinds in no fixed order (the page orders them). */
  entries: HomebrewEntry[];
}

/** The three audio files, as short-lived addresses to play. */
export type RecapAudioLinks = Partial<
  Record<"voice" | "music" | "mix", string>
>;

export type RecapAction = "post" | "again" | "drop";

/** Who knows a name or a connection, for the DM's full view (#3167; core #3166). Five plain states. */
export type KnowWord = "secret" | "hidden_from_the_players" | "known_to_an_npc" | "partly_revealed" | "known_to_the_party";

/** One name on the Story Graph (#2324). `secret` is sent to the campaign's DMs only. */
export interface WebNode {
  id: string;
  name: string;
  /** Who knows it, in the DM's full view only (#3167). With it, the old secret and not-revealed words give way. */
  knowWord?: KnowWord;
  /** The kind's key ("npc", "place", ...). */
  kind: string;
  secret?: boolean;
  /** False: the story hasn't revealed it to the players yet (#2405). Only in a view that shows
   * unrevealed things: a DM's full view, or a player's peek. */
  revealed?: boolean;
  /** True when this name has an NPC page (#2887; core #2893). Absent from an older API. */
  page?: boolean;
  /** The quests this name is joined to directly, the most-connected first, even a quest outside the steps drawn
   * (#2992). Only for a DM in Full View: nobody else is told of a quest or a tie to one. */
  quests?: { id: string; name: string; links: number }[];
}

/** How sure the party is of a connection it only suspects (#2405, docs/STORY_MEMORY.md). */
export interface WebBelief {
  /** Under 1 for a theory. */
  confidence: number;
  /** Plain words for what it is, such as "Party theory" or "Accusation". */
  status: string;
  /** Full view only: the party believes this and the truth holds it false. */
  contradicted?: boolean;
}

/** One connection between two names, for the edge panel. `direction` says who does it to whom, from
 * the edge's `a` and `b`: "a-b" (a does it to b), "b-a", or "both". */
/** What sort of connection it is, worked out from its relationship type (core #2942). */
export type WebLinkKind = "family" | "ally" | "group" | "work" | "other";

export interface WebLink {
  id: string;
  words: string;
  secret: boolean;
  /** Who knows the connection, in the DM's full view only (#3167). */
  knowWord?: KnowWord;
  /** Who may know a held connection, for the DM's full view (#2935 D4, #3214): only the DM, some characters, or
   * everyone at the table. Absent from an older API. */
  who?: "dm" | "some" | "all";
  /** The characters that know it, when `who` is "some". */
  knownBy?: { id: string; name: string }[];
  direction: "a-b" | "b-a" | "both";
  /** The kind of connection, for the map's colours and filters (#2942). Absent from an older API. */
  kind?: WebLinkKind;
  /** How it reads from the second name when that differs ("has as a member"); null or missing
   * when it reads the same either way or has no fixed reading back (#2666). */
  back?: string | null;
  /** DMbot's check of a pronoun, in plain words, for a DM only (#2859). */
  check?: string;
  /** False: not revealed to the players yet (#2405). */
  revealed?: boolean;
  /** A theory the party holds rather than a fact (#2405). */
  belief?: WebBelief;
  /** The DM pinned it as 100% truth (#2448, API #2447). Sent to a DM in the full view only; the page
   * also drops it for anyone else. */
  pinned?: boolean;
}

/** The line between two names: the most relevant connection's words, "(+more)" for the others. */
export interface WebEdge {
  a: string;
  b: string;
  label: string;
  more: number;
  links: WebLink[];
}

/** The web around one name, within some number of hops (#2324, API #2323). */
export interface WebGraph {
  /** The name in the middle (the API's pick when the page asked for none). */
  center: string;
  nodes: WebNode[];
  edges: WebEdge[];
  /** The API stopped at its cap of names. */
  truncated: boolean;
  /** The largest number of hops that is useful for this campaign. */
  maxHops: number;
  /** Only the campaign's DMs can change anything. */
  canEdit: boolean;
  /** Who may see the DM screen's things in this campaign (#2405): "private" is the DM only. */
  visibility?: "private" | "peek" | "open";
  /** A player may turn peeking on (the visibility allows it). */
  canPeek?: boolean;
  /** Which web this is. */
  view?: WebView;
  /** The web as one character knows it (#2999): whose it is. Null or left out for the DM's own web. */
  seeAs?: { viewer: string; name: string } | null;
}

/** One group of linked names, as the overview lists it (#2632): the name to put in the middle to see it. */
export interface WebGroup {
  center: string;
  centerName: string;
  size: number;
  /** The centre name has a page. */
  page?: boolean;
}

/** Every name the person may see, with the ones that have no connection yet and the groups of linked
 * names (#2632). The graph alone draws one centre's web, so a young campaign looked almost empty. */
export interface WebOverview {
  counts: {
    names: number;
    /** Connections the person may see (a DM's include the secret ones). */
    connections: number;
    connectedNames: number;
    notConnected: number;
  };
  /** The names with no connection yet, by kind then name. A list, not drawn. */
  tray: WebNode[];
  /** Groups of two or more linked names, the largest first. */
  groups: WebGroup[];
  /** The API stopped at its cap of names. */
  truncated: boolean;
  canEdit: boolean;
  view?: WebView;
}

export interface WebQuery {
  /** null: the API picks the most connected name. */
  center: string | null;
  hops: number;
  /** "players": exactly what a player sees. "full": everything, for a DM or a player who peeks.
   * Left out, the API picks for the person (#2405). */
  view?: WebView;
  /** A character's id, or "party": read the web as that one knows it (#2999). Left out for the DM's own web. */
  seeAs?: string;
}

/** A name a DM may look at the web as (#2999): a player character or an NPC. */
export interface WebViewer {
  id: string;
  name: string;
  kind: "pc" | "npc";
}
export interface WebViewers {
  viewers: WebViewer[];
  /** Every name that matches, so the page can say when it shows only some. */
  total: number;
}

/** Which web the page asks for (#2405). */
export type WebView = "players" | "full";

/** A name or a connection the DM shows the players (#2405). */
export type WebRevealTarget = { kind: "name" | "link"; id: string };

/** One change to the connections between names (DM only). */
export type WebLinkChange =
  | { action: "add"; a: string; b: string; words: string; secret: boolean }
  | { action: "words"; id: string; words: string }
  | { action: "secret"; id: string; secret: boolean }
  | { action: "pin"; id: string; pinned: boolean }
  // "everyone" is the table's answer; the read side calls the same answer "all" (WebLink.who). The bot's door takes
  // these names with #3202, so this change is not sent while WHO_KNOWS_LIVE is off.
  | { action: "who"; id: string; who: "dm" | "some" | "everyone"; characters?: string[] }
  | { action: "remove"; id: string; confirm: true };

/** One name in a campaign's memory, as the Names page lists it (#2295). */
export interface NameRow {
  id: string;
  name: string;
  /** The kind's key; its words are in the page's `kinds`. */
  kind: string;
  /** Other names it goes by: nicknames and old spellings. */
  others: string[];
  /** Only the campaign's DMs see a secret name. */
  secret: boolean;
  /** "Players don't know this one yet" on the whole entry (#2321); what the secret switch sets. */
  hidden?: boolean;
  /** For a DM: which of `others` are secret names. */
  secretOthers?: string[];
  /** The NPC's own page (#2266), for an NPC; null otherwise. */
  npcId: string | null;
  /** For a DM, when the entry has a main name: its id, which the said-as calls take (#2317). */
  aliasId?: string;
  /** For a DM, how the narrator says that main name; null when it says it as written (#2317). */
  saidAs?: string | null;
  /** How often the name has been mentioned, for choosing which of several to keep (#2427). Missing
   * from an API that doesn't send it. */
  mentions?: number;
  /** A person the story described and never named (#2803): the name is how the page calls them ("Ken's
   * sister"). Only a DM's list has them, in the group `not_named`. */
  unnamed?: boolean;
  /** For an unnamed person: who they are, in the story's words ("sister"). */
  unnamedWhat?: string;
  /** For an unnamed person: the name they hang from ("Ken"), or null when it is not a name. */
  unnamedAnchor?: string | null;
}

export interface NameKind {
  key: string;
  label: string;
  /** How many names of this kind the campaign has, whatever is typed in the search. */
  count: number;
}

export type NameSort = "name" | "newest";

/** One page of the Names list. `canEdit` is true for the campaign's DMs only. */
export interface NamesPage {
  campaign: string;
  canEdit: boolean;
  names: NameRow[];
  kinds: NameKind[];
  total: number;
  page: number;
  pages: number;
  pageSize: number;
}

/** The answer to "Search by meaning" (#2733): the campaign's names nearest in meaning to the words, best
 * first, at most 50, in the same row shape as the list. Needs the meaning search deployed (core, after #2664):
 * without it the API refuses with `not-available` and the page says so. */
export interface SmartNamesAnswer {
  names: NameRow[];
  /** True when only the newest notes were searched (the search looks at the newest 10,000 at most; core #2756). */
  capped?: boolean;
}

export interface NamesQuery {
  q: string;
  /** Kind keys joined with commas ("npc,place"), or "" for every kind. */
  kind: string;
  sort: NameSort;
  page: number;
  /** Names per page: 20, 50, 200 or 500 (core refuses any other); the API's usual (50) when missing. */
  size?: number;
  /** Ask for the people the story never named too (#2803): without it the list, its counts and its totals
   * leave them out, whatever the page's switch says. A DM's list only; anyone else gets none either way. */
  unnamed?: boolean;
}

/** What a DM can do to one name: the same actions as the name card in Discord (#2295). */
export type NameChange =
  | { action: "spell"; name: string }
  // `player`: for a player character, the Discord ID of who plays it (#2985); the bot checks the person is in the server.
  | { action: "kind"; kind: string; player?: string; confirmed?: true }
  | { action: "add-other"; other: string }
  | { action: "remove-other"; other: string }
  // "Players don't know this one yet" on the whole entry; `secret` is one other name (#2294).
  | { action: "hide"; hidden: boolean }
  | { action: "secret"; other: string; secret: boolean }
  | { action: "merge"; into: string }
  // Several names into the one the request is about (the kept name, whose id is in the path): one
  // change, one Undo, all or nothing (#2427, the contract CloudDev wrote on #2426).
  | { action: "merge-many"; others: string[] }
  | { action: "remove"; confirm: true };

/** The three steps of giving a name to someone the story never named (#2803; the bot applies each one). */
export type NameThemStep =
  | { action: "name"; name: string }
  // "Yes, the same person": joined into the name DMbot already knows, which is kept.
  | { action: "name-join"; into: string }
  // Takes back a name just given or a join; `answer` is the id the step came back with.
  | { action: "name-undo"; answer: string }
  // Who a Discord ID is (#3004): the bot asks Discord and writes nothing; the answer is shown for the DM to confirm.
  | { action: "kind"; kind: "player_lookup"; player: string };

/** How a step ended: done (`undo` is the id to take it back with, null when it can't be), or a question
 * because DMbot already knows someone with that name. */
export type NameThemResult =
  | { state: "done"; row: NameRow | null; undo: string | null; answer: string }
  | { state: "question"; other: { id: string; name: string } }
  | { state: "player"; name: string; inServer: boolean };

/** What the bot says about a queued name change (#2294). */
export interface NameAnswer {
  state: "waiting" | "working" | "done" | "failed";
  row: NameRow | null;
  removed?: boolean;
  /** A store-side refusal, such as "exists" or "not_found". */
  error?: string | null;
  /** The entry DMbot already knows, when a new name is one (#2803). */
  other?: { id: string; name: string } | null;
  /** True while the step can still be undone (#2803). */
  undo?: boolean;
  /** Who a looked-up Discord ID is: the name Discord shows, and whether they are in this server yet (#3004). */
  player?: { name: string; inServer: boolean } | null;
}
export const NAME_POLL_MS = 1000;
export const NAME_POLLS = 60;
export const READING_POLLS = 150;
/** What the web's answers say about one queued edit of a connection or a reveal (#2323, #2405). */
interface WebAnswer {
  state: "waiting" | "working" | "done" | "failed";
  error?: string | null;
  edge?: WebEdge | null;
  removed?: boolean;
  /** An Undo of a name fix: how many lines were put back (#2645). */
  putBack?: number | null;
  /** An answer about a reading (#2412): whether it can be undone, and where the wait stands. */
  undoable?: boolean;
  waits?: WaitingWaits;
  /** A Keep all on "Waiting for you" (#2921): how many connections are done of how many. */
  progress?: { done: number; total: number } | null;
}

/** The changed row, or `{ removed: true }` after a removal. */
export type NameChanged = NameRow | { removed: true };

/** One character's sheet as the page shows it (#2816): names and numbers only, the link to the player and the DMs. */
export interface SheetCharacter {
  entityId: string;
  name: string;
  /** True for the character the person plays. */
  mine: boolean;
  /** Where the sheet came from: a link from dndbeyond.com, or typed in; null when there is none. */
  source: "dndbeyond" | "typed" | null;
  /** The link, for the player and the campaign's DMs only; null when there isn't one. */
  url: string | null;
  fetchedAt: number | null;
  summary: {
    species: string | null;
    level: number | null;
    classes: { name: string; level: number; subclass: string | null }[];
    maxHp: number | null;
    ac: number | null;
  } | null;
}

export interface CharacterSheets {
  /** True for the campaign's DMs, who see every sheet and only read them. */
  isDm: boolean;
  sheets: SheetCharacter[];
}

/** One choice on a character sheet (#2816, #2832): a player links, forgets or types in their own; a DM reads them
 * again. `typed` (#2832): `names` is a comma-separated string; the bot keeps at most 20 of them. */
export type SheetChoice =
  | { action: "link"; url: string }
  | { action: "forget" }
  | { action: "read_again" }
  | { action: "typed"; className: string; level: number; species: string; names: string }
  /** A public web page or PDF the player made, read by the bot later (#2843). The page only checks its shape. */
  | { action: "document"; url: string };

/** Where a sheet choice stands, in the shared progress shape (#2816). */
export interface SheetAnswer {
  state: "running" | "stalled" | "done" | "failed";
  stage: string;
  action: string;
  entityId: string;
  error: string | null;
  progress?: JobProgress | null;
  startedAt?: number | null;
  updatedAt?: number | null;
}

/** One NPC's page, as the players see it, plus the caller's own note (#2266). */
export interface NpcPage {
  id: string;
  name: string;
  kind: string;
  isGroup: boolean;
  alsoCalled: string[];
  groups: string[];
  groupsMore: number;
  ties: string[];
  tiesMore: number;
  /** Posted recaps that mention the name; `ref` is `<server>.<campaign>.<time>`. */
  recaps: { ref: string; title: string; day: string }[];
  /** The caller's own private note; empty when there is none. */
  myNote: string;
  isDm: boolean;
  /** The DM's own note on the entry, for a DM of the campaign only; null for anyone else. */
  dmNote: string | null;
}

/** One of the caller's notes. `listed` is false for a name that has left the NPC list. */
export interface MyNpcNote {
  id: string;
  name: string;
  body: string;
  updatedAt: number;
  listed: boolean;
}

/** What waits for the DM (#2635, #2652; core `GET /me/campaigns/{ref}/web/waiting`, DMs only). */
export interface WaitingClaim {
  id: string;
  /** The claim as one plain line, "Mira Voss knows Far Hall". */
  statement: string;
  /** "True, but keep it secret" is not offered for a condition ("Roan is dead"). */
  canSecret: boolean;
  /** When the session it was said in began (Unix seconds). */
  at?: number | null;
}
export interface WaitingBackstory {
  id: string;
  subject: string;
  object: string;
  /** The connection in words, "knows" or "grew up in". */
  how: string;
  quote?: string;
  secret: boolean;
  /** The label of the upload it came from; empty for one queued before labels. */
  upload: string;
  /** False while a name in it is not confirmed yet; `waitingOn` says which. */
  ready: boolean;
  waitingOn: string[];
  /** DMbot's check of a pronoun, in plain words, for a DM only (#2859). */
  check?: string;
}
export interface WaitingWhich {
  id: string;
  /** The written name, "Mara". */
  word: string;
  /** True for a question about a pronoun ("she"): its candidates may be one name or none. */
  pronoun?: boolean;
  other: string;
  otherIsSubject: boolean;
  how: string;
  quote?: string;
  secret: boolean;
  upload: string;
  candidates: { id: string; name: string; limited: boolean }[];
  /** DMbot's check of a pronoun, in plain words, for a DM only (#2859). */
  check?: string;
}
export interface Waiting {
  claims: WaitingClaim[];
  backstory: WaitingBackstory[];
  which: WaitingWhich[];
  /** The true sizes (the lists are cut at their caps). */
  counts: { claims: number; backstory: number; ready: number; which: number; all: number };
}
/** What people believe (#2412 slice 3b; core PR 2693): readings DMbot found that the DM may keep as a belief.
 * DMbot's own words only: names, a relationship, whose view and how sure; never a line or a speaker. */
export type ReadingRule = "theory" | "accusation" | "lie" | "reveal" | "confession" | "evasion" | "false_clue" | "plot";
export interface Reading {
  id: string;
  rule: ReadingRule;
  /** Whose belief: "the party", "the DM's view" or a character's name. */
  who: string;
  statement: string;
  /** In words: "fairly sure", "doubtful", "a guess", "seen", "dodged", "false", "secret plot". */
  sureness: string;
  /** Who lied, for a suspected lie only (#3121): a name, or null when it is gone or unknown. Null on any other rule. */
  liar: string | null;
  /** The session's day (Unix seconds). */
  sessionAt: number;
  /** False while a new name in it has to be answered first, in Check new names. */
  canPlace: boolean;
  why: string | null;
  waitingOn: string[];
}
export interface Readings {
  readings: Reading[];
  counts: { readings: number };
  /** The list is cut at a cap: `counts.readings` is the true size. */
  capped: boolean;
}
/** Where an answer's wait stands: counts and stages only. */
export interface ReadingWaits {
  stage: string;
  waitedSeconds: number;
  lastMovedAt: number;
  stalled: boolean;
}
export type ReadingAnswer = { action: "place" | "no"; id: string } | { action: "undo"; id: string };
/** What the page hears while an answer about a reading waits (once a second). */
export interface ReadingLook {
  waits: ReadingWaits | null;
}
/** Where an answer on "Waiting for you" stands (#2921; core #2923): counts and stages only, never a connection's words. */
export interface WaitingWaits {
  stage: string;
  waitedSeconds: number;
  lastMovedAt: number;
  stalled: boolean;
  /** The server's clock when it was read (Unix seconds), so a wrong clock on the computer shows no wrong age. */
  now?: number;
}
/** What the page hears while an answer on "Waiting for you" waits, each time it looks. */
export interface WaitingLook {
  waits?: WaitingWaits | null;
  progress?: { done: number; total: number } | null;
}
/** One answer to a waiting item; the API queues it for the bot and the page waits for it. */
export type WaitingAnswer =
  | { kind: "claim"; action: "confirm" | "reject"; id: string; secret?: boolean }
  | { kind: "backstory"; action: "keep" | "skip"; id: string; secret?: boolean }
  | { kind: "backstory"; action: "keep_all"; ids: string[] }
  | { kind: "which"; action: "pick"; id: string; entity: string }
  | { kind: "which"; action: "neither"; id: string };

/** Sent on `window` whenever the real API answers "not signed in" (#2301). */
export const SIGNED_OUT_EVENT = "dmbot:signed-out";

/** Why a call failed, in a form the page can turn into plain words. */
export type ApiErrorKind =
  | "ambiguous" // two people could be the one a new name is for, so nothing was changed (#2803)
  | "hour-full" // the campaign's game masters have sent thirty talk messages to PlotBot this hour (#2774)
  | "plan-needed" // sharing house rules needs a plan that allows it (#2783)
  | "no-rules" // there is no house rule of the campaign's own to share (#2783)
  | "bad-choice" // a sharing choice the API cannot read (#2783)
  | "not-owner" // only the campaign's owner can pause it (#2985)
  | "full" // three examples already, so no fourth (#2985)
  | "busy" // a recap that is being written or sent now can't be made again (#2608, #2609)
  | "no-lines" // the session's transcript is gone, so there is nothing to write from (#2608)
  | "needs-rights" // the campaign's first added recap: say you may use it (#2609)
  | "not-a-google-doc" // a link that is not a Google Doc (#2609)
  | "doc-not-shared" // a Google Doc that is not shared with the link (#2609)
  | "doc-unreachable" // a Google Doc DMbot could not fetch (#2609)
  | "doc-too-big" // a Google Doc that is too long (#2609)
  | "network" // couldn't reach the API
  | "peek-off" // the DM turned peeking off, so the full view is closed (#2405)
  | "bad-target" // a merge, secret or remove the rules refuse for this name (#2379)
  | "still-waiting" // a queued name change the bot hasn't answered yet (#2294)
  | "gone" // a waiting connection that is not waiting any more (#2635)
  | "name-first" // a new name in it has to be answered first, in Check new names (#2635)
  | "stopped-recording" // someone it cites is no longer recorded, so the fact was thrown away (#2635)
  | "cannot-write" // the rules refuse to write that fact now; it stays waiting (#2635)
  | "queue-full" // too many connections wait already: answer some first (#2652)
  | "bad-reading" // the reading is not one DMbot can keep (#2412)
  | "unknown-character" // a character in it is not in this campaign (#2412)
  | "unknown-liar" // the one who lied is not known, or is not a character in this campaign (#3121)
  | "waiting-already" // the belief is already waiting to be kept (#2412)
  | "plot-is-known" // the secret plot is already known (#2412)
  | "already-known" // the belief is already kept (#2412)
  | "stale" // the recap changed since the page read it, so its scenes were read again (#2577)
  | "not-available" // the meaning search can't be used for this campaign yet (no vectors made, or switched off) (#2733)
  | "bad-present" // a name that isn't one of the campaign's characters (#2577)
  | "not-in-server" // an NPC page is for the people in the campaign's Discord server (#2266)
  | "bad-type" // a spoken note in a format the API doesn't read (#2113)
  | "bad-clip" // a spoken note that can't be heard as audio
  | "voice-off" // turning a recording into letters is switched off on the server (#2316)
  | "no-consent" // the DM has not agreed to be recorded
  | "signed-out" // the session ended
  | "no-free-slot" // accepting a hand-over: you have no free campaign slot
  | "not-allowed" // not your campaign, or not allowed right now
  | "sign-in-again" // acting for a server or deleting needs a sign-in from the last day
  | "try-it-used" // Try It was used before
  | "has-plan" // a plan already works (Try It or a second checkout)
  | "payments-off" // the payment company isn't set up yet
  | "already-linked" // someone else already said they added DMbot to this server
  | "not-installed" // DMbot isn't in that server yet
  | "offer-gone" // the hand-over offer ended, expired or was answered already
  | "confirm-again" // the delete confirmation ran out (10 minutes) or belongs elsewhere
  | "no-paid-plan" // the billing page needs a paid plan
  | "free-access" // free access: there's nothing to pay for
  | "slow-down" // too many tries from this address (the API limits sign-in, billing, delete)
  | "bad-link" // an emailed sign-in link that is wrong, used or ended (#1231)
  | "bad-speaker" // "whose backstory" is not a character of this campaign (#3022)
  | "bad-text" // a fix must be between 1 and 200 characters (#2858)
  | "same" // a fix that is what the line already says (#2858)
  | "not-yours" // only the speaker or the DM may fix a line (#2858)
  | "not-recorded" // the speaker has stopped being recorded, so the line can't be changed (#2858)
  | "line-changed" // the line was changed by someone else since the page was read (#2858)
  | "line-gone" // the line is gone (#2858)
  | "not-written" // DMbot could not save the change (#2858)
  | "not-available" // fixing lines is not on right now (#2858)
  | "fix-open" // this line already has a fix waiting for the DM (#2858)
  | "fix-many" // the person already has three fixes waiting for the DM (#2858)
  | "no-dm-screen" // the campaign has no DM screen to send a fix to (#2858)
  | "fix-queue-full" // too many fixes are waiting in this campaign (#2858)
  | "fix-bad-choice" // a fix choice the bot does not know (#2858)
  | "player-stopped" // the speaker has stopped being recorded (#2858)
  | "secret" // the change would reveal a secret, so it can't be made (#2858)
  | "bad-document-link" // a document link the bot can't read (#2843); its own words, not the character link's
  | "not-ready" // the API has no move codes yet (#1231)
  | "no-email" // no payment email is stored yet, so a move code has nowhere to go (#1246)
  | "needs-approval" // "Make the audio again" on a text that is no longer approved (#2090)
  | "too-soon" // the account moved less than 30 days ago (#1246)
  | "read-too-soon" // a DM asked for the sheets to be read again less than a minute ago (#2816)
  | "email-failed" // the move code could not be emailed just now (#1246)
  | "not-paying" // a support ticket needs an account with a plan, Try It too (#1340, #1435)
  | "one-waiting" // a free account already has a question waiting (#1435)
  | "daily-limit" // a free account sent 3 questions in the last day (#1435)
  | "not-human" // the person check wasn't passed or wasn't there (#1340)
  | "ticket-empty" // the subject or the message was empty (#1340)
  | "ticket-too-long" // the message is over 4,000 letters (#1340)
  | "subject-too-long" // the subject is over 120 letters (#1340)
  | "not-found" // a recap that isn't there, or isn't yours to see (#1386)
  | "not-ending" // Renew when the plan was not cancelled, or has already run out (#2165)
  | "use-billing-page" // the company will not undo the cancellation from here: renew on its page (#2165)
  | "in-use" // another account already uses that address (#2349)
  | "bad-email" // an address that is not an email address (#2349)
  | "bad-name" // a name that is empty or has a letter DMbot can't keep (#2295)
  | "bad-kind" // a kind the campaign doesn't have (#2295)
  | "bad-player" // a Discord ID that is not an ID (#2985)
  | "bot-player" // that ID is a bot, which cannot play a character (#2985)
  | "unknown-id" // Discord does not know that ID (#3004)
  | "lookup-failed" // Discord could not be asked: nothing was linked (#3004)
  | "name-exists" // another name already goes by that (#2295)
  | "needs-confirm" // a removal sent without its confirmation (#2295)
  | "bad-page" // a page of the Names list that isn't there any more (#2295)
  | "no-tries" // no Try again left, so no rewrite (#2114)
  | "sort-failed" // DMbot could not sort the note into points (#2114)
  | "not-editable" // the recap was posted, dropped or is being made, so it can't change (#1386)
  | "bad-text" // the recap text is empty or too long (#1386)
  | "bad-cues" // the recap text has cues that are wrong; `cueProblems` says where (#2084)
  | "fixed-lines" // the opening or closing line of a recap was edited; they are DMbot's (#2630)
  | "already-included" // the plan already has narrated recaps (#1390)
  | "already-subscribed" // the add-on is already bought (#1390)
  | "audio-off" // narration isn't switched on, so nothing would make it (#2183)
  | "audio-unavailable" // the narration can't be made here, or this month's are used up (#2419)
  | "not-in-plan" // the narration can't be turned on: the plan doesn't have it (#1468)
  | "bad-choice" // not one of the three recap choices (#1468)
  | "too-big" // a transcript too long to show or save as one file (#1512)
  | "too-many-lines" // a file has more lines than one import takes (#2107)
  | "too-many-speakers" // a file has more different names than one import takes (#2107)
  | "try-it-campaign" // importing is not part of Try It (#2107)
  | "too-many-files" // more than 20 files in one import (#2107)
  | "bad-files" // the files could not be read as sent (#2107)
  | "bad-mapping" // the speaker choices don't fit the files (#2107)
  | "bad-date" // a session's date is not a date (#2107)
  | "no-lines-kept" // every line of a file would be dropped (#2107)
  | "not-posted" // only a posted or approved recap can have a public link (#2118, #2119)
  | "bad-action" // not one of on, off or new (#2118)
  | "bad-setting" // a campaign setting the site does not change (#2484)
  | "bad-value" // a choice Discord does not offer for it (#2484)
  | "plan-holder" // the game master whose plan runs the campaign cannot be removed (#2768)
  | "too-many-masters" // the campaign already has the most game masters (#2768)
  | "unknown-person" // not someone DMbot can offer as a game master (#2768)
  | "bad-answer" // the Plot check's answer was not one it takes (#2122)
  | "plot-open" // Approve waits for the Plot check's open questions (#2122)
  | "not-yet" // a draft is opened as it is only after 3 days of waiting (#2305)
  | "changed" // the draft was revised since the page showed it (#2305)
  | "bad-hash" // the page sent no fingerprint of the words (#2305)
  | "ended" // a hand-over offer that has ended (#2534)
  | "owner-offer" // the person is the campaign's own owner (#2534)
  | "bad-server" // a server DMbot can't run the campaign on (#2534)
  | "shares-rules" // the campaign shares a rule set, so it can't move to another server yet (#2550)
  | "session-running" // a session is running, so the campaign can't be deleted (#2555)
  | "name-mismatch" // the typed name isn't the campaign's (#2555)
  | "no-backup" // a Try It campaign has no backup (#2555)
  | "backup-failed" // the copy could not be made, or was not ready in time (#2751)
  | "nothing-to-delete" // the person has no lines of theirs in that server to delete (#2769)
  | "backup-too-many-files" // more transcripts files than one backup carries (#2772)
  | "backup-part" // one file of a backup did not come through, so the set is not complete (#2772)
  | "backup-unfinished" // a copy that can never be a whole backup (its main file already taken, or files missing)
  | "last-account" // the only Discord account on a DMbot account can't be removed (#2534)
  | "not-a-backup" // the file is not a DMbot backup (#2745)
  | "too-many-parts" // the copy asks for more transcripts files than the restore takes (#2745)
  | "bad-part" // a transcripts file that is not one of the copy's (#2745)
  | "files-missing" // transcripts files are still to come (#2745)
  | "not-receiving" // the copy has already gone to DMbot (#2745)
  | "restore-plan" // the plan has no loading of copies, or no free campaign slot (#2745)
  | "too-slow" // a file took too long to arrive (#2745)
  | "restore-working" // DMbot has started the restore, so it can't be taken back (#2745)
  | "server"; // anything else

/** A backup loaded from the website (#2745; core web/restores.py). The shared progress shape, plus what the page
 * needs: the copy's own name (from the file the person picked), how many transcripts files it asked for and has,
 * and, once it has ended, the campaign it made and how many lines were left out for people who had deleted theirs. */
export interface RestoreStatus extends JobProgress {
  id: string;
  name: string;
  /** The restore swaps one of the person's own campaigns for the copy. */
  replacing: boolean;
  /** The campaign it replaces (its address), so the page can name it after a reload. */
  replaceRef: string | null;
  partsWanted: number;
  partsReceived: number;
  /** The person chose to restore without the transcripts files. */
  withoutParts: boolean;
  /** The new (or replaced) campaign's address, once it is made. */
  campaign: string | null;
  leftOut: number;
  /** Unix seconds the staged copy is deleted if nothing happened. */
  expiresAt: number;
}

/** How the page asks for a copy being made on DMbot's side (#2751): first after 1.5 s, then slower, to 10 s. */
export const BACKUP_POLL_MS = 1500;
export const BACKUP_POLL_MAX_MS = 10_000;
export const BACKUP_GIVE_UP_MS = 16 * 60_000;
/** One file that has not arrived in this long is a download that is stuck (DMbot keeps the copy 15 minutes). */
export const BACKUP_FILE_TIMEOUT_MS = 3 * 60_000;
/** What a failed copy's `reason` means to the page (core web/campaign_backup.py REASONS). */
export const backupReasons: Record<string, ApiErrorKind> = {
  plan: "no-backup",
  too_big: "too-big",
  too_many_files: "backup-too-many-files",
  not_allowed: "not-allowed",
  not_found: "not-found",
};

/** One file of a downloaded backup, with the name Discord's own backup gives it (#2772). */
export interface BackupFile {
  name: string;
  blob: Blob;
}
/** A backup as a set: the main file first, then its transcripts files in order. Keep them together. */
export interface BackupSet {
  files: BackupFile[];
}
/** A name safe to save under: letters, digits, dots, dashes and underscores. */
export const safeBackupName = (name: string, fallback: string): string => name.replace(/[^A-Za-z0-9._-]+/g, "-").slice(0, 160) || fallback;

export class ApiError extends Error {
  constructor(
    readonly kind: ApiErrorKind,
    readonly cueProblems: CueProblem[] = [],
  ) {
    super(kind);
    this.name = "ApiError";
  }
}

export interface AccountApi {
  /** Who is signed in, or null when nobody is. */
  me(): Promise<Me | null>;
  /** Where the "Sign in with Discord" button goes. */
  signInUrl(): string;
  signOut(): Promise<void>;
  /** End every sign-in this person has, this one too (#1163). */
  signOutEverywhere(): Promise<void>;
  /** Start the free Try It plan (no card). */
  startTryIt(): Promise<void>;
  /** The payment company's checkout page for a paid plan. */
  checkoutUrl(plan: PlanId): Promise<string>;
  /** The payment company's page to change plan, fix a payment or stop. */
  billingPortalUrl(flow?: "plans"): Promise<string>;
  /** Renew (#2165): undo a cancellation that has not taken effect. The plan renews on its date as before. */
  renewPlan?(): Promise<void>;
  /** The payment company's page to add narrated recaps (#1390). Missing from an API older than #1389. */
  recapAudioUrl?(): Promise<string>;
  /** The payment company's page to buy more hours for this month (a paid, working plan). */
  extraHoursUrl(): Promise<string>;
  /** The file for "Download my data": the person's own data as JSON (#1150). Fetched from the
   * page, so a refusal (once a minute, or signed out) can be said in words. */
  downloadExport(): Promise<Blob>;
  /** Email a one-use sign-in link if the address has an account (#1231). It always resolves
   * the same way, so the page can't tell anyone whether an address has an account. */
  emailSignInRequest?(email: string): Promise<void>;
  /** Use the token from an emailed link: signs this browser in (#1231). */
  emailSignInUse?(token: string): Promise<void>;
  /** Email a move code to the payment email (paid plans, #1231). */
  getMoveCode?(): Promise<void>;
  /** The owner hands a campaign over by email (#2534; core #2532). Always answers the same, whatever the
   * address is: the page never learns whether it has an account or a plan. */
  offerHandover?(ref: string, email: string): Promise<void>;
  /** Take the open offer back. */
  takeBackHandover?(ref: string): Promise<void>;
  /** Delete a campaign for good, with its name typed to confirm (#2555; core #2554). `channelsLeft` is true
   * when DMbot couldn't reach the server, so its Discord channels stay. Only the plan holder may. */
  deleteCampaign?(ref: string, name: string): Promise<{ channelsLeft: boolean }>;
  /** Load a backup (#2745; core web/restores.py): the copy's file goes to DMbot, which applies it after checking the
   * plan, the free slot and the rights again. `replaceRef` swaps one of the person's own campaigns for it; none
   * makes a new one. Answers the copy's own name and how many transcripts files it asks for. A restore of theirs
   * that is still on its way refuses with `slow-down`: `restoreStatus(serverId)` finds it. Missing from an older API. */
  restoreStage?(
    serverId: string,
    file: Blob,
    replaceRef?: string,
  ): Promise<{ id: string; name: string; partsWanted: number }>;
  /** One transcripts file of a staged copy (1 to the count it asked for); the same number again replaces it. */
  restorePart?(serverId: string, id: string, number: number, file: Blob): Promise<void>;
  /** Send the staged copy to DMbot, with every transcripts file in, or without any (`without`). */
  restoreGo?(serverId: string, id: string, without: boolean): Promise<void>;
  /** Where the person's restore stands; with no id, their latest in that server. Null when there is none. */
  restoreStatus?(serverId: string, id?: string): Promise<RestoreStatus | null>;
  /** Take a staged copy back before DMbot starts it, or clear one that has ended. */
  restoreCancel?(serverId: string, id: string): Promise<void>;
  /** The whole campaign as a backup file, offered on the campaign's page and before Delete (anyone in the server; not Try It). DMbot builds it
   * from its database (#2554): asked for with a POST, polled in the shared progress shape (`onProgress` hears each
   * answer: stage, how long, last moved), then fetched once. Refusals: `no-backup` (the plan has no copies),
   * `too-big`, `not-allowed`, `not-found`, `too-soon`, `backup-too-many-files` (more transcripts files than one
   * backup carries), `backup-part` (one of its files did not come through, so the set is not complete) and
   * `backup-failed` (it could not be made, or took longer than the 15 minutes it is kept). The answer is the
   * main file and every transcripts file, the same set Discord's `/dmbot backup` sends (#2772); `onFile` hears
   * how many are fetched ("2 of 7"). */
  campaignBackup?(
    ref: string,
    onProgress?: (progress: JobProgress) => void,
    onFile?: (done: number, total: number) => void,
  ): Promise<BackupSet>;
  /** The hand-over accept page (#2534; core #2532). The token is sent in the body, never in an address. */
  handoverLook?(token: string): Promise<HandoverLook>;
  /** `{moving: true}` (HTTP 202) when the campaign has to move to another server first: the bot does it within
   * about a minute (#2550). */
  handoverAccept?(token: string, serverId: string): Promise<{ campaignRef: string } | { moving: true }>;
  handoverDecline?(token: string): Promise<void>;
  /** Where "Add a Discord account" goes: Discord's own sign-in, then back to My Account with a result (#2534). */
  addDiscordUrl?(): string;
  /** Take one Discord account off this DMbot account; never the last (#2534). */
  removeDiscordAccount?(key: string): Promise<void>;
  /** The email DMbot uses for this person (recap emails, #2349): only their own, never logged.
   * Missing from an API older than that. */
  myEmail?(): Promise<MyEmail>;
  /** Ask to change it: a confirmation link goes to the new address and nothing changes until it is
   * opened. Not for a payment email, which is changed with the payment company. */
  changeEmail?(address: string): Promise<{ pending: string | null }>;
  /** Open the emailed link's code (`#email-confirm=...`): changes the email, but only for the same
   * signed-in account that asked. A wrong, used or ended code is `bad-link`. */
  confirmEmail?(token: string): Promise<void>;
  /** Stop a change that is waiting for its link, so a typo can be cancelled. */
  cancelEmailChange?(): Promise<void>;
  /** Send a private ticket to the DMbot team (paid plans, #1340). Missing from an API older
   * than that. `turnstile` is Cloudflare's token, or "" when the site has no key. */
  sendSupport?(ticket: SupportTicket): Promise<SupportReceipt>;
  /** The DM's recent session recaps, newest first (#1386). Missing from an API older than that. */
  recaps?(): Promise<Recap[]>;
  /** The same list with each campaign's recaps used this month (#1444). The page prefers it;
   * `thisMonth` is empty from an API older than #1428. */
  recapList?(): Promise<{ recaps: Recap[]; thisMonth: RecapMonth[] }>;
  /** A campaign's past sessions with what each one's recap is (DMs only; #2608). */
  pastSessions?(campaignRef: string): Promise<PastSessions>;
  /** A campaign's sessions that have notes, for its game masters only (#3027). Missing from an older API. */
  sessionNotesList?(campaignRef: string): Promise<SessionNotesList>;
  /** One session's numbered notes, `at` in Unix seconds (#3027). Missing from an older API. */
  sessionNotes?(campaignRef: string, at: number): Promise<SessionNotes>;
  /** The after-session report for one session (DMs only; #2661), `at` in Unix seconds. Missing from an
   * API older than the report. */
  sessionReport?(campaignRef: string, at: number): Promise<SessionReport>;
  /** Start making the report (`state` was `none` or `failed`); the report comes back as it stands. */
  makeSessionReport?(campaignRef: string, at: number): Promise<SessionReport>;
  /** One answer to one item, queued for the bot like #2638's; resolves once it is saved. `undo` puts the
   * item back to open. A refusal is an `ApiError` (`gone`, `cannot-write`, `queue-full`). */
  answerReport?(campaignRef: string, at: number, id: string, action: ReportAction | "undo"): Promise<void>;
  /** Save who was in one scene before the recap is approved (DM only; #2577): the whole new list, 0 to 12
   * names, each one of the recap's `characters`. Refusals: `not-found`, `stale`, `not-allowed`,
   * `bad-present`. */
  setBeatPresent?(recapRef: string, n: number, present: string[]): Promise<RecapBeat>;
  /** What the name pass fixed in this recap's session (DMs only; #2645). Missing from an older API. */
  namesFixed?(recapRef: string): Promise<NamesFixed>;
  /** Undo one fix, queued for the bot like the DM screen's Undo; resolves with how many lines were put back.
   * A refusal is an `ApiError` (`gone`, `not-allowed`, `cannot-write`, `queue-full`). */
  undoNameFix?(recapRef: string, id: string): Promise<{ putBack: number }>;
  /** Make a recap for a session, or make it again (`POST /me/recaps/{ref}/again`, #2608); `replace` after
   * the DM said yes to "This session already has a recap". The recap comes back, queued. */
  makeRecap?(recapRef: string, replace: boolean): Promise<Recap>;
  /** Add a recap someone wrote (#2609); the recap comes back with `writtenBy` set. */
  addRecap?(campaignRef: string, input: AddRecapInput): Promise<Recap>;
  recap?(ref: string): Promise<Recap>;
  /** Save the DM's edit of the recap text and its housekeeping bullets. */
  saveRecap?(
    ref: string,
    summary: string,
    housekeeping: string[],
  ): Promise<Recap>;
  /** Approve the text and pick what to make: voice-over, music, both or text only (#2083). The
   * API checks the cues again and refuses with `bad-cues`; any choice but text queues audio. */
  /** `post`: approve the text-only choice and hand it to the bot to post, in one call (#2419). An
   * older API ignores it and leaves the recap approved and not posted. */
  approveRecap?(ref: string, choice: ApproveChoice, options?: { post?: boolean }): Promise<Recap>;
  /** "Use this draft as it is" for a recap unchecked for 3 days (#2297, #2305): it then goes on as
   * a finished draft for the DM to edit and approve as usual. Missing from an older API. */
  approveUnchecked?(ref: string, textHash: string): Promise<Recap>;
  /** Short-lived addresses of the recap's audio (#1418). Empty when there is none to play. */
  recapAudioLinks?(ref: string): Promise<RecapAudioLinks>;
  /** The DM's three recap choices for a campaign, and whether the narration may be offered
   * (#1468). `campaignRef` is `<server>.<campaign>`. Missing from an API older than that. */
  recapChoices?(campaignRef: string): Promise<RecapChoices>;
  /** Change one of them; answers all three as they are now. */
  setRecapChoice?(
    campaignRef: string,
    which: RecapChoice,
    on: boolean,
  ): Promise<RecapChoices>;
  /** One page of a campaign's names, searched, filtered and sorted (#2295). `ref` is
   * `<server>.<campaign>`. Missing from an API older than that. */
  allNames?(ref: string, query: NamesQuery): Promise<NamesPage>;
  /** Names by meaning, for "ice goddess" or "friend of Brannoc" (#2733). The words go in a POST body, never the
   * address (a query string is written to logs). Missing from an API older than that. */
  smartNames?(ref: string, words: string): Promise<SmartNamesAnswer>;
  /** One change to one name (DMs only); the same action as the name card in Discord. */
  /** One campaign's page (#2416). Missing from an API older than that. */
  campaignPage?(campaignRef: string): Promise<CampaignPage>;
  /** Upload lore (#3022; core web/lore.py): DMbot reads the words and offers the names and connections for the DM to
   * check. Answers where it stands. Refusals: `needs-confirm` (the right-to-use box was not ticked), `slow-down` (one is
   * already open: `loreRead(ref)` finds it), `too-soon` (five an hour), `bad-text`, `ticket-too-long`, `bad-speaker`,
   * `not-allowed`, `not-found` while the server switch is off. Missing from an older API. */
  loreAdd?(campaignRef: string, input: LoreInput): Promise<LoreUpload>;
  /** The campaign's house-rules file (#2985; core web/house_file.py): whether one is connected, the site it is on, and
   * the newest request (or the one `id` names). DM only: `not-allowed` for anyone else, `not-found` while the server
   * switch is off. Missing from an older API. */
  houseFile?(campaignRef: string, id?: string): Promise<HouseFileState>;
  /** Queue a request about the file: connect or change it, stop using it, compare now, or upload text once. Answers the
   * state with that request. Refusals: `slow-down` (one is already open: `houseFile(ref)` finds it), `too-soon` (ten an
   * hour), `bad-text`, `ticket-too-long`, `not-allowed`, `not-found` while the server switch is off. */
  houseFileAdd?(campaignRef: string, input: HouseFileInput): Promise<HouseFileState>;
  /** The campaign's recap style (#2985; core web/recap_style.py): the DM's own example recaps and standing notes, and
   * whether DMbot's own examples are in use. DM only. */
  recapStyle?(campaignRef: string): Promise<RecapStyle>;
  /** Queue one change to the recap style for the bot; answers the id to look at with `recapStyleAction`. Refusals:
   * `bad-action`, `bad-choice` (nothing, too many words, or a bad id), `ticket-too-long`, `too-soon`, `not-allowed`,
   * `not-found` while the server switch is off. */
  changeRecapStyle?(campaignRef: string, action: RecapStyleChange["action"], arg?: string): Promise<{ id: string }>;
  /** Where a queued change stands: state, error word, times; never its words. */
  recapStyleAction?(campaignRef: string, id: string): Promise<RecapStyleAction>;
  /** One upload's progress, or with no id the newest of the last day; null when there is none. */
  loreRead?(campaignRef: string, id?: string): Promise<LoreUpload | null>;
  /** One NPC's page. `campaignRef` is `<server>.<campaign>`, `entry` the NPC's id (#2266). */
  npcPage?(campaignRef: string, entry: string): Promise<NpcPage>;
  /** Save my private note on an NPC; an empty note clears it. */
  saveNpcNote?(campaignRef: string, entry: string, body: string): Promise<void>;
  /** All of my notes in a campaign, including those on names no longer listed. */
  myNpcNotes?(campaignRef: string): Promise<MyNpcNote[]>;
  /** Delete all of my notes in a campaign; how many there were. */
  deleteMyNpcNotes?(campaignRef: string): Promise<number>;
  changeName?(ref: string, id: string, change: NameChange): Promise<NameChanged>;
  /** Give a name to someone the story never named, join them into a name DMbot knows, or undo either (#2803).
   * `onLook` hears each time the bot's answer is read, for the wait panel. Missing from an API older than that. */
  nameThem?(ref: string, id: string, step: NameThemStep, onLook?: () => void): Promise<NameThemResult>;
  /** The Story Graph around a name (#2324, API #2323). Missing from an older API. */
  webGraph?(ref: string, query: WebQuery): Promise<WebGraph>;
  /** How many names that web would draw, asked before drawing so a big one can be warned about. */
  webSize?(ref: string, query: WebQuery): Promise<{ names: number }>;
  /** The Story Graph's overview: counts, names with no connection yet, groups (#2632). Missing from an API
   * older than that: then the page is the graph alone. */
  webOverview?(ref: string, query: { view?: WebView }): Promise<WebOverview>;
  /** The names to offer under "Find a name" in this view: only what the caller may see there, so a DM in
   * Players View is never offered a name the players don't know (#2989). Missing from an older API. */
  webFind?(ref: string, query: { q: string; view?: WebView }): Promise<{ id: string; name: string; kind: string }[]>;
  /** The names a DM may look at the web as, player characters first; `q` narrows them (#2999). */
  webViewers?(ref: string, q: string): Promise<WebViewers>;
  /** "Add all" under a finished upload (#3222): queue the kinds ("all" or the kind keys); the answer is polled. */
  confirmKinds?(ref: string, upload: string, kinds: "all" | string[]): Promise<{ answer: string }>;
  /** Undo an "Add all" answer (#3222): queued, then polled like the others. */
  confirmUndo?(ref: string, answer: string): Promise<{ answer: string }>;
  /** One look at an "Add all" or Undo answer (#3222). */
  confirmAnswer?(ref: string, answer: string): Promise<ConfirmAnswer>;
  /** What waits for the DM, and one answer to it (#2635, #2652). `answerWaiting` resolves with how many
   * connections a Keep all kept, when the API says. Missing from an API older than that. */
  webWaiting?(ref: string): Promise<Waiting>;
  /** `onLook` hears how the wait stands each time the page looks (#2921). */
  answerWaiting?(ref: string, answer: WaitingAnswer, onLook?: (look: WaitingLook) => void): Promise<{ kept?: number }>;
  /** What people believe, waiting for the DM, and one answer to it (#2412; DM only: anyone else gets
   * `not-found`). `answerReading` queues the answer for the bot and waits for it, telling `onLook` how the
   * wait stands each time it looks; it resolves with the answer's id and whether it can be undone. */
  webReadings?(ref: string): Promise<Readings>;
  answerReading?(
    ref: string,
    answer: ReadingAnswer,
    onLook?: (look: ReadingLook) => void,
  ): Promise<{ answerId: string; undoable: boolean }>;
    /** A player who agreed to the warning peeks behind the DM screen; the DM is told once (#2405). */
  webPeek?(ref: string): Promise<void>;
  /** A DM shows a name or a connection to the players (#2405). */
  webReveal?(ref: string, target: WebRevealTarget): Promise<void>;
/** Add, reword, mark secret or remove a connection. Resolves with the edge as it now stands, or
   * `{ removed: true }` when the last connection between two names went. */
  changeWebLink?(ref: string, change: WebLinkChange): Promise<WebEdge | { removed: true }>;
  /** The campaigns with a transcript in the person's servers, for anyone in them (#1501). Missing
   * from an API older than that. */
  transcriptCampaigns?(): Promise<TranscriptCampaign[]>;
  /** A campaign's name and its saved sessions, newest first. `campaignRef` is `<server>.<campaign>`. */
  transcriptSessions?(
    campaignRef: string,
  ): Promise<{
    name: string;
    keepDays?: number | null;
    sessions: TranscriptSessionItem[];
  }>;
  /** One session to read, in one version. */
  transcript?(
    campaignRef: string,
    number: number,
    version: TranscriptVersion,
    offset?: number,
  ): Promise<TranscriptView>;
  /** The same text as a file to save, as Discord's download button gives it. */
  /** The as-heard words of one left-out stretch (anyone who may read the transcript; #2568). */
  transcriptStretch?(campaignRef: string, number: number, skip: TranscriptSkip): Promise<StretchLine[]>;
  /** A DM puts a stretch back into the cleaned transcript, or leaves it out again; answers how many lines changed. */
  transcriptPutBack?(campaignRef: string, number: number, skip: TranscriptSkip): Promise<number>;
  transcriptLeaveOut?(campaignRef: string, number: number, skip: TranscriptSkip): Promise<number>;
  transcriptFile?(
    campaignRef: string,
    number: number,
    version: TranscriptVersion,
  ): Promise<Blob>;
  /** Send one settings choice to the bot (#2484): the answer to poll. A shorter transcript limit
   * deletes old sessions, so it goes with `confirm: true` once the game master has said yes. */
  setCampaignSetting?(
    campaignRef: string,
    choice: { setting: QueuedSetting; value: string; confirm?: boolean; on?: boolean },
  ): Promise<string>;
  /** The optional rules that apply to the campaign and which are on (#2753), for its game masters. Missing
   * from an API older than that. A rule is switched with `setCampaignSetting({setting: "optional_rule",
   * value: <id>, on})`. */
  campaignOptionalRules?(campaignRef: string): Promise<OptionalRules>;
  /** The campaign's game masters (#2768), for its game masters. Missing from an API older than that. A change is
   * sent with `setCampaignSetting({setting: "co_dm", value: <userId>, on})`. */
  campaignGameMasters?(campaignRef: string): Promise<GameMasters>;
  /** The character sheets the person may see in a campaign (#2816): their own, and for a DM all of them, read only. */
  campaignSheets?(campaignRef: string): Promise<CharacterSheets>;
  /** One sheet choice (#2816), queued for the bot: returns the answer's id to poll. `entity` is "-" for a DM's read again. */
  chooseSheet?(campaignRef: string, entity: string, choice: SheetChoice): Promise<string>;
  /** Asks to fix one line of a session's cleaned transcript; the answer comes back through `fixAnswer` (#2858). */
  fixTranscriptLine?(
    campaignRef: string,
    number: number,
    line: { speaker: string; startedMs: number },
    text: string,
  ): Promise<{ answer: string; mode: FixMode }>;
  /** Where one line fix stands (#2858). */
  fixAnswer?(campaignRef: string, number: number, answer: string): Promise<FixAnswer>;
  /** Where one of the person's own sheet choices stands (#2816). */
  sheetAnswer?(campaignRef: string, answer: string): Promise<SheetAnswer>;
  /** Who can be offered as a game master, searched by name (#2768): at most 25, members who agreed to be recorded. */
  campaignGameMasterCandidates?(campaignRef: string, query: string): Promise<GameMasterCandidate[]>;
  /** Where a choice sent with `setCampaignSetting` stands. */
  campaignSettingAnswer?(campaignRef: string, answer: string): Promise<CampaignSettingAnswer>;
  /** The campaign's sessions in order with recaps and what was revealed (#2462). Missing from an
   * older API. */
  timeline?(campaignRef: string): Promise<Timeline>;
  /** The campaigns with house rules in the person's servers, for anyone in them (#1521). Missing
   * from an API older than that. */
  houseRuleCampaigns?(): Promise<HouseRuleCampaign[]>;
  /** The sets and packs your campaigns share and use (#2460), for the campaigns you run. Missing
   * from an older API. */
  sharedRules?(): Promise<SharedRulesCampaign[]>;
  /** A campaign's house rules and the sets it follows. `campaignRef` is `<server>.<campaign>`. */
  houseRules?(campaignRef: string): Promise<HouseRules>;
  /** The same rules as the file the bot's Download button sends. */
  houseRulesFile?(campaignRef: string): Promise<Blob>;
  /** What the campaign shares and follows (DMs only, #2783); "not allowed" for anyone else. */
  ruleSharing?(campaignRef: string): Promise<RuleSharing>;
  /** Queue one sharing choice for the bot; resolves with the id to look at. */
  chooseRuleSharing?(campaignRef: string, choice: RuleSharingChoice): Promise<string>;
  /** Where a choice sent with `chooseRuleSharing` stands. */
  ruleSharingAnswer?(campaignRef: string, answer: string): Promise<RuleSharingAnswer>;
  /** Whether DMbot may use this person's words to improve itself (#1610); on by default. Missing
   * from an older API, which shows no switch. */
  improvement?(): Promise<boolean>;
  /** Turn that on or off for this person, and say what is saved. */
  setImprovement?(on: boolean): Promise<boolean>;
  /** The campaigns with homebrew the DM shared, for anyone in the server (#1525). Missing from an
   * API older than that. */
  homebrewCampaigns?(): Promise<HomebrewCampaign[]>;
  /** A campaign's shared homebrew. `campaignRef` is `<server>.<campaign>`. */
  homebrew?(campaignRef: string): Promise<HomebrewPage>;
  /** The same entries as the file the bot writes for them. */
  homebrewFile?(campaignRef: string): Promise<Blob>;
  /** The campaign's public names with the narrator's spelling (#1451). `campaignRef` is the
   * recap's address without its last part: `<server>.<campaign>`. */
  campaignNames?(campaignRef: string): Promise<CampaignName[]>;
  /** Set (a string) or clear (null) how the narrator says one name. */
  saveSaidAs?(
    campaignRef: string,
    nameId: string,
    saidAs: string | null,
  ): Promise<void>;
  /** "How to say it" (#2317, API #2316): a respelling of the written name, for the DM to take, change or
   * ignore. Missing from an API older than that. */
  suggestSaidAs?(campaignRef: string, nameId: string): Promise<string>;
  /** One short recording of the DM saying the name (webm or ogg, at most 10 seconds and 1 MB); the
   * API turns it into a respelling and deletes the clip. Resolves with the respelling, unsaved. */
  respellFromVoice?(campaignRef: string, nameId: string, clip: Blob): Promise<string>;
  /** "Make the audio again": the API allows it once in 10 minutes for a recap. */
  askRecapAudioAgain?(ref: string): Promise<Recap>;
  /** What that would do (#2630): only the voice or the whole audio, and why. Missing from an API older
   * than #2630; the page then offers "Make the audio again" as before. */
  recapAudioRemake?(ref: string): Promise<RecapRemake>;
  /** "Tell DMbot what to fix" (#2114): send a note, follow the request, and rewrite with the points
   * the DM kept. Missing from an API older than #2113. */
  /** The recap's new names to check, and the choices for answering them (#2114 part 3). */
  nameChecks?(ref: string): Promise<NameCheckList>;
  /** The Plot check (#2124). Missing from an API older than #2122. */
  plotCheck?(ref: string): Promise<PlotCheck>;
  answerPlotIssue?(ref: string, issue: string, answer: { kind: PlotAnswerKind; note?: string }): Promise<PlotIssue>;
  /** The DM's message about one question: queued for PlotBot, who answers a little later. */
  plotChatSend?(ref: string, issue: string, text: string): Promise<void>;
  /** The talk about one question, oldest first; ask again while a message is waiting. */
  plotChat?(ref: string, issue: string): Promise<PlotMessage[]>;
  /** Save or decline a suggested note; saving also answers the issue "the story changed". */
  decidePlotProposal?(ref: string, issue: string, proposal: string, action: "save" | "decline"): Promise<PlotProposal>;
  /** Queue one answer for the bot; the name leaves the list once it is applied. */
  answerNameCheck?(ref: string, id: string, answer: NameCheckAnswer): Promise<void>;
  sendFix?(ref: string, text: string): Promise<{ requestId: string }>;
  fixResult?(ref: string, requestId: string): Promise<FixResult>;
  /** "Speak instead" (#2113 part 2): send a recorded note (webm or ogg, at most 2 MB and 2 minutes) and
   * follow the request. The clip is turned into words and deleted at once; only the words come back. */
  sendVoice?(ref: string, clip: Blob): Promise<{ requestId: string }>;
  voiceResult?(ref: string, requestId: string): Promise<VoiceResult>;
  rewriteWith?(
    ref: string,
    requestId: string,
    corrections: string[],
    style: string[],
  ): Promise<Rewritten>;
  /** Importing older sessions (#2107): look at the files, find people to match, check what would be
   * kept, start, and follow the work. Missing from an API older than #2105. */
  importPreview?(
    ref: string,
    files: ImportFileIn[],
    links?: string[],
  ): Promise<ImportPreview>;
  importMembers?(ref: string, q: string): Promise<ImportMember[]>;
  importCheck?(
    ref: string,
    files: ImportFileIn[],
    mapping: Record<string, string>,
    links?: string[],
  ): Promise<ImportCheck>;
  importStart?(
    ref: string,
    files: ImportFileIn[],
    plan: ImportPlan,
    links?: string[],
  ): Promise<ImportStarted>;
  importProgress?(ref: string, importId: string): Promise<ImportProgress>;
  /** Turn a posted recap's public link on, off, or make a new one (#2118). */
  shareRecap?(ref: string, action: ShareAction): Promise<RecapShare>;
  /** A shared recap by its link's secret, for anyone, signed in or not (#2118). */
  publicRecap?(token: string): Promise<PublicRecap>;
  /** Post to the table (the bot sends it), Try again, or Don't post. */
  decideRecap?(ref: string, action: RecapAction): Promise<Recap>;
  /** The person's own questions to the team (#1373). Missing from an API older than that. */
  mySupport?(): Promise<MySupport>;
  /** The whole conversation of one of the person's own questions, oldest first, named by the time
   * it was sent as the list gives it (#2193). Missing from an API older than that. */
  supportThread?(at: number): Promise<SupportMessage[]>;
  /** The person's own characters. Missing from an API older than #1164. */
  characters?(): Promise<Character[]>;
  /** Stop recording me in one server (#1151). Starting again stays in Discord. */
  stopRecording(serverId: string): Promise<void>;
  /** The person's own line counts in a server, and a deletion of theirs that is open (#2769). Missing from an older
   * API, and answering `not-found` while the server has it switched off. */
  myLines?(serverId: string): Promise<MyLines>;
  /** Ask the bot to delete the person's own lines in the server, as Discord's 🗑 button does; the id of the request
   * (the same one again while it is open). Refusals: `nothing-to-delete`, `too-soon`, `not-found`. */
  deleteMyLines?(serverId: string): Promise<string>;
  /** Where that deletion stands. */
  myLinesStatus?(serverId: string, id: string): Promise<MyLinesStatus>;
  /** Where "Add DMbot" goes: the API, which sends the browser on to Discord. */
  installUrl(serverId: string): string;
  /** Say you added DMbot to a server it joined through a plain link. */
  linkServer(serverId: string): Promise<void>;
  /** The campaign's other DMs, who can be offered it (never filtered by plan: the owner
   * must not learn whether someone pays, #437). Missing until the API has the route
   * (#704's follow-up); the page then shows no Hand over button. */
  handoverCandidates?(campaignId: string): Promise<Person[]>;
  /** Offer a campaign to one of them. Missing until the API has the route, as above. */
  handover?(campaignId: string, toUserId: string): Promise<void>;
  /** Take a campaign offered to you (needs a free campaign slot now). */
  acceptOffer(offerId: string): Promise<void>;
  /** Say no to a campaign offered to you. */
  declineOffer(offerId: string): Promise<void>;
  /** Take back a hand-over you offered. */
  withdrawOffer(offerId: string): Promise<void>;
  /** Step 1 of deleting the account: returns a short-lived token for step 2. */
  requestDelete(): Promise<string>;
  /** Step 2: delete the account and its data now. */
  confirmDelete(token: string): Promise<void>;
}

/** The fix routes' own codes (#2858). Kept apart from the shared table so a bare `open` or `many` from any other
 * route can never show the fix words. Only the two fix calls read it. */
export const fixErrorKinds: Record<string, ApiErrorKind> = {
  bad_text: "bad-text",
  bad_speaker: "bad-speaker",
  same: "same",
  not_yours: "not-yours",
  not_recorded: "not-recorded",
  line_changed: "line-changed",
  line_gone: "line-gone",
  not_written: "not-written",
  not_available: "not-available",
  open: "fix-open",
  many: "fix-many",
  no_dm_screen: "no-dm-screen",
  queue_full: "queue-full",
  bad_choice: "bad-choice",
  player_stopped: "player-stopped",
  secret: "secret",
};

export const errorKinds: Record<string, ApiErrorKind> = {
  plan: "plan-needed",
  no_rules: "no-rules",
  not_owner: "not-owner",
  full: "full",
  bad_arg: "bad-choice",
  gone: "gone",
  ambiguous: "ambiguous",
  cannot_write: "cannot-write",
  queue_full: "queue-full",
  stale: "stale",
  bad_reading: "bad-reading",
  unknown_character: "unknown-character",
  unknown_liar: "unknown-liar",
  waiting_already: "waiting-already",
  plot_is_known: "plot-is-known",
  already_known: "already-known",
  bad_present: "bad-present",
  not_available: "not-available",
  no_lines: "no-lines",
  needs_rights: "needs-rights",
  not_a_google_doc: "not-a-google-doc",
  doc_not_shared: "doc-not-shared",
  doc_unreachable: "doc-unreachable",
  doc_too_big: "doc-too-big",
  no_free_slot: "no-free-slot",
  not_allowed: "not-allowed",
  sign_in_again: "sign-in-again",
  try_it_used: "try-it-used",
  try_it_has_plan: "has-plan",
  has_paid_plan: "has-plan",
  payments_off: "payments-off",
  already_linked: "already-linked",
  not_installed: "not-installed",
  confirm_again: "confirm-again",
  no_paid_plan: "no-paid-plan",
  offer_gone: "offer-gone",
  name_first: "name-first",
  stopped_recording: "stopped-recording",
  has_free_access: "free-access",
  slow_down: "slow-down",
  busy: "slow-down",
  bad_link: "bad-link",
  bad_document: "bad-document-link",
  move_off: "not-ready",
  not_paid: "no-paid-plan",
  no_email: "no-email",
  too_soon: "too-soon",
  read_too_soon: "read-too-soon",
  hour_full: "hour-full",
  bad_answer: "bad-answer",
  plot_open: "plot-open",
  not_yet: "not-yet",
  changed: "changed",
  bad_hash: "bad-hash",
  ended: "ended",
  owner: "owner-offer",
  bad_server: "bad-server",
  last_account: "last-account",
  not_a_backup: "not-a-backup",
  too_many_parts: "too-many-parts",
  bad_part: "bad-part",
  missing: "files-missing",
  not_receiving: "not-receiving",
  working: "restore-working",
  too_slow: "too-slow",
  shares_rules: "shares-rules",
  not_the_owner: "not-allowed",
  session_running: "session-running",
  name_mismatch: "name-mismatch",
  no_backup_on_this_plan: "no-backup",
  needs_approval: "needs-approval",
  email_failed: "email-failed",
  not_paying: "not-paying",
  one_waiting: "one-waiting",
  daily: "daily-limit",
  not_human: "not-human",
  empty: "ticket-empty",
  too_long: "ticket-too-long",
  subject_too_long: "subject-too-long",
  already_included: "already-included",
  already_subscribed: "already-subscribed",
  not_found: "not-found",
  bad_email: "bad-email",
  in_use: "in-use",
  bad_target: "bad-target",
  // The web's views: a visit the API doesn't know is no more than a failed try, so the page says
  // its generic line rather than a reason (#2405).
  bad_visit: "server",
  peek_off: "peek-off",
  not_in_server: "not-in-server",
  bad_name: "bad-name",
  bad_kind: "bad-kind",
  bad_player: "bad-player",
  unknown_id: "unknown-id",
  lookup_failed: "lookup-failed",
  bot_player: "bot-player",
  exists: "name-exists",
  needs_confirm: "needs-confirm",
  bad_page: "bad-page",
  not_ending: "not-ending",
  use_billing_page: "use-billing-page",
  not_in_plan: "not-in-plan",
  audio_unavailable: "audio-unavailable",
  audio_off: "audio-off",
  bad_choice: "bad-choice",
  too_big: "too-big",
  not_editable: "not-editable",
  bad_speaker: "bad-speaker",
  bad_text: "bad-text",
  fixed_lines: "fixed-lines",
  no_tries: "no-tries",
  sort_failed: "sort-failed",
  bad_type: "bad-type",
  bad_clip: "bad-clip",
  voice_off: "voice-off",
  no_consent: "no-consent",
  not_a_dm: "not-allowed",
  not_on_a_paid_plan: "no-paid-plan",
  too_many_lines: "too-many-lines",
  too_many_speakers: "too-many-speakers",
  try_it_campaign: "try-it-campaign",
  too_many_files: "too-many-files",
  bad_files: "bad-files",
  bad_mapping: "bad-mapping",
  bad_date: "bad-date",
  no_lines_kept: "no-lines-kept",
  not_posted: "not-posted",
  bad_action: "bad-action",
  bad_setting: "bad-setting",
  bad_value: "bad-value",
  nothing_to_delete: "nothing-to-delete",
  plan_holder: "plan-holder",
  too_many: "too-many-masters",
  unknown_person: "unknown-person",
};

/** The recap routes answer 409 `busy` ("being written or sent now"), which the shared code table reads as
 * "slow down" for the sign-in and billing routes; here it means the recap is busy. */
export async function busyAsBusy<T>(run: () => Promise<T>): Promise<T> {
  try {
    return await run();
  } catch (error) {
    if (error instanceof ApiError && error.kind === "slow-down") throw new ApiError("busy");
    throw error;
  }
}

/** An import request's body with its Google Doc links, only when there are any (#2140). */
export function withLinks<T extends object>(
  body: T,
  links: string[] | undefined,
): T & { links?: string[] } {
  return links && links.length > 0 ? { ...body, links } : body;
}

/** What the calls in the on-demand areas share with `httpApi` (#3010). */
export interface ApiHelpers {
  call: (method: "GET" | "POST" | "PUT" | "DELETE", path: string, body?: unknown, asFile?: boolean, raw?: Blob, anonymous?: boolean, kinds?: Record<string, ApiErrorKind>) => Promise<unknown>;
  restoreCall: ApiHelpers["call"];
  waitForAnswer: (path: string, onLook?: (got: WebAnswer) => void, polls?: number) => Promise<WebAnswer>;
  root: string;
  url: (path: string, body?: unknown) => Promise<string>;
}

/** The calls of each area, by name: what `httpApi` stands in for until the area's file is loaded. */
export const ACCOUNT_METHODS = ["emailSignInRequest", "emailSignInUse", "offerHandover", "deleteCampaign", "restoreStage", "restorePart", "restoreGo", "restoreStatus", "restoreCancel", "campaignBackup", "takeBackHandover", "handoverLook", "handoverAccept", "handoverDecline", "removeDiscordAccount", "getMoveCode", "myEmail", "changeEmail", "confirmEmail", "cancelEmailChange", "sendSupport", "mySupport", "supportThread", "signOutEverywhere", "startTryIt", "checkoutUrl", "billingPortalUrl", "renewPlan", "extraHoursUrl", "downloadExport", "characters", "linkServer", "acceptOffer", "declineOffer", "withdrawOffer", "stopRecording", "myLines", "deleteMyLines", "myLinesStatus", "requestDelete", "confirmDelete"] as const satisfies readonly (keyof AccountApi)[];
export const RECAPS_METHODS = ["recapAudioUrl", "recaps", "pastSessions", "sessionReport", "makeSessionReport", "answerReport", "setBeatPresent", "makeRecap", "addRecap", "recapList", "recap", "saveRecap", "approveRecap", "approveUnchecked", "recapAudioLinks", "recapChoices", "setRecapChoice", "askRecapAudioAgain", "plotCheck", "answerPlotIssue", "plotChatSend", "plotChat", "decidePlotProposal", "recapAudioRemake", "sendFix", "fixResult", "sendVoice", "voiceResult", "rewriteWith", "importPreview", "importMembers", "importCheck", "importStart", "importProgress", "shareRecap", "publicRecap", "decideRecap"] as const satisfies readonly (keyof AccountApi)[];
export const NAMES_METHODS = ["namesFixed", "undoNameFix", "campaignNames", "suggestSaidAs", "respellFromVoice", "saveSaidAs", "nameChecks", "answerNameCheck", "npcPage", "saveNpcNote", "myNpcNotes", "deleteMyNpcNotes", "allNames", "smartNames", "changeName", "nameThem", "webGraph", "webSize", "webOverview", "webFind", "webViewers", "confirmKinds", "confirmUndo", "confirmAnswer", "webWaiting", "answerWaiting", "webReadings", "answerReading", "webPeek", "webReveal", "changeWebLink"] as const satisfies readonly (keyof AccountApi)[];
export const CAMPAIGN_METHODS = ["transcriptCampaigns", "transcriptSessions", "transcript", "transcriptFile", "transcriptStretch", "transcriptPutBack", "transcriptLeaveOut", "sessionNotesList", "sessionNotes", "setCampaignSetting", "campaignOptionalRules", "campaignGameMasters", "campaignSheets", "chooseSheet", "sheetAnswer", "fixTranscriptLine", "fixAnswer", "campaignGameMasterCandidates", "campaignSettingAnswer", "timeline", "sharedRules", "houseRuleCampaigns", "houseRules", "ruleSharing", "chooseRuleSharing", "ruleSharingAnswer", "houseRulesFile", "improvement", "setImprovement", "homebrewCampaigns", "homebrew", "homebrewFile", "campaignPage", "loreAdd", "loreRead", "recapStyle", "changeRecapStyle", "recapStyleAction", "houseFile", "houseFileAdd"] as const satisfies readonly (keyof AccountApi)[];

/** A stand-in for each call of an area: the first use loads the area's file (once), and every call goes to it. */
function lazyArea<K extends keyof AccountApi>(names: readonly K[], load: () => Promise<{ default: (h: ApiHelpers) => Record<string, unknown> }>, h: ApiHelpers): Pick<AccountApi, K> {
  let area: Promise<Record<string, unknown>> | null = null;
  return Object.fromEntries(
    names.map((name) => [name, (...args: unknown[]) => (area ??= load().then((m) => m.default(h)))
      .then((a) => (a[name] as (...a: unknown[]) => unknown)(...args))]),
  ) as Pick<AccountApi, K>;
}

/** The real API over HTTP. `base` is the API's address, e.g. "https://api.example". */
export function httpApi(
  base: string,
  fetcher: typeof fetch = fetch,
): AccountApi {
  const root = base.replace(/\/+$/, "");

  async function call(
    method: "GET" | "POST" | "PUT" | "DELETE",
    path: string,
    body?: unknown,
    asFile = false,
    raw?: Blob,
    anonymous = false,
    kinds: Record<string, ApiErrorKind> = errorKinds,
  ): Promise<unknown> {
    let response: Response;
    try {
      // A call for someone who is not signed in (a shared recap's link) sends no cookie and no custom
      // header: it needs neither, and a plain GET stays a simple request.
      const init: RequestInit = anonymous
        ? { method, credentials: "omit", headers: { Accept: "application/json" } }
        : {
            method,
            credentials: "include",
            headers: { "X-DMbot-Request": "1", Accept: "application/json" },
          };
      if (raw !== undefined) {
        // A recorded clip goes as it is, with its own type; the API reads webm and ogg only.
        init.headers = { ...init.headers, "Content-Type": raw.type };
        init.body = raw;
      } else if (body !== undefined) {
        init.headers = { ...init.headers, "Content-Type": "application/json" };
        init.body = JSON.stringify(body);
      }
      response = await fetcher(`${root}${path}`, init);
    } catch {
      throw new ApiError("network");
    }
    if (response.status === 401) {
      // Tell the page's "who is signed in" line, which asked only once when the page opened: a
      // sign-in that ends later (expired, signed out elsewhere) must show its Sign in button (#2301).
      if (!anonymous && typeof window !== "undefined") window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
      throw new ApiError("signed-out");
    }
    if (!response.ok) {
      let code = "";
      let cueProblems: CueProblem[] = [];
      try {
        const body: unknown = await response.json();
        // Most routes answer {"error": code}; the import routes' refusals are {"detail": code}.
        const found = body as { error?: unknown; detail?: unknown };
        code = String(
          found.error ?? (typeof found.detail === "string" ? found.detail : ""),
        );
        cueProblems = readCueProblems(body);
      } catch {
        // No JSON body: a plain server error.
      }
      // A 404 whose code isn't a known one is a route or page the server doesn't have (an older API:
      // FastAPI's own answer is {"detail":"Not Found"}; or the proxy's page): that is "not found", not
      // an outage. A 404 with a known code keeps what the code says (errorKinds is read first).
      const kind =
        kinds[code] ??
        (response.status === 403
          ? "not-allowed"
          : response.status === 404
            ? "not-found"
            : "server");
      // Cue problems are the page's answer, unless the recap can't be changed at all: then the
      // reload that goes with "not-allowed" and "not-editable" matters more.
      if (
        (cueProblems.length > 0 || code === "cue_problems") &&
        kind !== "not-allowed" &&
        kind !== "not-editable"
      ) {
        throw new ApiError("bad-cues", cueProblems);
      }
      throw new ApiError(kind);
    }
    if (response.status === 204) return null;
    return asFile ? response.blob() : response.json();
  }

  // A copy the person may not load (no plan that allows it, or no free campaign slot) reaches the page as its
  // own word: "plan" means something else for the rest of the site, so only the restore calls see this (#2745).
  const restoreCall: typeof call = async (...args) => {
    try {
      return await call(...args);
    } catch (error) {
      if (error instanceof ApiError && error.kind === "plan-needed") throw new ApiError("restore-plan");
      throw error;
    }
  };

  /** Wait for the bot's answer to one queued edit, asking about once a second for up to a minute.
   * A failed look is not an answer (the bot may still apply the change); only "signed out" stops.
   * A change that is not done in time ends as "still-waiting", never as "nothing changed". */
  async function waitForAnswer(path: string, onLook?: (got: WebAnswer) => void, polls = NAME_POLLS): Promise<WebAnswer> {
    for (let i = 0; i < polls; i++) {
      let got: WebAnswer | null = null;
      try {
        got = (await call("GET", path)) as WebAnswer;
        onLook?.(got);
      } catch (error) {
        if (error instanceof ApiError && error.kind === "signed-out") throw error;
      }
      if (got?.state === "done") return got;
      if (got?.state === "failed") throw new ApiError(errorKinds[got.error ?? ""] ?? "server");
      if (i < polls - 1) await new Promise((r) => setTimeout(r, NAME_POLL_MS));
    }
    throw new ApiError("still-waiting");
  }

  const url = async (path: string, body?: unknown): Promise<string> =>
    ((await call("POST", path, body)) as { url: string }).url;

  // The rest of the calls load when a page first uses them (#3010): each area is its own file, fetched on demand,
  // so a page does not carry the calls of the other pages. Every call is still there to ask for (`api.x?.(...)`).
  const helpers: ApiHelpers = { call, restoreCall, waitForAnswer, root, url };
  return {
    async me() {
      try {
        return (await call("GET", "/me")) as Me;
      } catch (error) {
        if (error instanceof ApiError && error.kind === "signed-out")
          return null;
        throw error;
      }
    },
    // Back to the page the person is on after Discord (#2301). Only an /account page is sent: the
    // API checks the same rule again, so this is a courtesy, not the guard.
    signInUrl: () => {
      const here = typeof window === "undefined" ? "" : window.location.pathname;
      return `${root}/auth/discord/start${here.startsWith("/account") ? `?next=${encodeURIComponent(here)}` : ""}`;
    },
    signOut: async () => {
      await call("POST", "/auth/logout");
    },
    addDiscordUrl: () => `${root}/auth/discord/add?next=${encodeURIComponent("/account")}`,
    installUrl: (serverId) =>
      `${root}/install?server_id=${encodeURIComponent(serverId)}`,
    ...lazyArea(ACCOUNT_METHODS, () => import("./api-account"), helpers),
    ...lazyArea(RECAPS_METHODS, () => import("./api-recaps"), helpers),
    ...lazyArea(NAMES_METHODS, () => import("./api-names"), helpers),
    ...lazyArea(CAMPAIGN_METHODS, () => import("./api-campaign"), helpers),
  };
}
