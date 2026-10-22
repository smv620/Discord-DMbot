/**
 * Every word on the DM's recap pages (#1386): the list at /account/recaps and the review page
 * at /account/recaps/<id>. Plain words; every message says what to do next. Nothing here
 * promises more than the API does: Post only asks DMbot, which checks once more and sends it.
 */
import type { ApiErrorKind, CueProblem, Recap, RecapMonth } from "../account/api";
import { shareWords } from "./recap-share";
import { RECAP_SHARE_LIVE, RECAPS_LIVE } from "./recap-words";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** "1 November": the day the allowance starts again, counted in UTC like the bot's own words
 * (recaps/allowance.py), so the page and the bot never name different days. */
export function nextMonthStart(now: Date): string {
  return `1 ${MONTHS[(now.getUTCMonth() + 1) % 12]}`;
}

/** The quiet lines under the list (#1444): how much of this month's allowance each campaign has
 * used. Nothing for a campaign with no recaps this month; narration only while its switch is on. */
export function allowanceLines(
  months: RecapMonth[] | undefined,
  narration: boolean,
  now: Date,
): string[] {
  const lines: string[] = [];
  const next = nextMonthStart(now);
  for (const m of months ?? []) {
    const name = m.campaign || "Your campaign";
    const quiet: string[] = [];
    const used: string[] = [];
    if (m.allowed > 0 && m.used >= m.allowed)
      used.push(`${name} has used all ${m.allowed} recaps for this month. More come on ${next}.`);
    else if (m.used > 0) quiet.push(`${m.used} of ${m.allowed} recaps used this month`);
    if (narration) {
      if (m.audioAllowed > 0 && m.audioUsed >= m.audioAllowed)
        used.push(
          `${name} has used all ${m.audioAllowed} narrated recaps for this month. More come on ${next}.`,
        );
      else if (m.audioUsed > 0) quiet.push(`${m.audioUsed} of ${m.audioAllowed} narrated`);
    }
    if (quiet.length > 0) lines.push(`${name}: ${quiet.join(", ")}`);
    lines.push(...used);
  }
  return lines;
}

