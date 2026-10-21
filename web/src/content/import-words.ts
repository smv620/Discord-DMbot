// The words about importing older sessions (#2107; docs/PLAN.md, "Importing older sessions").
// A DM loads text transcripts of sessions DMbot didn't record, matches each speaker to a server
// member, sets dates and order, and confirms. The importer and its API are #2105 and #2106 and are
// not built yet, so none of these words may show before then: Help would describe a page nobody
// has, and What's new a feature nobody can use.
//
// HOW IT TURNS ON: when the importer and #2143 (Google Doc links; an older API ignores `links`) are
// deployed and dev1 has seen a real import, the What's new entry "Bring in older sessions" moves out of `held` (whats-new.ts) and IMPORT_LIVE becomes true
// in the same change. A test ties the two together and fails until both are done.
export const IMPORT_LIVE = false;

/** How many files one import takes, and how big each may be (PLAN: "up to 20 files at once, each
 * up to 2 MB of text"). These MIRROR the importer's limits (#2105): change them together, or the
 * page promises a number DMbot does not keep. */
export const IMPORT_MAX_FILES = 20;
export const IMPORT_MAX_MB = 2;

/** The Help step. Written from PLAN; check it against #2105's code before IMPORT_LIVE is true. */
export const importSteps: { text: string }[] = [
  {
    text: `Importing is for game masters on paid plans. To bring in sessions DMbot didn't record, such as a written-out Craig recording (Craig is a Discord recording bot), open your campaign's session transcripts on the website and press Import sessions. Pick up to ${IMPORT_MAX_FILES} text files, each up to ${IMPORT_MAX_MB} MB, and press Preview. You can also paste the link of a Google Doc (Share, then General access, then Anyone with the link, then Viewer); DMbot reads it without signing in to your Google account, only when you press Preview, Check or Import; files and links count together, up to ${IMPORT_MAX_FILES} in all. For each name in a file, pick the server member it is, or Leave out. DMbot suggests a person for a name it has seen in an earlier import or that matches a member's name; you decide. DMbot keeps a player's lines only if they have agreed to be recorded in Discord, the same as for a live session. Your word that everyone agreed is not enough. Everyone else's lines (a person you left out, a person who has not agreed, and bots) are dropped before anything is saved and are never stored, and the page shows how many lines each choice keeps or drops. Then set each file's date and order, check how much work it will take, and press Import. Imported sessions can be read by everyone in your server, like any transcript.`,
  },
  {
    text: "After an import, DMbot goes through the imported sessions in date order, as it does after a live session, and asks you about any new names. Nothing becomes a fact without your say. Recaps are optional. For each session you tick under Recaps, DMbot writes one after you've answered the questions about names, like after a live session, and sends you one message in Discord when they start. The files themselves are not kept after the import.",
  },
];

/** The words on the Import sessions page (#2107). The page shows what the API says; these are the
 * plain words around it. Consent is said where the choice is made, not only in Help. */
