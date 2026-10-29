// The words for making, remaking and adding a recap for a past session (#2610; core #2608, #2609). The
// switch is off until the core doors are deployed and seen working: nothing here shows before then.
// Only the DM sees any of it (the API answers a player "not allowed" and the page then shows nothing).
// Plain words; DMbot writes only from what was said by people who agreed to be recorded.
export const RECAP_MAKE_LIVE = false; // moves with the What's new entry "Make a recap for a past session"

export const makeWords = {
  heading: "Recaps for past sessions",
  lead: "Make a recap from what was said at the table, make it again, or add one that someone wrote. DMbot writes only what was said, and you decide what to post. Making it again writes a new recap in place of the old one.",
  loading: "Looking at your past sessions…",
  none: "No past sessions with a transcript yet. Recaps are made from the session transcript.",
  session: (number: number | null | undefined, day: string, length: string): string =>
    [number ? `Session ${number}` : "Session", day, length].filter(Boolean).join(" · "),
  state: {
    none: "No recap yet",
    working: "Being made",
    ready: "Ready to read",
    failed: "Couldn't be made",
    held: "Held back for your check",
    approved: "Approved by you",
    posted: "Posted to players",
  } as Record<string, string>,
  make: "Make the recap",
  again: "Make it again",
  add: "Add a recap someone wrote",
  open: "Open the recap",
  close: "Close this",
  // One question when a session already has a recap (the API's `needs_confirm`). An approved or posted recap
  // is kept until the new one is approved, so the words say that only for those.
  askAgain: (kept: boolean): string =>
    `This session already has a recap. Make a new one?${kept ? " The one you have stays until you approve the new one." : ""}`,
  // Shown under that question (also when an uploaded recap would replace it) when a scene of the recap has a
  // "who was there" list the DM saved (#2684): a rewrite or a replacement finds the scenes afresh, so the lists go. `maybe` when the recap could not be read to check.
  askAgainLists: 'Your saved lists for "Who was in each scene" are deleted right away. The new recap gets new scenes.',
  askAgainMaybeLists: 'Any saved lists for "Who was in each scene" are deleted right away. The new recap gets new scenes.',
  checkingLists: "Checking your saved lists…",
  // A recap that failed has nothing to keep (#2764), but its saved "who was there" lists would still go.
  askAgainUnfinished: "Make this recap again?",
  askReplaceUnfinished: "Add this one in place of the recap that didn't finish?",
  askReplaceDraft: "This session already has a recap. Add this one in its place? The recap there isn't approved yet, so it is replaced.",
  askReplaceExisting: "This session already has a recap. Add this one in its place? The old one is kept as an earlier version.",
  yesAgain: "Yes, make a new one",
  yesReplace: "Yes, add it",
  noTranscript: "No transcript is kept for this session. You can add a recap someone wrote.",
  keep: "No, keep it",
  form: {
    heading: "Add a recap someone wrote",
    how: "How do you want to add it?",
    paste: "Paste it here",
    file: "From a text file",
    link: "From a Google Doc link",
    text: "Paste the recap",
    fileHelp: "A plain text file (.txt or .md), up to 1 MB.",
    link_: "Link to the Google Doc",
    linkHelp: "In Google Docs, choose Share, then General access, then Anyone with the link.",
    who: "Who wrote it (your name is fine)",
    whoHelp: "The name, as you want it shown.",
    title: "Title (optional)",
    right: "I have the right to use this text.",
    rightNote: "DMbot doesn't check this. It asks only once, and keeps your name and the date. It goes into your recap.",
    submit: "Add this recap",
    cancel: "Cancel",
    working: "Adding…",
  },
  // "Written by Maren, added 12 Oct 2026." (the API gives who wrote it and when a game master added it)
  uploaded: (writer: string, day: string | null): string => `Written by ${writer}${day ? `, added ${day}` : ""}.`,
  // The recap's own state while it is made (the bot's steps), in plain words. Any other word gets the
  // general "DMbot is working on it".
  stage: {
    queued: "Waiting for its turn",
    beats: "Working out what happened",
    checked: "Checking it against what was said",
    prose: "Writing the recap",
    housekeeping: "Tidying up",
    reading: "Reading what was said",
    writing: "Writing the recap",
    checking: "Checking the recap",
    fetching: "Reading your Google Doc",
    saving: "Saving the recap",
    // What the API sends while a recap is made on the website (core `recaps/progress.py`, #2636): every
    // code starts "recap_". The unprefixed words above are the older names and stay as they were.
    recap_names: "Finding the names in what was said",
    recap_reading: "Reading what was said",
    recap_beats: "Working out what happened",
    recap_writing: "Writing the recap",
    recap_checking: "Checking the recap",
    recap_check_waiting: "Written, waiting to be checked",
    recap_final_read: "Giving it a last read",
    recap_finishing: "Tidying up",
    recap_ready: "Ready to read",
    recap_failed: "Couldn't be made",
  } as Record<string, string>,
  fileTooBig: "That file is over 1 MB. Choose a smaller one.",
  doneNote: "The recap is ready.",
  failedReason: {
    error: "Something went wrong. Choose Make it again.",
    recap_failed: "DMbot couldn't write a recap from this session. Choose Make it again, or add one someone wrote.",
    stalled: "This has stopped making progress. Choose Make it again.",
  } as Record<string, string>,
  stoppedWatching: "DMbot stopped checking after a long time with nothing new. Reload this page to look again.",
  errors: {
    "not-allowed": "Only this campaign's DMs can do this.",
    "no-lines": "That session's transcript isn't kept any more, so DMbot has nothing to write from. You can add a recap someone wrote instead.",
    "busy": "A recap for that session is being written or sent right now. Wait for it to finish.",
    "not-in-plan": "Your plan doesn't include this. You can change your plan on My Account.",
    "too-soon": "Please wait a minute, then try again.",
    "bad-text": "The recap is empty or can't be used. Paste or choose some text and try again.",
    "too-big": "That recap is too long. Cut it down and try again.",
    "not-a-google-doc": "That doesn't look like a Google Doc link. Copy it from your browser's address bar and try again.",
    "doc-not-shared": "DMbot can't read that document. In Google Docs choose Share, then General access, then Anyone with the link, and try again.",
    "doc-unreachable": "DMbot couldn't open that document just now. Try again in a moment, or paste the text instead.",
    "doc-too-big": "That document is too long. Paste a shorter part of it instead.",
    "needs-rights": "Tick the box to say you have the right to use this text.",
    network: "We couldn't reach DMbot, so nothing was changed. Try again in a moment.",
    server: "That didn't work, and nothing was changed. Try again in a moment.",
  } as Record<string, string>,
};
