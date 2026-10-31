// The words for "After the session: things to check" (#2661; docs/STORY_MEMORY.md 9a): the minor
// contradictions PlotBot found in a session and what the party has wrong, for the campaign's DM only (it is
// full of spoilers; the API refuses a player and the page then shows nothing). Switched off until CloudDev's
// report routes are deployed and seen working. Plain words; DMbot only lists, the DM decides.
export const AFTER_SESSION_REPORT_LIVE = false; // moves with the What's new entry "After the session: things to check"

export const reportWords = {
  heading: "After the session: things to check",
  lead: "DMbot noticed a few things from this session. None of them changed your story, and DMbot may be wrong: you decide what each one means. Only you can see this; players never do.",
  loading: "Looking for the report…",
  none: "No report for this session yet.",
  make: "Make the report",
  makeAgain: "Try again",
  // The last "make it again" did not work, and the report shown is the one made before.
  lastFailed: "The new report couldn't be made, so this is the one from before. Nothing was changed.",
  // The wait (owner rule 2026-10-23): the stage, how far, how long, and when it last moved.
  stages: {
    waiting: "Waiting for DMbot to start",
    reading: "Reading the session's notes",
    grading: "Checking what might not fit",
    sorting: "Sorting what the party has wrong",
  } as Record<string, string>,
  sayHello: "Say hello",
  failed: "The report couldn't be made. Nothing was changed. Try again, and if it fails again, tell us on the Say hello page.",
  stoppedWatching: "This is taking longer than usual, so the page stopped checking. The report is still being made and nothing is lost. Open this page again in a little while.",
  empty: "Nothing to check from this session.",
  leftForNextTime: "Some of this will be checked after the next session.",
  counts: (c: { contradictions: number; disguised: number; misunderstood: number; false: number; open: number }): string => {
    const parts: string[] = [];
    if (c.contradictions) parts.push(`${c.contradictions} that may not fit`);
    const wrong = c.disguised + c.misunderstood + c.false;
    if (wrong) parts.push(`${wrong} where the party sees it differently`);
    const left = c.open === 1 ? "1 left for you to answer" : `${c.open} left for you to answer`;
    return parts.length ? `${parts.join(", ")}. ${left}.` : "";
  },
  contradictionsHeading: "Things that may not fit",
  contradictionsHint: "Two things from your notes that seem to clash. Two characters disagreeing is just story, so it isn't listed.",
  differencesHeading: "Where the party sees it differently",
  differencesHint: "Where the party or a player character believes something your view says otherwise, sorted by how it looks, so you can close plot holes before they grow.",
  groups: {
    disguised: "Looks disguised",
    misunderstood: "Looks misunderstood",
    false: "Nothing found to back it",
  } as Record<string, string>,
  groupHints: {
    disguised: "Looks like a lie, a disguise or a red herring you set up.",
    misunderstood: "They seem to have a true piece and read it another way.",
    false: "DMbot found nothing in your notes that backs it.",
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
    source: "something someone told them",
  } as Record<string, string>,
  // A name, not words: the session only, like a line (#3134).
  sourceHere: (kind: string, session: number): string => `${kind} from session ${session}`,
  // A kind the page does not know yet: said plainly, never by its own raw name (#3134).
  unknownKind: "something DMbot noted",
  // A line is a person's words: only that it exists, never what it said.
  lineHere: (session: number): string => `a line from session ${session}`,
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
  answered: (action: string): string =>
    (
      ({
        intended: "Kept as intended. DMbot won't list it again. You can undo this.",
        fix: "Marked to fix. Nothing has changed yet.",
        not_a_problem: "Marked as not a problem.",
        remind: "DMbot will remind you next session.",
        fixed: "Fixed.",
      }) as Record<string, string>
    )[action] ?? "Answered.",
  undo: "Undo",
  openConnection: "Open the Story Graph to fix it",
  working: "Saving your answer…",
  saved: (left: number): string => (left === 1 ? "Saved. 1 left." : `Saved. ${left} left.`),
  undone: (left: number): string => (left === 1 ? "Answer taken back. 1 left." : `Answer taken back. ${left} left.`),
  // What went wrong, in plain words and what to do next. An unknown code gets the general sentence.
  errors: {
    gone: "That was already answered or isn't there any more. The list now shows the newest.",
    "cannot-write": "DMbot can't save that right now. It stays on the list. Try again in a minute.",
    "queue-full": "Too many answers are waiting. Give DMbot a moment, then try again.",
    "not-allowed": "Only this campaign's DM can see this report.",
    "not-found": "That report wasn't found.",
    "still-waiting": "Not saved yet. Wait a minute, then look at this item before you answer again.",
    network: "Couldn't reach DMbot. Check your connection and try again.",
    server: "Something went wrong on our side. Nothing was changed. Try again in a minute.",
  } as Record<string, string>,
};
