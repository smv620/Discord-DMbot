// The recap page's "Approve the text" part (#2084, #2083). No audio starts by itself: the DM reads
// the recap, may edit it, approves the text, and then picks what to make. Plain words, the same
// four choices as the Discord buttons (core/src/dmbot/recaps/approval.py LABELS). The part shows
// only while RECAP_AUDIO_LIVE is true (recap-words.ts).
import type { ApiErrorKind, ApproveChoice } from "../account/api";

export const approveWords = {
  heading: "Approve the text",
  lead: "Read the recap first. When you approve it, you pick what DMbot makes from it. Nothing is made until you pick. If you change the text later, you approve it again.",
  approve: "Approve the text",
  pickHeading: "What should DMbot make?",
  /** Where the plan has no narration or music. */
  textOnlyNote: "Voice-over and music aren't in your plan, so the only choice is Text only.",
  /** The plan lookup failed, which is not the same as having no audio. */
  planUnknown: "We couldn't check your plan, so only Text only is shown. Reload to see all the choices.",
  checking: "Checking your plan…",
  busy: "Approving…",
  back: "Go back",
  /** The Discord buttons' labels (approval.py LABELS). */
  choices: {
    voice: "🎙️ Voice-over",
    music: "🎵 Background music",
    both: "Both",
    text: "Text only",
  } satisfies Record<ApproveChoice, string>,
  /** Said once it is done. */
  done: {
    voice: "You approved the text. DMbot is making the voice-over. This page shows it when it is ready.",
    music: "You approved the text. DMbot is making the background music. This page shows it when it is ready.",
    both: "You approved the text. DMbot is making the voice-over and the music. This page shows them when they are ready.",
    text: "You approved the text. No audio will be made.",
  } satisfies Record<ApproveChoice, string>,
  /** Said when the text was approved, on a later visit. */
  was: {
    voice: "You approved this text for a voice-over.",
    music: "You approved this text for background music.",
    both: "You approved this text for a voice-over and music.",
    text: "You approved this text with no audio.",
  } satisfies Record<ApproveChoice, string>,
  /** Said when the API refused because of the cues. */
  cuesUnlisted:
    "Nothing was approved. DMbot found a problem with a cue but can't say where. Press Edit and check each 《 》 and (( )), then save and approve again.",
  editEnds: "If you change the text, you approve it again.",
} as const;

/** Said instead of the "not-allowed" line when the page has just read the recap again itself. */
export const approveRefreshed = "This recap changed, maybe in Discord. The page is up to date now.";

export const approveErrors: Partial<Record<ApiErrorKind, string>> = {
  "not-in-plan": "Voice-over and music aren't in your plan. Pick Text only.",
  "bad-choice": "That isn't one of the choices. Reload the page and try again.",
  "not-allowed": "This recap changed, maybe in Discord. Reload the page to see where it is now.",
  "not-found": "We can't find that recap. Reload the page, or open it again from your recaps.",
  "signed-out": "You were signed out. Sign in again, then approve the text again.",
  network: "We couldn't reach DMbot. Check your connection and try again.",
};
