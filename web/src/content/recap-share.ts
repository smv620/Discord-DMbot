// The words for sharing a recap by a link (#2119; core #2118; docs/PLAN.md, "Recaps on the website").
// A game master turns a link on for a posted recap; anyone with it reads the recap (and plays its
// voice and music) without signing in. The link is a secret in the address, so the words say to
// treat it like a password, as the privacy page does. Shown only while RECAP_SHARE_LIVE is true.
import { RECAP_AUDIO_LIVE } from "./recap-words";

export const shareWords = {
  heading: "Share this recap",
  /** Said while the link is off. */
  offLead:
    "Make a link to share this recap. Anyone who has the link can read it without signing in, even people outside your Discord server, and they can pass it on.",
  /** Said while the link is on. */
  onLead: (audio: boolean): string =>
    `Anyone who has this link can read this recap${audio ? " and play its voice and music" : ""} without signing in, even people outside your Discord server, and they can pass it on. Treat the link like a password.`,
  make: "Make a link",
  linkLabel: "The link to share",
  copy: "Copy",
  copied: "Copied",
  copyFailed: "Select the link and copy it yourself.",
  off: "Turn the link off",
  fresh: "Make a new link",
  freshHint: "The old link stops working. Anyone who already read the recap may keep a copy.",
  working: "Working…",
  doneOff: "The link is off. It no longer opens the recap.",
  doneFresh: "Here is your new link. The old one no longer opens the recap.",
  doneOn: "Here is your link.",
  notPosted: "Post the recap to the table first. Only a posted recap can be shared.",
  problem: {
    "not-found": "We can't find that recap. Reload the page.",
    "not-posted": "Post the recap to the table first. Only a posted recap can be shared.",
    "signed-out": "You were signed out. Sign in again.",
    network: "We couldn't reach DMbot. Check your connection and try again.",
    server: "Something went wrong on our side. Try again in a minute.",
  } as Record<string, string>,
  /** The state in the recap list. */
  listTag: "Shared",
  audio: RECAP_AUDIO_LIVE,
} as const;

/** The public page at /r/<token>. One message for every kind of dead link, on purpose: the API
 * answers the same for a wrong, never-made, turned-off or no-longer-passing link. */
export const publicWords = {
  loading: "Loading…",
  dead: "This recap link isn't active. Ask your DM for a new one.",
  busy: "Too many tries just now. Wait a minute and open the link again.",
  posted: (day: string): string => `Posted ${day}`,
  tableNote: "A recap from a game of DMbot's table. Written from what the table said; read by whoever has the link.",
  voice: "Voice",
  music: "Music",
} as const;
