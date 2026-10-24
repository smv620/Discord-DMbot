// The words of the session transcript pages (#1501). The two notes under a transcript are the
// bot's own (core/src/dmbot/transcript/export.py: CLEANED_NOTE, AS_HEARD_NOTE), word for word, so
// the website says what the Discord download says; test/transcripts-words.test.ts checks them
// against that file.
import { transcriptsCard } from "./account-cards";

import type { TranscriptVersion } from "../account/api";

export const cleanedNote =
  "Cleaned: DMbot fixed the spelling of names it was sure about; a few may still be wrong. Chat clearly not about the game is left out, and a short note says how long it was spoken. The \"As heard\" version (/transcript) still has every word. Only people who agreed were recorded.";
export const heardNote =
  "As heard: every word exactly as DMbot heard it, off-topic chat included, before it fixed any names, so some names may be misheard. The \"Cleaned\" version (/transcript) has the names fixed and leaves out clearly off-topic chat. Only people who agreed were recorded.";

export const versionNote: Record<TranscriptVersion, string> = {
  cleaned: cleanedNote,
  heard: heardNote,
};

/** Off-topic chat left out of the cleaned transcript, and the DM's way to put it back (#2568; core #2567). */
export const PUT_BACK_READS_AGAIN = false; // true once a put-back also reaches names and story memory (#2587)
export const offTopic = {
  left: (lines: number, seconds: number): string =>
    `Left out as off-topic: ${lines} ${lines === 1 ? "line" : "lines"} (${seconds >= 60 ? `${Math.round(seconds / 60)} min` : `${seconds} sec`}).`,
  putBackLine: (lines: number): string => `Put back by a DM: ${lines} ${lines === 1 ? "line" : "lines"} now in the cleaned transcript.`,
  show: "Show what was said",
  showFor: (lines: number): string => `Show what was said in the ${lines} left-out ${lines === 1 ? "line" : "lines"}`,
  hide: "Hide",
  loading: "Loading what was said…",
  heading: "As heard",
  who: (when: string, who: string): string => `[${when}] ${who}`,
  putBack: "Put it back",
  putBackFor: (lines: number): string => `Put it back in the cleaned transcript (${lines} ${lines === 1 ? "line" : "lines"})`,
  leaveOut: "Leave it out again",
  leaveOutFor: (lines: number): string => `Leave it out again (${lines} ${lines === 1 ? "line" : "lines"})`,
  explainPutBack: "Put it back adds these lines to the cleaned transcript and its downloads. You can leave it out again.",
  onlyDm: "Only a DM can put this back.",
  reloadButton: "Reload",
  working: "One moment…",
  putBackDone: "Put back. The cleaned transcript now includes these lines.",
  readsAgain: " DMbot will read it for names and story again.",
  leftOutDone: "Left out again. The cleaned transcript no longer includes these lines.",
  nothingChanged: "It was already like that.",
  reload: "Reload the page to see it in place.",
  filter: (kept: number, leftOut: number, putBack: number): string =>
    `${kept} ${kept === 1 ? "line" : "lines"} kept, ${leftOut} left out as off-topic${putBack > 0 ? `, ${putBack} put back` : ""}.`,
  problem: {
    "not-allowed": "Only this campaign's DMs can put chat back. Ask your DM.",
    "not-found": "Those lines aren't there any more. Reload the page.",
    network: "We couldn't reach DMbot, so nothing was changed. Try again in a moment.",
    server: "That didn't work, and nothing was changed. Try again in a moment.",
  } as Record<string, string>,
};

export const text = {
  heading: transcriptsCard.heading,
  link: transcriptsCard.link,
  lead: transcriptsCard.lead,
  loading: "Loading…",
  empty: "No transcripts yet. They appear here after a session that DMbot recorded. Ask your DM to start recording.",
  signedOut: "Sign in to read transcripts.",
  signIn: "Sign in with Discord",
  account: "Back to My Account",
  allCampaigns: "All campaigns",
  campaignLine: (server: string, sessions: number): string =>
    `${server} · ${sessions} ${sessions === 1 ? "session" : "sessions"}`,
  // How long the campaign keeps transcripts (#1575). `undefined` is an older API: say nothing.
  keepLine: (days: number | null | undefined): string | null =>
    days === undefined
      ? null
      : days === null
        ? "Transcripts are kept until the game master deletes them."
        : `Transcripts are kept for ${days === 365 ? "1 year" : `${days} ${days === 1 ? "day" : "days"}`} after each session, then deleted.`,
  keepSoon: "A transcript is deleted within a day of getting that old. To keep a copy, save it before then.",
  allSessions: "All sessions of this campaign",
  open: "Read",
  openCampaign: (name: string): string => `Read: ${name}`,
  sessionTitle: (n: number): string => `Session ${n}`,
  sessionLine: (day: string, lines: number): string =>
    `${day} · ${lines} ${lines === 1 ? "line" : "lines"}`,
  deletedLine: (day: string): string => `Transcript deleted on ${day}`,
  deletedPage: (day: string): string =>
    `This transcript was deleted on ${day}, so there is nothing to read or save. The session is still in the list, with the same number.`,
  versionLabel: "Version",
  versions: { cleaned: "Cleaned", heard: "As heard" } as Record<TranscriptVersion, string>,
  download: "Save as a text file",
  downloading: "Saving…",
  downloadFailed: "That didn't save. Try again.",
  about: "About this version",
  // The bot's notes mention its /transcript command; on a web page say what to use here.
  webNote: "On this page, use the Cleaned and As heard buttons. The /transcript command is for Discord.",
  difference: {
    cleaned: "Names fixed, off-topic chat left out.",
    heard: "Every word, names not fixed.",
  } as Record<TranscriptVersion, string>,
  shareLine: "Anyone in your Discord server can read this. Only people who agreed were recorded.",
  showMore: "Show more",
  loadingMore: "Loading more…",
  moreFailed: "That didn't load. Try again.",
  tooBig: "This transcript is too big to show here. Ask whoever runs DMbot for help.",
  noLines: "Nothing was said in this version.",
  names:
    "DMbot could not find the names of some people, so they are shown as Speaker 1, Speaker 2 and so on, in the order they first spoke in this session.",
  notFound: "We can't find that transcript. Pick it from the list.",
  notInPlan: "Transcripts aren't available for this campaign. Ask the campaign's owner to take a look.",
  failed: "That didn't load. Try again in a moment.",
  tooMany: "You're going a little fast. Wait a moment, then try again.",
};

/** The words for what went wrong; the ones only a person outside the server could see are the same
 * "can't find it", so nobody learns what exists. */
export function problem(kind: string): string {
  if (kind === "not-found") return text.notFound;
  if (kind === "not-in-plan") return text.notInPlan;
  if (kind === "too-big") return text.tooBig;
  if (kind === "slow-down") return text.tooMany;
  return text.failed;
}
