// The words for "Who was in each scene" under a recap (#2577): for each scene, the player characters DMbot thinks
// were in it, and the DM's change before the recap is approved. DM only (the API sends the scenes to the
// DM alone). Switched off until the save route (#2684) is deployed. The DM's word wins: DMbot never
// changes a list the DM has set. The lists live on this recap's scenes: making the recap again finds the
// scenes afresh and clears them (decided with Supervisor on #2684, 2026-10-27), and the page says so.
export const PRESENT_PER_SCENE_LIVE = false; // moves with the What's new entry "Who was in each scene"

export const presentWords = {
  heading: "Who was in each scene",
  lead: "DMbot guesses who was in each scene. Change any it got wrong. DMbot never changes a list you save. If you make the recap again, your saved lists are deleted.",
  locked: "This recap is approved, so these lists can't be changed.",
  scene: (n: number): string => `Scene ${n}`,
  nobody: "No one listed yet",
  change: "Change",
  changeScene: (n: number): string => `Change who was in scene ${n}`,
  pick: (n: number): string => `Who was in scene ${n}?`,
  none: "Leave all unticked if no one was there.",
  max: "You can pick up to 12 characters. Untick one to add another.",
  save: "Save list",
  saving: "Saving…",
  slow: "Still saving… this is taking longer than usual.",
  updating: "Updating the scenes…",
  cancel: "Cancel",
  // The save is one quick call, not a queued job: it says it is saving, then what it did. It does not say
  // the recap text was rewritten, because it is not.
  saved: (n: number): string => `Saved. Your list for scene ${n} is kept for this recap. The recap text above stays as it is.`,
  errors: {
    stale: "The recap changed while you were editing, so your change was not saved. The scenes below are the latest. Check them and try again.",
    "bad-present": "One name isn't a character in this campaign, so nothing was saved. Untick the one that isn't a character and save again.",
    reread: "Your change was not saved, and the scenes couldn't be read again. Reload the page to see the latest.",
    "not-allowed": "This recap is approved, so its lists are locked. Nothing was saved.",
    "not-found": "That scene is gone, so nothing was saved. The page now shows the latest scenes.",
    "signed-out": "You were signed out, so your change wasn't saved. Sign in again, then try again.",
    network: "Couldn't reach DMbot, so nothing was saved. Check your connection and press Save list again.",
    server: "Something went wrong on our side. Nothing was changed. Try again in a minute.",
  } as Record<string, string>,
};
