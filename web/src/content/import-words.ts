// The words about importing older sessions (#2107; docs/PLAN.md, "Importing older sessions").
// A DM loads text transcripts of sessions DMbot didn't record, matches each speaker to a server
// member, sets dates and order, and confirms. The importer and its API are #2105 and #2106 and are
// not built yet, so none of these words may show before then: Help would describe a page nobody
// has, and What's new a feature nobody can use.
//
// HOW IT TURNS ON: when the importer is deployed and dev1 has seen a real import, the What's new
// entry "Bring in older sessions" moves out of `held` (whats-new.ts) and IMPORT_LIVE becomes true
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
    text: `To bring in sessions DMbot didn't record, such as a written-out Craig session, open your campaign on the website and press Import sessions (game masters only, on paid plans). Pick up to ${IMPORT_MAX_FILES} text files, each up to ${IMPORT_MAX_MB} MB, and press Preview. For each speaker name, pick the server member it is, or Leave out. DMbot keeps a player's lines only if they have agreed to be recorded in Discord, the same as for a live session. Your word that everyone agreed is not enough. Everyone else's lines are dropped before anything is saved and are never stored, and the page shows how many lines each choice keeps or drops. Then set each file's date and order, check the estimated cost, and press Import. Imported sessions can be read by everyone in your server, like any transcript.`,
  },
  {
    text: "After an import, DMbot goes through the imported sessions in date order, as it does after a live session, and asks you about any new names. Nothing becomes a fact without your say. You can also tick Write recaps for a session; each recap counts against the month's recaps. The files themselves are not kept.",
  },
];
