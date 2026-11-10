// The switch of the start-up wizard's pages (#3956, part of #3953): a step-by-step way to set up a new campaign on the website.
// In a file of its own so the pages only ask "is it on?". Switched off until the wizard's doors, its finish run (#3955) and the
// ＋ New campaign button's link are deployed and dev1 has checked them; flip it with the What's new entry "Set up a campaign step
// by step" (docs/GO_LIVE.md).
export const WIZARD_LIVE = false;

/** The one address the wizard pages are on at before WIZARD_LIVE: the development site, so the owner can try the real wizard on the
 * test server (the API only lets this address talk to it, docs/DEPLOY.md). An exact host, never a pattern: getdmbot.com, a pull
 * request preview and localhost stay off. */
export const WIZARD_TEST_HOST = "dev.getdmbot.com";

/** Whether the wizard pages are on for a page served from this host (`window.location.hostname`). */
export function wizardOn(host: string): boolean {
  return WIZARD_LIVE || host === WIZARD_TEST_HOST;
}

// What the first release leaves out (docs/PLAN.md and #3953: a DM is never asked to upload something that is then not used). Each
// comes back with the piece that uses it; flip it with that piece's go-live row (docs/GO_LIVE.md), in this order:
/** Each character's public sheet link in step 1 (needs the bot's sheet fetch queue). */
export const WIZARD_SHEETS_LIVE = false;
/** House rules and homebrew files and links in steps 7 and 8 (need their existing imports run at Finish). */
export const WIZARD_HOUSE_FILES_LIVE = false;
/** The DM's own ruleset file in step 6. */
export const WIZARD_OWN_RULESET_LIVE = false;
/** The Google Drive choice in step 1 and its button in the file steps (needs Google's file picker). */
export const WIZARD_DRIVE_LIVE = false;
/** A link as a document in the file steps (the server reads a file or text, not a link; links come with the imports of #3953's follow-ups). */
export const WIZARD_LINKS_LIVE = false;
