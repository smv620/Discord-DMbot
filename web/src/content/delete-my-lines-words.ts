// The words of "Delete my transcripts" on My Account (#2769): Discord's own (core consent_dm.py: the one warning,
// the one confirmation, the done and nothing-to-delete lines), plus the wait for the bot (#2564). Plain words.
// Counts only, never a word of a transcript.
const plural = (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`;

export const deleteMyLinesWords = {
  button: "Delete my transcripts",
  buttonFor: (server: string): string => `Delete my transcripts in ${server}`,
  looking: "Looking…",
  /** The one warning: plain facts, what it covers and that it can't be undone. */
  warning: (server: string, lines: number, sessions: number): string =>
    `This deletes everything you said that DMbot wrote down in ${server}: ${plural(lines, "line", "lines")} in ${plural(sessions, "session", "sessions")}. Other people's lines and the DM's story notes stay. Copies people already downloaded stay too. This can't be undone.`,
  yes: "Yes, delete my lines",
  cancel: "Cancel",
  kept: "OK, nothing was deleted.",
  nothing: (server: string): string => `DMbot has nothing written down from you in ${server}, so there is nothing to delete.`,
  done: (server: string, lines: number): string => `Done. What you said is deleted from ${server}'s transcripts (${plural(lines, "line", "lines")}).`,
  // The wait for the bot's answer (#2564): DMbot's stage, how long, when it last moved.
  stage: {
    waiting: "Waiting for DMbot to pick it up.",
    deleting: "DMbot is deleting your lines.",
    done: "Done.",
    failed: "That didn't work.",
  } as Record<string, string>,
  quiet: "DMbot hasn't answered yet. Your request is waiting and nothing has been deleted so far. You can leave this page and come back later to see where it stands.",
  gaveUp: "DMbot hasn't answered in an hour. Your lines may still be deleted: check back later before you ask again.",
  problem: {
    "not-found": "DMbot can't find that server any more. Reload the page.",
    "nothing-to-delete": "DMbot has nothing written down from you here, so there is nothing to delete.",
    "too-soon": "You asked several times in the last hour. Try again a little later.",
    "signed-out": "You were signed out. Sign in again, then try again.",
    network: "We couldn't reach DMbot, so nothing was deleted. Try again in a moment.",
    server: "Sorry, that didn't work and nothing was deleted. Please try again in a minute.",
  } as Record<string, string>,
  /** What the bot says when it failed: the page's words, never a code. */
  failed: "Sorry, that didn't work and nothing was deleted. Please try again in a minute.",
};