export const text = {
  heading: "Your recaps",
  lead: "After a session, DMbot can write a short recap for your table (paid plans). Only you can see it until you post it.",
  loading: "Loading your recaps…",
  empty: "No recaps yet. DMbot tells you when one is ready.",
  // While recaps are off (RECAPS_LIVE false) the page promises nothing: no email, no recap to come.
  emptyOff: "No recaps yet.",
  back: "All your recaps",
  linkToList: "Read your session recaps",
  account: "Back to My Account",
  open: "Open",
  signedOut: "Sign in to read your recaps.",
  signIn: "Sign in with Discord",
  untitled: "Session recap",
  notFound:
    "We can't find that recap. Open it again from your email, or pick it from your list.",
  onlyYou: "Only you can see this until you post it.",
  working: "DMbot is still writing this recap. Check back in a few minutes.",
  // Written, but the checks that read it before the DM does can't run yet (#2297). One steady line:
  // it stays up through the short gaps between tries, until the recap is ready or has failed.
  waitingForCheck:
    "📜 Your recap is written and waiting for one last check. DMbot will finish it as soon as it can.",
  // A draft DMbot couldn't check for 3 days (#2297): the DM reads it before it goes anywhere.
  uncheckedHeading: "Not checked yet: read it before you share it",
  uncheckedLead:
    "DMbot wrote this recap but couldn't finish checking it. Nothing has been shared. The sentences marked below are the ones DMbot would have looked at twice.",
  uncheckedMark: "Look at this one",
  uncheckedUse: "Use this draft as it is",
  uncheckedUsing: "Opening it…",
  uncheckedAfter: "After this you can still change it and then approve it, as with any recap.",
  uncheckedFailed: "That didn't work. Try again in a minute.",
  checksHeading: "What the checks said",
  checksNote: "DMbot wasn't sure about these. Only you can see them. You decide what stays.",
  failed: "DMbot couldn't write this recap. Press Try again, or leave it.",
  failedNoTries:
    "DMbot couldn't write this recap, and there are no more tries. You can leave it unposted.",
  textHeading: "Recap",
  // The session's transcript was deleted by the table's "Keep transcripts for" setting (#2022).
  // The list says it short; the recap's own page adds why (`keepDays`), as one sentence.
  transcriptDeleted: (day: string, keepDays?: number | null): string =>
    keepDays
      ? `Transcript deleted on ${day}, as your table keeps transcripts for ${keepDays === 365 ? "1 year" : `${keepDays} ${keepDays === 1 ? "day" : "days"}`}. The recap is kept.`
      : `Transcript deleted on ${day}. The recap is kept.`,
  // A name's fixed spelling changed this recap's text after it was posted (#2170, #2178): the
  // message in Discord is not changed, so the DM is told. Only on a posted recap.
  respelledHeading: "Spelling updated",
  respelled: (day: string): string =>
    `A name's spelling was fixed in this recap on ${day}. The text on this page has the new spelling. The message already posted in Discord still has the old one.`,
  housekeepingHeading: "Housekeeping",
  housekeepingNone: "Nothing to sort out.",
  toConfirmHeading: "Facts to be confirmed",
  toConfirmNote:
    "These names were said at your table, and DMbot doesn't know them yet. Say which are in your game with Check new names on your DM screen in Discord. Only you can see this list. DMbot never posts it.",
  edit: "Edit",
  editLabel: "Recap",
  housekeepingLabel: "Housekeeping (one line for each)",
  save: "Save changes",
  saving: "Saving…",
  cancel: "Cancel",
  saved: "Saved.",
  post: "Post to the table",
  again: "Try again",
  busy: "Working…",
  noTriesLeft:
    "You have tried twice. You can still edit it or approve it.",
  posting:
    "Sent. DMbot will check it once more, then post it. Check back here to see if it posted.",
  posted: "This recap was posted to your table.",
  dropped:
    "You chose not to post this recap. It stays here, and nobody else sees it.",
  beforeChoosing:
    "DMbot only suggests this recap. You decide what your players see.",
  againHint: "Try again writes a new recap and replaces your changes.",
  // The email promise lives in the line under it, which names the address (#2335).
  tryingAgain: "DMbot is making it again.",
} as const;

/** What a held recap says instead of its draft (#2210): it waits for the DM's answers about
 * names, and where to give them. */
export const heldWords = {
  status: "Waiting for your answers about names",
  heading: "Waiting for your answers",
  line: (left: number | undefined): string =>
    `DMbot will write this recap once you answer ${
      left && left > 0
        ? `the ${left} ${left === 1 ? "question" : "questions"}`
        : "its questions"
    } about names in Discord: 📝 Check new names on the DM screen.`,
} as const;

/** A recap the bot is holding until the DM answers its questions about names (#2210). The API
 * sends a held recap as its own entry (state "waiting", #2174) and clears the flag once the real
 * recap exists, so the flag alone says it. One test for the list and the page, so they never
 * disagree. */
export function isHeld(recap: Recap): boolean {
  return recap.waitingForNames === true;
}

/** "Ready to read", "Posted"… for one row of the list. */
/** `stillWaiting`: the page has seen this recap wait for its check and keeps saying so through the short
 * gaps where the API says something else (#2297), so the tag and the line below it agree. */
export function statusLine(recap: Recap, stillWaiting = false): string {
  if (recap.status === "posted" && recap.shared && RECAPS_LIVE && RECAP_SHARE_LIVE) return shareWords.listTag;
  if (recap.status === "posted") return "Posted";
  if (recap.status === "posting") return "Posting";
  if (recap.status === "dropped") return "Not posted";
  if (isHeld(recap)) return heldWords.status;
  if (recap.state === "failed" && !recap.working) return "Couldn't be made";
  if (recap.working && (recap.waitingForCheck || stillWaiting)) return "Waiting for a last check";
  if (recap.working) return "Being made";
  return "Ready to read";
}

