// The words of "Fix this line" on a session's Cleaned transcript (#2858; core #2882). Plain words: a player fixes a
// line that is theirs, and the DM decides; a DM's change is made at once and does not change what DMbot heard.
export const fixLineWords = {
  button: "Fix this line",
  buttonFor: (text: string): string => `Fix this line: ${text.slice(0, 40)}${text.length > 40 ? "…" : ""}`,
  label: "What was actually said",
  counter: (n: number): string => `${n} of 200`,
  send: "Send",
  cancel: "Cancel",
  playerNote: "Your DM will see this and decide. Nothing changes until they say yes.",
  dmNote: "This changes the line at once. It does not change what DMbot heard.",
  stage: {
    waiting: "Waiting for DMbot to pick it up.",
    fixing: "DMbot is fixing the line.",
    done: "Done.",
    failed: "That didn't work.",
  },
  quiet: "DMbot is taking a while. You can leave this page; the fix will still go through.",
  sentToDm: "Sent to your DM to check.",
  changed: "Changed.",
  undoNote: " You can undo it from the fix notes in Discord.",
  failures: {
    player_stopped: "This person has stopped being recorded, so their lines can't be changed.",
    secret: "That change can't be made. Try different words.",
    line_changed: "That line was changed by someone else. Reload the page and try again.",
    line_gone: "That line is gone.",
    not_written: "DMbot couldn't save the change. Try again.",
    not_available: "Fixing lines isn't available right now.",
    open: "This line already has a fix waiting for your DM.",
    many: "You already have three fixes waiting for your DM.",
    no_dm_screen: "This campaign has no DM screen yet, so there is nowhere to send it.",
    failed: "That didn't work. Try again. If it fails again, ask on the Help page.",
  } as Record<string, string>,
  refusals: {
    bad_text: "Write between 1 and 200 characters.",
    same: "That is what the line already says.",
    not_yours: "Only the person who said it, or the DM, can fix this line.",
    not_recorded: "This person has stopped being recorded, so their lines can't be changed.",
    not_found: "That line is gone.",
    open: "This line already has a fix waiting for your DM.",
    many: "You already have three fixes waiting for your DM.",
    no_dm_screen: "This campaign has no DM screen yet, so there is nowhere to send it.",
    queue_full: "Too many fixes are waiting in this campaign. Try again in a few minutes.",
    network: "Couldn't reach DMbot. Try again.",
    other: "Couldn't reach DMbot. Try again.",
  } as Record<string, string>,
  /** The line's speaker label is `[0:12:04] (Name) {Character}: words`; the words are what follows the first `: `
   * after the label's closing bracket or brace. Returns "" when the pattern is not there. */
  wordsOf: (line: string): string => {
    const m = /^\[[^\]]*\]\s*(?:\([^)]*\)\s*)?(?:\{[^}]*\}\s*)?:\s?(.*)$/.exec(line);
    return m ? (m[1] ?? "") : "";
  },
};
