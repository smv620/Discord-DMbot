// The switch of the start-up wizard's pages (#3956, part of #3953): a step-by-step way to set up a new campaign on the website.
// In a file of its own so the pages only ask "is it on?". Switched off until the wizard's doors, its finish run (#3955) and the
// ＋ New campaign button's link are deployed and dev1 has checked them; flip it with the What's new entry "Set up a campaign step
// by step" (docs/GO_LIVE.md).
export const WIZARD_LIVE = false;

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
