// The words of "how to say it" (#2317; docs/PLAN.md, "How to say it"): next to the box for how a name is
// said, a suggestion, the DM's own voice and a way to hear the result. Plain words. DMbot never decides:
// every answer lands in the box for the DM to change, and nothing is saved until the DM saves.
/** The longest recording, in seconds (the API takes at most this, and 1 MB). */
export const CLIP_SECONDS = 10;

export const sayIt = {
  suggest: "💡 Suggest",
  suggesting: "Thinking…",
  sayIt: "🎤 Say it",
  asking: "Waiting for the microphone…",
  askingHint: "Your browser may ask to use the microphone. Choose Allow.",
  stop: "⏹ Stop",
  recording: (seconds: number): string => `Recording… ${seconds} of ${CLIP_SECONDS} seconds`,
  startedAloud: `Recording. It stops by itself after ${CLIP_SECONDS} seconds.`,
  recorded: "Got it. Play it back, then use it or record again.",
  playBack: "▶️ Play it back",
  playing: "Playing…",
  again: "Record again",
  useThis: "Use this",
  sending: "Turning it into letters…",
  hear: "🔊 Hear it",
  // Said once, near the buttons.
  how: "DMbot only listens while you press 🎤 Say it. DMbot turns what you say into letters. Check them before you save. Your recording is deleted once it's turned into letters.",
  filled: "Here is what DMbot heard, as letters. Change them if they're not right, then save.",
  suggested: "Here is a suggestion. Change it if it's not right, then save.",
  playbackLabel: (name: string): string => `Your recording of ${name}`,
  problem: {
    "voice-off": "Saying it isn't available right now. You can type how to say it instead.",
    "mic-refused": "DMbot can't use the microphone. Allow it in your browser, or type how to say it instead.",
    "bad-clip": "DMbot couldn't make that out. Try again somewhere quieter, or type it.",
    "too-big": `That recording was a bit long. Try again in under ${CLIP_SECONDS} seconds, or type it.`,
    "no-consent": "DMbot only listens to people who agreed to be recorded. You can type how to say it instead.",
    "signed-out": "You were signed out. Sign in again, then try again.",
    "not-allowed": "Only this campaign's DM can change how a name is said.",
    "not-found": "That name isn't in this campaign any more. Reload the page to see the latest list.",
    "bad-text": "DMbot couldn't turn that into letters. Try again, or type it.",
    server: "Something went wrong on our side. Try again in a minute, or type it.",
  } as Record<string, string>,
  suggestNone: "DMbot has no suggestion for that name. You can type how to say it.",
} as const;