export const importPage = {
  heading: "Import sessions",
  back: "Back to the sessions",
  lead: `Bring in sessions DMbot didn't record, from text files or Google Docs. DMbot keeps only the lines of players who have agreed to be recorded. Imported sessions can be read by everyone in your server, like any transcript.`,
  loading: "Loading…",
  signedOut: "Sign in to import sessions. You will need to choose your files again.",
  signIn: "Sign in with Discord",
  pickLabel: `Choose up to ${IMPORT_MAX_FILES} text files (.txt), each up to ${IMPORT_MAX_MB} MB, or paste Google Doc links below`,
  chosen: (n: number): string => `${n} ${n === 1 ? "file" : "files"} chosen`,
  preview: "Preview",
  working: "Working…",
  tooManyFiles: `That is more than ${IMPORT_MAX_FILES} files and links together. Choose fewer.`,
  fileTooBig: (name: string): string => `${name} is over ${IMPORT_MAX_MB} MB. Leave it out or split it.`,
  docNoAnswer: "DMbot gave no answer for one of your Google Docs. Press Preview again.",
  fileNameTaken: (name: string): string => `${name} has a name DMbot uses for Google Docs. Rename the file, then choose it again.`,
  fileNotTxt: (name: string): string => `${name} isn't a .txt file.`,
  fileError: {
    unreadable: "DMbot couldn't read this file.",
    too_big: `This file is over ${IMPORT_MAX_MB} MB.`,
    not_txt: "This isn't a .txt file.",
    not_a_google_doc:
      "This isn't a Google Doc link. Open the doc, press Share, then Copy link, and paste that here. Or remove it.",
    doc_not_shared:
      "DMbot couldn't open this doc. Check the link is right, and that Share → General access is Anyone with the link, Viewer. Then press Preview again.",
    doc_too_big: `This doc is over ${IMPORT_MAX_MB} MB of text. Split it into smaller docs, or paste the part you need into a new one.`,
    doc_unreachable: "DMbot couldn't open this doc just now. Press Preview again in a minute.",
    repeated_link: "You added this doc twice. Remove one of them, then press Preview again.",
    too_many_lines: "This file or doc has too many lines. Split it into smaller ones.",
    too_many_speakers: "This file or doc has too many different names. Check it is one session, or split it.",
    empty: "This file or doc is empty.",
  } as Record<string, string>,
  fileErrorOther: "DMbot couldn't use this file.",
  linkLabel: "Or paste a Google Doc link",
  linkHelp: "Share the doc first:",
  linkSteps: [
    "Open the doc.",
    "Press Share.",
    "Scroll down to General access and choose Anyone with the link.",
    "Set it to Viewer.",
  ],
  linkHelpAfter:
    "DMbot reads the doc without signing in to your Google account, and only when you press Preview, Check or Import. It never changes the doc. Anyone with the link can open it. Keep sharing on until you press Import and it has started, then you can switch it off.",
  linkPlaceholder: "https://docs.google.com/document/d/…",
  notAGoogleDocLink: "That doesn't look like a Google Doc link. It starts with docs.google.com/document.",
  linkAlreadyAdded: "You already added this doc.",
  tooManyToAdd: `That is ${IMPORT_MAX_FILES} already, the most DMbot takes. Remove one first.`,
  added: (name: string, total: number): string => `Added ${name}. ${total} ${total === 1 ? "item" : "items"} chosen.`,
  removed: (name: string, total: number): string => `Removed ${name}. ${total} ${total === 1 ? "item" : "items"} chosen.`,
  /** A link is shown without the ".txt" the API puts on its name. */
  shownName: (name: string): string => name.replace(/^(Google Doc \d+)\.txt$/, "$1"),
  shortLink: (link: string): string => {
    const id = /\/d\/([\w-]+)/.exec(link)?.[1];
    return id ? `docs.google.com/…/d/${id.length > 16 ? `${id.slice(0, 16)}…` : id}` : link;
  },
  addLink: "Add link",
  remove: "Remove",
  removeLink: (n: number): string => `Remove Google Doc ${n}`,
  linkName: (n: number): string => `Google Doc ${n}`,
  chosenItems: (files: number, links: number): string =>
    `${files} ${files === 1 ? "file" : "files"} and ${links} ${links === 1 ? "link" : "links"} chosen`,
  noUsableFiles: "None of these files or links can be used. Fix the notes above, or choose others.",
  matchHeading: "Who is who?",
  matchLead:
    "For each name in your files, pick the server member it is, or Leave out. A player's lines are kept only if they have agreed to be recorded. A player agrees in Discord, the same as for a live session.",
  find: "Find a person",
  findHint: "Type part of a name to see more people.",
  leaveOut: "Leave out",
  notAgreedMark: "hasn't agreed",
  lines: (n: number): string => `${n} ${n === 1 ? "line" : "lines"}`,
  suggestedBecause: (why: string): string =>
    why === "remembered" ? "From an earlier import." : "Matches their name.",
  checkButton: "Check what will be kept",
  checkLead: "This is what would be kept and dropped. Dropped lines are never saved. Nothing is saved yet.",
  kept: (n: number): string => `${n} kept`,
  droppedReason: {
    not_matched: "you left this name out",
    not_agreed: "hasn't agreed to be recorded: their lines are left out",
    bot: "this is a bot",
    left_out: "chat that isn't game talk",
  } as Record<string, string>,
  dropped: (n: number, reason: string): string => `${n} dropped: ${reason}`,
  leftOutFile: (n: number): string => `${n} ${n === 1 ? "line" : "lines"} of chat that isn't game talk ${n === 1 ? "was" : "were"} left out.`,
  checkedSummary: (kept: number, dropped: number): string => `Checked: ${kept} kept, ${dropped} dropped.`,
  datesHeading: "Date and order",
  datesLead: "Set the date of each session. DMbot reads the sessions in the order below, so put the oldest first.",
  datesOutOfOrder: "These dates aren't in order, oldest first. Check them.",
  moved: (name: string, i: number, n: number): string => `${name} moved to ${i} of ${n}.`,
  dateLabel: (name: string): string => `Date of ${name}`,
  dateGuess: "The page guessed this date. Check it.",
  moveUp: (name: string): string => `Move ${name} up`,
  moveDown: (name: string): string => `Move ${name} down`,
  position: (i: number, n: number): string => `${i} of ${n}`,
  confirmHeading: "Ready?",
  // The choice to have DMbot write a recap of an imported session (#2221; #2106 step 2).
  recapsHeading: "Recaps (optional)",
  recapsAll: "Write recaps for all",
  recapsOne: (name: string): string => `Write a recap of ${name}`,
  // Said where the ticks would be when recaps are on for the site but this campaign can't have
  // them (#2229). A reason the DM can't change (the whole server's switch) says nothing.
  recapsWhy: {
    campaign_off: "Recaps are off for this campaign. Turn them on in the campaign's settings to get recaps of imported sessions.",
    plan: "Recaps aren't part of this campaign's plan.",
  } as Record<string, string>,
  // Only after the DM ticked recaps and the setting changed before the start: say it, don't stay silent.
  recapsNotWritten: "No recaps will be written for these sessions.",
  recapsPlans: "See the plans",
  recapsNote:
    "Nothing is written for a session you leave unticked. Recaps wait until you've answered DMbot's questions about names, like after a live session. You'll get one message in Discord when they start. Recaps are not in the estimate above.",
  checkFirst: `Press "Check what will be kept" first, so you see what is dropped before you import.`,
  needDates: "Every session needs a date before you can import.",
  checkAgain: "Check again",
  estimate: (sessions: number, calls: number): string =>
    `DMbot will tidy up and read ${sessions} ${sessions === 1 ? "session" : "sessions"}. This will use about ${calls} quick AI ${calls === 1 ? "request" : "requests"}.`,
  renumberNote: "Sessions after these will be renumbered.",
  confirmNote:
    "Only lines from players who have agreed are kept. Everyone in your server can read the imported sessions. The files themselves are not kept. Nothing becomes a fact without your say, and DMbot will ask you about any new names on your DM screen.",
  importButton: "Import",
  progressHeading: "Importing",
  state: {
    waiting: "Waiting",
    working: "Importing…",
    done: "Done",
    failed: "Couldn't import this one",
  } as Record<string, string>,
  failedReason: {
    names_failed: "The check for new names failed. Import this file again on its own, later.",
    partly_labelled: "Some lines couldn't be sorted, so they count as game talk.",
  } as Record<string, string>,
  doneCount: (done: number, n: number): string => `${done} of ${n} done.`,
  failedFallback: "Something went wrong with this one. Import this file again on its own.",
  allDone: "All done. The sessions are in your transcripts.",
  someFailed: "Some sessions could not be imported. The others are done.",
  seeTranscripts: "Read the transcripts",
  problem: {
    "not-allowed": "Only a game master of this campaign can import sessions. Ask them to do it.",
    "no-paid-plan": "Importing is part of the paid plans.",
    "too-soon": "Wait a minute, then try again.",
    "too-many-lines": "A file has too many lines. Split it into smaller files, then try again.",
    "too-many-speakers": "A file has too many different names. Check it is one session, or split it, then try again.",
    "try-it-campaign": "Importing isn't part of Try It. A paid plan has it.",
    "too-many-files": `That is more than ${IMPORT_MAX_FILES} files and links together. Choose fewer.`,
    "too-big": `One of the files is over ${IMPORT_MAX_MB} MB. Leave it out or split it.`,
    "bad-files": "DMbot couldn't read the files as sent. Choose them again.",
    "bad-files-docs":
      "One of your Google Docs couldn't be opened just now. Check it's still shared as Anyone with the link, then press Preview again.",
    "bad-mapping": "One of the choices of who is who doesn't fit. Check them and try again.",
    "bad-date": "One of the dates isn't a date. Check them and try again.",
    "no-lines-kept":
      "Every line in one of the files would be dropped, so nothing was imported. Match more people, or ask players to agree to be recorded in Discord, then try again.",
    "signed-out": "You were signed out. Sign in again.",
    network: "We couldn't reach DMbot. Check your connection and try again.",
    "not-found": "We can't find that campaign. Go back and open it again.",
    server: "Something went wrong on our side. Try again in a minute.",
  } as Record<string, string>,
} as const;
