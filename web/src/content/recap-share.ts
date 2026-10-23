// The words for sharing a recap by a link (#2119; core #2118; docs/PLAN.md, "Recaps on the website").
// A DM turns a link on for a posted recap (or one they approved); anyone with it reads the recap (and plays its
// voice and music) without signing in. The link is a secret in the address, so the words say to
// treat it like a password, as the privacy page does. Shown only while RECAP_SHARE_LIVE is true.
import { RECAP_AUDIO_LIVE } from "./recap-words";

export const shareWords = {
  heading: "Share this recap",
  /** Said while the link is off. */
  offLead: (audio: boolean): string =>
    `Nothing is shared until you make a link. Then anyone who has the link can read this recap${audio ? " and play its voice and music" : ""} without signing in, even people outside your Discord server, and they can pass the link on. It is the same text you posted to the table.`,
  /** Said while the link is off, for a recap the DM approved and has not posted (#2419). */
  offLeadApproved: (audio: boolean): string =>
    `Nothing is shared until you make a link. Then anyone who has the link can read this recap${audio ? " and play its voice and music" : ""} without signing in, even people outside your Discord server, and they can pass the link on. It is the text you approved; it is not posted to your table. If you change the text, the link stops opening it until you approve it again.`,
  /** Said while the link is on. */
  onLead: (audio: boolean): string =>
    `Anyone who has this link can read this recap${audio ? " and play its voice and music" : ""} without signing in, even people outside your Discord server. Anyone you give it to can pass it on, so treat the link like a password.`,
  make: "Make a link",
  linkLabel: "The link to share",
  copy: "Copy",
  copied: "Copied",
  copyFailed: "Copy didn't work. Press and hold the link, then choose Copy.",
  off: "Turn the link off",
  fresh: "Make a new link",
  freshHint:
    "Turning the link off or making a new one stops the old link. It can't take back what someone already read or saved.",
  working: "Working…",
  copyingWait: "Wait for the change to finish, then copy.",
  doneOff: "The link is off. It no longer opens the recap. Anyone who already saw it may still have a copy.",
  doneFresh: "Here is your new link. The old one no longer opens the recap. Anyone who already saw it may still have a copy.",
  doneOn: "Here is your link.",
  notPosted:
    "Approve the recap first (above, press Approve and post text only, or Approve but don't post). Then you can make a link.",
  problem: {
    "not-found": "We can't find that recap. Reload the page.",
    "not-posted":
      "Approve the recap first (press Approve and post text only, or Approve but don't post). Then you can make a link.",
    "signed-out": "You were signed out. Sign in again.",
    network: "We couldn't reach DMbot. Check your connection and try again.",
    "slow-down": "Too many tries just now. Wait a minute and try again.",
    server: "Something went wrong on our side. Try again in a minute.",
  } as Record<string, string> & { server: string },
  /** The state in the recap list. */
  listTag: "Link on",
  audio: RECAP_AUDIO_LIVE,
} as const;

/** The public page at /r/<token>. One message for every kind of dead link, on purpose: the API
 * answers the same for a wrong, never-made, turned-off or no-longer-passing link. */
export const publicWords = {
  loading: "Loading…",
  dead: "This recap link isn't active. Ask your DM for a new one.",
  busy: "Too many tries just now. Wait a minute and open the link again.",
  posted: (day: string): string => `Posted ${day}`,
  /** A recap the DM approved and shared without posting it to the table (#2419). */
  approved: "Shared by the DM.",
  /** A network or server trouble: not a dead link, so the reader is not sent to the DM for nothing. */
  trouble: "We couldn't load this recap. Check your connection and try again.",
  tryAgain: "Try again",
  audioTrouble: "The sound couldn't be played. Reload the page to try again.",
} as const;