/** "Try again (2 left)". The button is greyed at none. */
export function againLabel(left: number): string {
  return left > 0 ? `${text.again} (${left} left)` : text.again;
}

export function sessionDay(sessionAt: number, locale?: string): string {
  return new Date(sessionAt * 1000).toLocaleDateString(locale, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

export const errors: Partial<Record<ApiErrorKind, string>> & { other: string } =
  {
    "signed-out":
      "You were signed out. Sign in again, then open the recap again.",
    network: "We couldn't reach DMbot. Check your connection and try again.",
    "not-found": text.notFound,
    "not-allowed":
      "That isn't possible any more. Reload the page to see where this recap is now.",
    "not-editable":
      "This recap can't be changed any more. Reload the page to see where it is now.",
    "bad-text": "The recap is empty or too long. Shorten it and save again.",
    "bad-cues": "Nothing was saved. Some cues need fixing. See the list under the text box.",
    other: "Something went wrong on our side. Try again in a minute.",
  };

// The cue checks on save (#2084). The API says where each problem is and what kind it is; these
// are the words for it. A cue is the 《mood》 or ((pause)) marker in the text, so each line says
// "cue", never "tag" or "syntax", and each says what to do next. The kinds and the paragraph
// counting (a blank line starts a new paragraph) are #2083's, from core/src/dmbot/recaps/cues.py;
// "no_words" there is a mood cue with no story after it before the next mood.
export const cueWords = {
  help: "Cue words",
  helpLead:
    "A mood cue looks like 《calm》. A pause or pace cue looks like ((long pause)). Only the words below are cue words.",
  moods: "Moods",
  synonyms: "Other words that work for a mood",
  pacing: "Pauses and pace",
  /** What a paragraph is, since the box shows no numbers. */
  paragraphs: "Paragraphs are counted from the top; a blank line starts a new one.",
  /** Nothing was saved, and the edit is still in the box. */
  noteEmpty:
    "Nothing was saved. A cue in the text is wrong. Check each 《 》 and (( )), then save again.",
  maxShown: 5,
} as const;

/** The heading over the list: how many, and what happens next. After a refused save the edit is
 * still in the box; after a refused approval the DM goes to Edit first. */
export function cueProblemsHeading(count: number, action: "save" | "approve" = "save"): string {
  const these = count === 1 ? "this cue" : `these ${count} cues`;
  return action === "approve"
    ? `Nothing was approved. Press ${text.edit}, fix ${these}, save, then approve again.`
    : `Nothing was saved, and your edits are still in the box. Fix ${these}, then press ${text.save}.`;
}

/** "And 2 more. Fix these and save to see the rest." for a list cut at `maxShown`. */
export function cueProblemsMore(hidden: number): string {
  return `And ${hidden} more. Fix these and save to see the rest.`;
}

/** One cue problem as a plain sentence: where it is, what is wrong, what to do. `help` is true
 * when the "Cue words" panel is on the page, so a line only points at it when it is there. */
export function cueProblemLine(problem: CueProblem, help = true): string {
  const where = `Near “${problem.near}” (paragraph ${problem.paragraph})`;
  const cue = `“${problem.cue}”`;
  const pick = help ? " Pick one from Cue words." : "";
  const said: Record<string, string> = {
    unknown: problem.suggest
      ? `${cue} is not a cue word. Change it to “${problem.suggest}”.${pick}`
      : `${cue} is not a cue word.${pick}`,
    unclosed: `${cue} has no ending. Add the closing mark after it.`,
    mismatched: `${cue} starts with one mark and ends with another. Use 《 》 at both ends, or (( )) at both ends.`,
    empty: `${cue} has nothing inside. Type a word between the marks, or delete it.`,
    no_words: `${cue} has no story after it before the next mood. Put the words it goes with after it, or delete it.`,
    inside_word: `${cue} is inside a word. Move it to a space between words.`,
  };
  return `${where}: ${said[problem.kind] ?? `${cue} is wrong. Check it against the cue words.`}`;
}
