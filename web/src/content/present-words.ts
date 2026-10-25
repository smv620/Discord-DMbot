// The words for "Who was there" under a recap (#2577): for each scene, the player characters DMbot thinks
// were in it, and the DM's change before the recap is approved. DM only (the API sends the scenes to the
// DM alone). Switched off until CloudDev's save route (#2684) is deployed. The DM's word wins: DMbot never
// changes a list the DM has set.
export const PRESENT_PER_SCENE_LIVE = false; // moves with the What's new entry "Who was in each scene"

export const presentWords = {
  heading: "Who was in each scene",
  lead: "DMbot lists the characters who seem to be in each scene. If it got one wrong, change it. Your list wins: DMbot won't change it.",
  scene: (n: number): string => `Scene ${n}`,
  present: "Present",
  nobody: "No one listed",
  change: "Change",
  changeScene: (n: number): string => `Change who was in scene ${n}`,
  pick: (n: number): string => `Who was in scene ${n}?`,
  max: "You can pick up to 12.",
  save: "Save",
  saving: "Saving…",
  cancel: "Cancel",
  // The save is one quick call, not a queued job: it says it is saving, then what it did. The words are
  // careful about the text above, which this does not rewrite.
  saved: "Saved. DMbot will use this when it writes the recap again. The text above isn't changed.",
  errors: {
    stale: "The recap changed since this page looked. The scenes are updated below. Check them and change again if you need to.",
    "bad-present": "One of those isn't a character in this campaign. Pick from the list.",
    "not-allowed": "This recap can't be changed any more.",
    "not-found": "That scene wasn't found. The page will show the newest.",
    "signed-out": "You were signed out. Sign in again, then try again.",
    network: "Couldn't reach DMbot. Check your connection and try again.",
    server: "Something went wrong on our side. Nothing was changed. Try again in a minute.",
  } as Record<string, string>,
};
