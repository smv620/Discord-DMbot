// The recap page's "Approve this recap" part (#2084, #2083, #2419). The DM reads the recap, may edit
// it, and approves it in one of three plain ways: post the text, make audio to review first, or
// keep it without posting. Nothing is made or posted until a button is pressed. The labels are the
// same as the Discord buttons' (#2420). The audio button is greyed, with the reason, where the
// narration can't be made (plan, month's allowance, not set up).
import type { ApiErrorKind, ApproveChoice, AudioUnavailable } from "../account/api";

export const approveWords = {
  heading: "Approve this recap",
  lead: "Read the recap above. Nothing reaches your players until you choose. Only the first button posts. If you change the text later, you approve it again.",
  /** The three ways to approve (the owner's words, 2026-10-22). */
  postText: "✅ Approve and post text only",
  makeAudio: "🎙️ Approve and make audio to review",
  keepIt: "📋 Approve but don't post",
  /** Why the audio button is greyed. */
  audioWhy: {
    plan: "Audio isn't in your plan, so the audio button is off.",
    allowance: "You've used all your audio for this month, so the audio button is off. You get more on the 1st.",
    not_set_up: "Audio isn't available yet, so the audio button is off.",
    unavailable: "Audio isn't available for this campaign right now, so the audio button is off.",
  } satisfies Record<AudioUnavailable, string>,
  /** The plan lookup failed, which is not the same as having no audio. */
  audioUnknown: "We couldn't check whether audio is available, so the audio button is off. Reload the page to try again.",
  audioChecking: "Checking if audio is available…",
  approve: "Approve the text",
  pickHeading: "Which audio should DMbot make? Pressing one approves the text. Nothing is posted.",
  busy: "Approving…",
  back: "Go back",
  /** The Discord buttons' labels (approval.py LABELS). */
  choices: {
    voice: "🎙️ Voice-over",
    music: "🎵 Background music",
    both: "Both",
    text: "Text only",
  } satisfies Record<ApproveChoice, string>,
  /** The state once approved and not posted (the owner's words), and what can be done with it. */
  notPosted: "Approved. Not posted: share it your own way below.",
  /** The same, where there is no Post button to offer. */
  copy: "Copy the text",
  copied: "Copied.",
  copyFailed: "Your browser wouldn't copy it. Select the text above and copy it yourself.",
  /** The recap's audio is ready to play: it is not posted until the DM says so. */
  review: "Your audio is ready. Listen to it below. Nothing is posted until you press Post to the table, which sends the text and the audio.",
  /** Said when the post step failed after the text was approved. */
  approvedNotPostedPrefix: "Approved, but not posted yet.",
  postLater: "Post to the table",
  keepAudio: "Keep it, don't post",
  posting: "Posting…",
  /** Said once it is done. */
  done: {
    voice: "You approved the text. DMbot is making the voice-over. Nothing is posted. This page shows it when it is ready.",
    music: "You approved the text. DMbot is making the background music. Nothing is posted. This page shows it when it is ready.",
    both: "You approved the text. DMbot is making the voice-over and the music. Nothing is posted. This page shows them when they are ready.",
    text: "You approved the text. No audio will be made.",
  } satisfies Record<ApproveChoice, string>,
  /** Said when the text was approved, on a later visit. */
  was: {
    voice: "Approved. DMbot is making the voice-over. Nothing is posted.",
    music: "Approved. DMbot is making the background music. Nothing is posted.",
    both: "Approved. DMbot is making the voice-over and music. Nothing is posted.",
    text: "Approved. No audio. Nothing is posted.",
  } satisfies Record<ApproveChoice, string>,
  /** Said when the API refused because of the cues. */
  cuesUnlisted:
    "Nothing was approved. DMbot found a problem with a cue but can't say where. Press Edit at the bottom of the page and check each 《 》 and (( )), then save and approve again.",
  editEnds: "If you change the text, you approve it again.",
} as const;

/** Said instead of the "not-allowed" line when the page has just read the recap again itself. */
export const approveRefreshed = "This recap changed, maybe in Discord. The page is up to date now.";

export const approveErrors: Partial<Record<ApiErrorKind, string>> = {
  "not-in-plan": "Audio isn't in your plan. Go back and press Approve and post text only, or Approve but don't post.",
  "audio-unavailable": "Audio can't be made right now. Go back and press Approve and post text only, or Approve but don't post.",
  "bad-choice": "That isn't one of the choices. Reload the page and try again.",
  "not-allowed": "This recap changed, maybe in Discord. Reload the page to see where it is now.",
  "not-found": "We can't find that recap. Reload the page, or open it again from your recaps.",
  "signed-out": "You were signed out. Sign in again, then approve the text again.",
  network: "We couldn't reach DMbot. Check your connection and try again.",
};
