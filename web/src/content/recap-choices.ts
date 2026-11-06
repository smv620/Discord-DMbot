// The recap page's "Your recap choices" part (#1468): the same three choices as the DM screen's
// 📜 Recaps panel (core/src/dmbot/dm_screen/recap_panel.py), in the panel's own words. The labels
// and the "Saved." lines below are copied from that file, and a test (recap-choices.test.tsx)
// checks each one is still in it, so the site and the panel cannot drift apart.
export const choiceWords = {
  heading: "Your recap choices",
  /** The panel's LABELS. */
  labels: {
    text: "Write a recap after each session",
    audio: "Make the narration and music",
    email: "Email me when it is ready",
  },
  /** The panel's SAVED lines, said under the switches after a change. */
  saved: {
    text: { on: "Saved. A recap after each session.", off: "Saved. No recap after each session." },
    audio: { on: "Saved. Narration and music on.", off: "Saved. No narration and music." },
    email: { on: "Saved. You'll get an email.", off: "Saved. No email." },
  },
  /** The panel's line for a DM with the email off. */
  emailOff: "The recap will be on your recap page.",
  /** Where the same choices are on Discord. */
  sameOnDiscord: "The same choices are in Discord: 👤 DMbot Main Menu → ⚙️ Settings, in your DM Bot channel.",
  // The page's own: what to do when it didn't work.
  failed: "That didn't save. Try again.",
  notInPlan: "Narration and music aren't in your plan.",
  signedOut: "You were signed out. Sign in, then make the change again.",
  gone: "We can't find that campaign. Reload the page, or pick it again from your recaps.",
  loadFailed: "Your choices didn't load. Reload the page.",
  saving: "Saving…",
  loading: "Loading your choices…",
} as const;
