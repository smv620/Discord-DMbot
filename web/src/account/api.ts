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
}

export interface Campaign {
  id: string;
  name: string;
  serverName: string;
  /** ISO date and time of the last session, or null if never played. */
  lastPlayedAt: string | null;
  status: "active" | "paused";
  /** "owner": uses your hours. "co-dm": you help run it. */
  role: "owner" | "co-dm";
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

export interface Me {
  user: Person;
  plan: MyPlan | null;
  /** Missing from an API older than #804: the site can go live first. */
  access?: Access;
  campaigns: Campaign[];
  servers: Server[];
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

/** What the Support form sends (#1340). */
export interface SupportTicket {
  subject: string;
  message: string;
  turnstile: string;
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
  /** Unix seconds. */
  postedAt: number;
  /** Only the kinds that exist; each is a full address that needs no sign-in. */
  audio: { voice?: string; music?: string };
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
function recapFrom(body: unknown): Recap {
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
}

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
  /** "unreadable", "too_big", "not_txt" or "empty"; null when the file can be used. */
  error: string | null;
  suggestedDate: string | null;
  lineCount: number;
  /** Lines of talk that is not the game (Craig's placeholders). */
  leftOut: number;
  speakers: ImportSpeaker[];
}
export interface ImportPreview {
  files: ImportPreviewFile[];
  estimate: { sessions: number; fastCalls: number };
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
  sessions: { file: string; date: string; order: number }[];
}
export type ImportState = "waiting" | "working" | "done" | "failed";
export interface ImportStarted {
  importId: string;
  sessions: { file: string; state: ImportState }[];
  estimate: { sessions: number; fastCalls: number };
}
export interface ImportProgress {
  sessions: {
    file: string;
    date: string;
    state: ImportState;
    reason: string | null;
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

/** Why a call failed, in a form the page can turn into plain words. */
export type ApiErrorKind =
  | "network" // couldn't reach the API
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
  | "not-ready" // the API has no move codes yet (#1231)
  | "no-email" // no payment email is stored yet, so a move code has nowhere to go (#1246)
  | "needs-approval" // "Make the audio again" on a text that is no longer approved (#2090)
  | "too-soon" // the account moved less than 30 days ago (#1246)
  | "email-failed" // the move code could not be emailed just now (#1246)
  | "not-paying" // a support ticket needs an account with a plan, Try It too (#1340, #1435)
  | "one-waiting" // a free account already has a question waiting (#1435)
  | "daily-limit" // a free account sent 3 questions in the last day (#1435)
  | "not-human" // the person check wasn't passed or wasn't there (#1340)
  | "ticket-empty" // the subject or the message was empty (#1340)
  | "ticket-too-long" // the message is over 4,000 letters (#1340)
  | "subject-too-long" // the subject is over 120 letters (#1340)
  | "not-found" // a recap that isn't there, or isn't yours to see (#1386)
  | "no-tries" // no Try again left, so no rewrite (#2114)
  | "sort-failed" // DMbot could not sort the note into points (#2114)
  | "not-editable" // the recap was posted, dropped or is being made, so it can't change (#1386)
  | "bad-text" // the recap text is empty or too long (#1386)
  | "bad-cues" // the recap text has cues that are wrong; `cueProblems` says where (#2084)
  | "already-included" // the plan already has narrated recaps (#1390)
  | "already-subscribed" // the add-on is already bought (#1390)
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
  | "not-posted" // only a posted recap can have a public link (#2118)
  | "bad-action" // not one of on, off or new (#2118)
  | "server"; // anything else

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
  billingPortalUrl(): Promise<string>;
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
  /** Send a private ticket to the DMbot team (paid plans, #1340). Missing from an API older
   * than that. `turnstile` is Cloudflare's token, or "" when the site has no key. */
  sendSupport?(ticket: SupportTicket): Promise<SupportReceipt>;
  /** The DM's recent session recaps, newest first (#1386). Missing from an API older than that. */
  recaps?(): Promise<Recap[]>;
  /** The same list with each campaign's recaps used this month (#1444). The page prefers it;
   * `thisMonth` is empty from an API older than #1428. */
  recapList?(): Promise<{ recaps: Recap[]; thisMonth: RecapMonth[] }>;
  recap?(ref: string): Promise<Recap>;
  /** Save the DM's edit of the recap text and its housekeeping bullets. */
  saveRecap?(
    ref: string,
    summary: string,
    housekeeping: string[],
  ): Promise<Recap>;
  /** Approve the text and pick what to make: voice-over, music, both or text only (#2083). The
   * API checks the cues again and refuses with `bad-cues`; any choice but text queues audio. */
  approveRecap?(ref: string, choice: ApproveChoice): Promise<Recap>;
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
  transcriptFile?(
    campaignRef: string,
    number: number,
    version: TranscriptVersion,
  ): Promise<Blob>;
  /** The campaigns with house rules in the person's servers, for anyone in them (#1521). Missing
   * from an API older than that. */
  houseRuleCampaigns?(): Promise<HouseRuleCampaign[]>;
  /** A campaign's house rules and the sets it follows. `campaignRef` is `<server>.<campaign>`. */
  houseRules?(campaignRef: string): Promise<HouseRules>;
  /** The same rules as the file the bot's Download button sends. */
  houseRulesFile?(campaignRef: string): Promise<Blob>;
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
  /** "Make the audio again": the API allows it once in 10 minutes for a recap. */
  askRecapAudioAgain?(ref: string): Promise<Recap>;
  /** "Tell DMbot what to fix" (#2114): send a note, follow the request, and rewrite with the points
   * the DM kept. Missing from an API older than #2113. */
  sendFix?(ref: string, text: string): Promise<{ requestId: string }>;
  fixResult?(ref: string, requestId: string): Promise<FixResult>;
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
  /** The person's own characters. Missing from an API older than #1164. */
  characters?(): Promise<Character[]>;
  /** Stop recording me in one server (#1151). Starting again stays in Discord. */
  stopRecording(serverId: string): Promise<void>;
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

const errorKinds: Record<string, ApiErrorKind> = {
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
  has_free_access: "free-access",
  slow_down: "slow-down",
  busy: "slow-down",
  bad_link: "bad-link",
  move_off: "not-ready",
  not_paid: "no-paid-plan",
  no_email: "no-email",
  too_soon: "too-soon",
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
  not_in_plan: "not-in-plan",
  bad_choice: "bad-choice",
  too_big: "too-big",
  not_editable: "not-editable",
  bad_text: "bad-text",
  no_tries: "no-tries",
  sort_failed: "sort-failed",
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
};

/** An import request's body with its Google Doc links, only when there are any (#2140). */
function withLinks<T extends object>(
  body: T,
  links: string[] | undefined,
): T & { links?: string[] } {
  return links && links.length > 0 ? { ...body, links } : body;
}

/** The real API over HTTP. `base` is the API's address, e.g. "https://api.example". */
export function httpApi(
  base: string,
  fetcher: typeof fetch = fetch,
): AccountApi {
  const root = base.replace(/\/+$/, "");

  async function call(
    method: "GET" | "POST" | "PUT",
    path: string,
    body?: unknown,
    asFile = false,
    anonymous = false,
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
      if (body !== undefined) {
        init.headers = { ...init.headers, "Content-Type": "application/json" };
        init.body = JSON.stringify(body);
      }
      response = await fetcher(`${root}${path}`, init);
    } catch {
      throw new ApiError("network");
    }
    if (response.status === 401) throw new ApiError("signed-out");
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
      const kind =
        errorKinds[code] ??
        (response.status === 403 ? "not-allowed" : "server");
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

  const url = async (path: string, body?: unknown): Promise<string> =>
    ((await call("POST", path, body)) as { url: string }).url;

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
    signInUrl: () => `${root}/auth/discord/start`,
    signOut: async () => {
      await call("POST", "/auth/logout");
    },
    emailSignInRequest: async (email) => {
      await call("POST", "/auth/email/request", { email });
    },
    emailSignInUse: async (token) => {
      await call("POST", "/auth/email/verify", { token });
    },
    getMoveCode: async () => {
      await call("POST", "/me/move-code");
    },
    sendSupport: async (ticket) => {
      const answer = (await call("POST", "/support", ticket)) as {
        reply?: unknown;
      } | null;
      // Only an explicit "no address" changes the words: an answer without the field is a
      // ticket that was kept, and the team still replies by email.
      return { reply: answer?.reply !== false };
    },
    mySupport: async () => {
      const answer = (await call("GET", "/support/mine")) as {
        tickets?: unknown;
        reply?: unknown;
      } | null;
      // Anything that is not a list of questions is no list: the page shows nothing rather
      // than guess.
      const tickets = Array.isArray(answer?.tickets) ? answer.tickets : [];
      return {
        tickets: tickets.filter(
          (t): t is SupportQuestion =>
            typeof t === "object" &&
            t !== null &&
            typeof t.at === "number" &&
            typeof t.subject === "string" &&
            (t.status === "new" ||
              t.status === "answered" ||
              t.status === "closed"),
        ),
        reply: answer?.reply !== false,
      };
    },
    signOutEverywhere: async () => {
      await call("POST", "/me/sign-out-everywhere");
    },
    startTryIt: async () => {
      await call("POST", "/plan/try-it");
    },
    checkoutUrl: (plan) => url("/billing/checkout", { plan }),
    billingPortalUrl: () => url("/billing/portal"),
    extraHoursUrl: () => url("/billing/extra-hours"),
    recapAudioUrl: () => url("/billing/recap-audio"),
    downloadExport: async () =>
      (await call("GET", "/me/export", undefined, true)) as Blob,
    recaps: async () =>
      ((await call("GET", "/me/recaps")) as { recaps: Recap[] }).recaps,
    recapList: async () => {
      const got = (await call("GET", "/me/recaps")) as {
        recaps: Recap[];
        recapsThisMonth?: RecapMonth[];
      };
      return { recaps: got.recaps, thisMonth: got.recapsThisMonth ?? [] };
    },
    transcriptCampaigns: async () =>
      (
        (await call("GET", "/me/transcripts")) as {
          campaigns: TranscriptCampaign[];
        }
      ).campaigns,
    transcriptSessions: async (campaignRef) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/transcripts`,
      )) as {
        name: string;
        keepDays?: number | null;
        sessions: TranscriptSessionItem[];
      },
    transcript: async (campaignRef, number, version, offset = 0) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/transcripts/${number}?version=${version}&offset=${offset}`,
      )) as TranscriptView,
    transcriptFile: async (campaignRef, number, version) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/transcripts/${number}/download?version=${version}`,
        undefined,
        true,
      )) as Blob,
    houseRuleCampaigns: async () =>
      (
        (await call("GET", "/me/house-rules")) as {
          campaigns: HouseRuleCampaign[];
        }
      ).campaigns,
    houseRules: async (campaignRef) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/house-rules`,
      )) as HouseRules,
    houseRulesFile: async (campaignRef) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/house-rules/download`,
        undefined,
        true,
      )) as Blob,
    improvement: async () =>
      ((await call("GET", "/me/improvement")) as { improve: boolean }).improve,
    setImprovement: async (on) =>
      (
        (await call("PUT", "/me/improvement", { improve: on })) as {
          improve: boolean;
        }
      ).improve,
    homebrewCampaigns: async () =>
      ((await call("GET", "/me/homebrew")) as { campaigns: HomebrewCampaign[] })
        .campaigns,
    homebrew: async (campaignRef) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/homebrew`,
      )) as HomebrewPage,
    homebrewFile: async (campaignRef) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/homebrew/download`,
        undefined,
        true,
      )) as Blob,
    campaignNames: async (campaignRef) =>
      (
        (await call(
          "GET",
          `/me/campaigns/${encodeURIComponent(campaignRef)}/names`,
        )) as { names: CampaignName[] }
      ).names,
    saveSaidAs: async (campaignRef, nameId, saidAs) => {
      await call(
        "PUT",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/names/${encodeURIComponent(nameId)}/said-as`,
        { saidAs },
      );
    },
    recap: async (ref) =>
      recapFrom(await call("GET", `/me/recaps/${encodeURIComponent(ref)}`)),
    saveRecap: async (ref, summary, housekeeping) =>
      recapFrom(
        await call("PUT", `/me/recaps/${encodeURIComponent(ref)}`, {
          summary,
          housekeeping,
        }),
      ),
    approveRecap: async (ref, choice) =>
      recapFrom(
        await call("POST", `/me/recaps/${encodeURIComponent(ref)}/approve`, {
          choice,
        }),
      ),
    recapAudioLinks: async (ref) =>
      (
        (await call("GET", `/me/recaps/${encodeURIComponent(ref)}/audio`)) as {
          links: RecapAudioLinks;
        }
      ).links,
    recapChoices: async (campaignRef) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/recap-choices`,
      )) as RecapChoices,
    setRecapChoice: async (campaignRef, which, on) =>
      (await call(
        "POST",
        `/me/campaigns/${encodeURIComponent(campaignRef)}/recap-choices`,
        {
          which,
          on,
        },
      )) as RecapChoices,
    askRecapAudioAgain: async (ref) =>
      (await call(
        "POST",
        `/me/recaps/${encodeURIComponent(ref)}/audio/again`,
      )) as Recap,
    sendFix: async (ref, text) =>
      (await call(
        "POST",
        `/me/recaps/${encodeURIComponent(ref)}/instructions`,
        { text },
      )) as { requestId: string },
    fixResult: async (ref, requestId) =>
      (await call(
        "GET",
        `/me/recaps/${encodeURIComponent(ref)}/instructions/${encodeURIComponent(requestId)}`,
      )) as FixResult,
    rewriteWith: async (ref, requestId, corrections, style) => {
      const got = (await call(
        "POST",
        `/me/recaps/${encodeURIComponent(ref)}/rewrite`,
        { requestId, corrections, style },
      )) as Rewritten;
      return { ...got, recap: recapFrom(got.recap) };
    },
    // Google Doc links ride along with the files (#2140); the API opens each and names it "Google Doc N.txt".
    importPreview: async (ref, files, links) =>
      (await call(
        "POST",
        `/me/campaigns/${encodeURIComponent(ref)}/imports/preview`,
        withLinks({ files }, links),
      )) as ImportPreview,
    importMembers: async (ref, q) =>
      (
        (await call(
          "GET",
          `/me/campaigns/${encodeURIComponent(ref)}/imports/members?q=${encodeURIComponent(q)}`,
        )) as { members: ImportMember[] }
      ).members,
    importCheck: async (ref, files, mapping, links) =>
      (await call(
        "POST",
        `/me/campaigns/${encodeURIComponent(ref)}/imports/check`,
        withLinks({ files, mapping }, links),
      )) as ImportCheck,
    importStart: async (ref, files, plan, links) =>
      (await call(
        "POST",
        `/me/campaigns/${encodeURIComponent(ref)}/imports`,
        withLinks({ files, plan }, links),
      )) as ImportStarted,
    importProgress: async (ref, importId) =>
      (await call(
        "GET",
        `/me/campaigns/${encodeURIComponent(ref)}/imports/${encodeURIComponent(importId)}`,
      )) as ImportProgress,
    shareRecap: async (ref, action) =>
      (await call("POST", `/me/recaps/${encodeURIComponent(ref)}/share`, {
        action,
      })) as RecapShare,
    publicRecap: async (token) =>
      (await call(
        "GET",
        `/recaps/${encodeURIComponent(token)}`,
        undefined,
        false,
        true,
      )) as PublicRecap,
    decideRecap: async (ref, action) =>
      (await call(
        "POST",
        `/me/recaps/${encodeURIComponent(ref)}/${action}`,
      )) as Recap,
    characters: async () =>
      ((await call("GET", "/me/characters")) as { characters: Character[] })
        .characters,
    installUrl: (serverId) =>
      `${root}/install?server_id=${encodeURIComponent(serverId)}`,
    linkServer: async (serverId) => {
      await call("POST", `/servers/${encodeURIComponent(serverId)}/link`);
    },
    // No handoverCandidates or handover yet: the API has no route to make an offer from
    // the site until #704's follow-up, so the Hand over button stays hidden.
    acceptOffer: async (offerId) => {
      await call("POST", `/offers/${encodeURIComponent(offerId)}/accept`);
    },
    declineOffer: async (offerId) => {
      await call("POST", `/offers/${encodeURIComponent(offerId)}/decline`);
    },
    withdrawOffer: async (offerId) => {
      await call("POST", `/offers/${encodeURIComponent(offerId)}/withdraw`);
    },
    stopRecording: async (serverId) => {
      await call("POST", `/me/recording/${encodeURIComponent(serverId)}/stop`);
    },
    requestDelete: async () =>
      (
        (await call("POST", "/account/delete/request")) as {
          confirm_token: string;
        }
      ).confirm_token,
    confirmDelete: async (token) => {
      await call("POST", "/account/delete/confirm", { confirm_token: token });
    },
  };
}
