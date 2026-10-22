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
