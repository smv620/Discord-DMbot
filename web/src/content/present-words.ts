// The words for "Who was in each scene" under a recap (#2577): for each scene, the player characters DMbot thinks
// were in it, and the DM's change before the recap is approved. DM only (the API sends the scenes to the
// DM alone). Switched off until CloudDev's save route (#2684) is deployed. The DM's word wins: DMbot never
// changes a list the DM has set.
export const PRESENT_PER_SCENE_LIVE = false; // moves with the What's new entry "Who was in each scene"

export const presentWords = {
  heading: "Who was in each scene",
  lead: "DMbot guesses which characters were in each scene. Fix any it got wrong. Your list is final: DMbot never changes it.",
  locked: "This recap is approved, so these lists are locked.",
  scene: (n: number): string => `Scene ${n}`,
  nobody: "No one listed yet",
  change: "Change",
  changeScene: (n: number): string => `Change who was in scene ${n}`,
  pick: (n: number): string => `Who was in scene ${n}?`,
  none: "Leave all unticked if no one was there.",
  max: "12 is the most. Untick one to add another.",
  save: "Save list",
  saving: "Saving…",
  slow: "Still saving… this is taking longer than usual.",
  updating: "Updating the scenes…",
  cancel: "Cancel",
  // The save is one quick call, not a queued job: it says it is saving, then what it did. It does not say
  // the recap text was rewritten, because it is not (CloudDev may change that; see docs/GO_LIVE.md).
  saved: (n: number): string => `Saved. Your list for scene ${n} is kept. The recap text above stays as it is.`,
  // Supervisor (#2577): the saved list is kept for the next rewrite; a DM who wants it now uses the same
  // "Make it again" as the recap maker (recap-make.ts), after one question because it replaces the text.
  againHint: "Want your lists used now?",
  again: "Make it again",
  againSure: "This writes the whole recap again from your lists and replaces the text above, including any edits you made.",
  againYes: "Yes, make it again",
  againNo: "Keep the text as it is",
  againStarted: "DMbot is writing the recap again. This page shows how far it has got.",
  errors: {
    again: "The recap couldn't be started again. Nothing was changed. Try again in a minute, or use Make it again on the recaps page.",
    stale: "The recap changed while you were editing, so your change was not saved. The scenes below are the latest. Check them and try again.",
    "bad-present": "One name isn't a character in this campaign, so nothing was saved. Pick only from the ticked list and save again.",
    reread: "Your change was not saved, and the scenes couldn't be read again. Reload the page to see the latest.",
    "not-allowed": "This recap is approved, so its lists are locked. Nothing was saved.",
    "not-found": "That scene is gone, so nothing was saved. The page now shows the latest scenes.",
    "signed-out": "You were signed out, so your change wasn't saved. Sign in again, then try again.",
    network: "Couldn't reach DMbot, so nothing was saved. Check your connection and press Save list again.",
    server: "Something went wrong on our side. Nothing was changed. Try again in a minute.",
  } as Record<string, string>,
};
