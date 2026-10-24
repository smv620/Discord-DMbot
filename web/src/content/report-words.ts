// The words for "After the session: things to check" (#2661; docs/STORY_MEMORY.md 9a): the minor
// contradictions PlotBot found in a session and what the party has wrong, for the campaign's DM only (it is
// full of spoilers; the API refuses a player and the page then shows nothing). Switched off until CloudDev's
// report routes are deployed and seen working. Plain words; DMbot only lists, the DM decides.
export const AFTER_SESSION_REPORT_LIVE = false; // moves with the What's new entry "After the session: things to check"

export const reportWords = {
  heading: "After the session: things to check",
  lead: "DMbot noticed a few things from this session. None of them changed your story: you decide what each one means. Only you can see this; players never do.",
  loading: "Looking for the report…",
  none: "No report for this session yet.",
  make: "Make the report",
  makeAgain: "Try again",
  // The wait (owner rule 2026-10-23): the stage, how far, how long, and when it last moved.
  stages: {
    reading: "Reading the session's notes",
    grading: "Weighing each thing that might not fit",
    sorting: "Sorting what the party has wrong",
  } as Record<string, string>,
  failed: "The report couldn't be made. Nothing was changed. Try again, and if it fails again, tell us on the Say hello page.",
  stoppedWatching: "This is taking longer than usual, so the page stopped checking. Open it again in a little while.",
  empty: "Nothing to check from this session.",
  counts: (c: { contradictions: number; disguised: number; misunderstood: number; false: number; open: number }): string => {
    const parts: string[] = [];
    if (c.contradictions) parts.push(`${c.contradictions} that may not fit`);
    const wrong = c.disguised + c.misunderstood + c.false;
    if (wrong) parts.push(`${wrong} the party has wrong`);
    const left = c.open === 1 ? "1 still waits for you" : `${c.open} still wait for you`;
    return parts.length ? `${parts.join(", ")}. ${left}.` : "";
  },
  contradictionsHeading: "Things that may not fit",
  contradictionsHint: "Two things DMbot holds that seem to clash. Different views disagreeing is not here: that is story.",
  differencesHeading: "What the party has wrong",
  differencesHint: "Where the party or a player character believes something your view says otherwise. Sorted so you can close plot holes before they grow.",
  groups: {
    disguised: "Disguised on purpose",
    misunderstood: "Misunderstood",
    false: "Simply false",
  } as Record<string, string>,
  groupHints: {
    disguised: "A lie, a disguise or a red herring that is working as planned.",
    misunderstood: "They had a true piece and read it wrong.",
    false: "They believe something with nothing behind it.",
  } as Record<string, string>,
  gameTimesUnknown: "The game times aren't known, so these may not clash.",
  first: "One thing DMbot holds",
  second: "Another thing DMbot holds",
  believes: "They believe",
  truth: "Your view says",
  view: (v: string): string => `From: ${v}`,
  fromSession: (n: number | null | undefined): string => (n ? `Session ${n}` : "An earlier session"),
  evidence: "Where it came from",
  // The words are gone (Stop recording me, Delete my transcripts, retention): the session and the kind only.
  evidenceKinds: {
    claim: "a fact",
    line: "a line",
    clue: "a clue",
    event: "an event",
  } as Record<string, string>,
  gone: (kind: string, session: number): string => `${kind} from session ${session}, no longer available`,
  there: (kind: string, session: number, quote: string): string => `${kind} from session ${session}: “${quote}”`,
  reminded: (n: number): string => `You asked to be reminded in session ${n}.`,
  // The four answers. Each is queued for the bot and can be undone.
  actions: {
    intended: "That's intended",
    fix: "Fix the DM's view",
    not_a_problem: "Not a problem",
    remind: "Remind me next session",
  } as Record<string, string>,
  actionLabel: (action: string, what: string): string => `${(reportWords.actions as Record<string, string>)[action] ?? action}: ${what}`,
  answered: (action: string): string =>
    (
      ({
        intended: "Kept as intended. DMbot won't raise it again.",
        fix: "You opened it to fix.",
        not_a_problem: "Marked as not a problem.",
        remind: "DMbot will remind you next session.",
        fixed: "Fixed.",
      }) as Record<string, string>
    )[action] ?? "Answered.",
  undo: "Undo",
  undoLabel: (what: string): string => `Undo the answer: ${what}`,
  openConnection: "Open the connection",
  working: "Saving your answer…",
  done: "Saved.",
  // What went wrong, in plain words and what to do next. An unknown code gets the general sentence.
  errors: {
    gone: "That was already answered or isn't there any more. The list is updated.",
    "cannot-write": "DMbot can't save that right now. It stays on the list. Try again in a minute.",
    "queue-full": "Too many answers are waiting. Give DMbot a moment, then try again.",
    "not-allowed": "Only this campaign's DM can see this report.",
    "not-found": "That report wasn't found.",
    "still-waiting": "DMbot hasn't saved your answer yet. It may still do it. Check again in a minute before you answer twice.",
    network: "Couldn't reach DMbot. Check your connection and try again.",
    server: "Something went wrong on our side. Nothing was changed. Try again in a minute.",
  } as Record<string, string>,
};
