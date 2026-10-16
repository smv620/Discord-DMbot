/**
 * Every word on the DM's recap pages (#1386): the list at /account/recaps and the review page
 * at /account/recaps/<id>. Plain words; every message says what to do next. Nothing here
 * promises more than the API does: Post only asks DMbot, which checks once more and sends it.
 */
import type { ApiErrorKind, Recap } from "../account/api";

export const text = {
  heading: "Your recaps",
  lead: "After a session, DMbot writes a short recap for your table. Only you can see it until you post it.",
  loading: "Loading your recaps…",
  empty: "No recaps yet. DMbot emails you when one is ready.",
  // While recaps are off (RECAPS_LIVE false) the page promises nothing: no email, no recap to come.
  emptyOff: "No recaps yet.",
  back: "All your recaps",
  linkToList: "Read your session recaps",
  account: "Back to My Account",
  open: "Open",
  signedOut: "Sign in to read your recaps.",
  signIn: "Sign in with Discord",
  untitled: "Session recap",
  notFound:
    "We can't find that recap. Open it again from your email, or pick it from your list.",
  onlyYou: "Only you can see this until you post it.",
  working: "DMbot is still writing this recap. Check back in a few minutes.",
  failed: "DMbot couldn't write this recap. Press Try again, or don't post it.",
  failedNoTries:
    "DMbot couldn't write this recap, and there are no more tries. You can leave it unposted.",
  textHeading: "Recap",
  housekeepingHeading: "Housekeeping",
  housekeepingNone: "Nothing to sort out.",
  edit: "Edit",
  editLabel: "Recap",
  housekeepingLabel: "Housekeeping (one line for each)",
  save: "Save changes",
  saving: "Saving…",
  cancel: "Cancel",
  saved: "Saved.",
  post: "Post to the table",
  again: "Try again",
  drop: "Don't post",
  busy: "Working…",
  noTriesLeft:
    "You have tried twice. You can still edit it, post it or not post it.",
  posting:
    "Sent. DMbot will check it once more, then post it. Check back here to see if it posted.",
  posted: "This recap was posted to your table.",
  dropped:
    "You chose not to post this recap. It stays here, and nobody else sees it.",
  beforeChoosing:
    "DMbot only suggests this recap. You decide what your players see.",
  againHint: "Try again writes a new recap and replaces your changes.",
  tryingAgain:
    "DMbot is making it again. You will get an email when it is ready.",
} as const;

/** "Ready to read", "Posted"… for one row of the list. */
export function statusLine(recap: Recap): string {
  if (recap.status === "posted") return "Posted";
  if (recap.status === "posting") return "Posting";
  if (recap.status === "dropped") return "Not posted";
  if (recap.state === "failed") return "Couldn't be made";
  if (recap.working) return "Being made";
  return "Ready to read";
}

/** "Try again (2 left)". The button is greyed at none. */
export function againLabel(left: number): string {
  return left > 0 ? `${text.again} (${left} left)` : text.again;
}

export function sessionDay(sessionAt: number, locale?: string): string {
  return new Date(sessionAt * 1000).toLocaleDateString(locale, {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}

export const errors: Partial<Record<ApiErrorKind, string>> & { other: string } =
  {
    "signed-out":
      "You were signed out. Sign in again, then open the recap again.",
    network: "We couldn't reach DMbot. Check your connection and try again.",
    "not-found": text.notFound,
    "not-allowed":
      "That isn't possible any more. Reload the page to see where this recap is now.",
    "not-editable":
      "This recap can't be changed any more. Reload the page to see where it is now.",
    "bad-text": "The recap is empty or too long. Shorten it and save again.",
    other: "Something went wrong on our side. Try again in a minute.",
  };
