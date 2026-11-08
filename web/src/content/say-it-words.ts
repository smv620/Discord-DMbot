// The words of "how to say it" (#2317; docs/PLAN.md, "How to say it"): next to the box for how a name is
// said, a suggestion, the DM's own voice and a way to hear the result. Plain words. DMbot never decides:
// every answer lands in the box for the DM to change, and nothing is saved until the DM saves.
/** The longest recording, in seconds (the API takes at most this, and 1 MB). */
export const CLIP_SECONDS = 10;

export const sayIt = {
  suggest: "💡 Suggest",
  suggesting: "Thinking…",
  // A slow wait says it is still going and how long it has been, then (as the name summary does) that it can be left (#3347).
  slow: (n: number): string => `Still working (${n}s). This is taking longer than usual.`,
  stalled: (n: number): string => `Nothing new yet after ${n}s. You can leave this and try again.`,
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
  // Hear it in the recap narrator's voice (#3807): the words go to DMbot's voice service, so it is said once, and the
  // wait shows DMbot is working. With no narration set up the browser's own voice reads it, and that is said too.
  hearPrivacy: "Pressing 🔊 Hear it sends what's in the box to DMbot's voice service. It plays the sound in your recap narrator's voice.",
  hearStage: "Making the sound in the narrator's voice…",
  hearQuiet: "Still working. You can keep going: your spelling stays in the box. If no sound comes, press 🔊 Hear it again.",
  browserVoice: "This is your browser's voice, not the narrator's. Recaps will sound different.",
  // Said once, near the buttons.
  how: "DMbot only listens while you press 🎤 Say it. DMbot turns what you say into letters. Check them before you save. Your recording is deleted once it's turned into letters.",
  filled: "Here is what DMbot heard, as letters. Change them if they're not right, then save.",
  suggested: "Here is a suggestion. Change it if it's not right, then save.",
  playbackLabel: (name: string): string => `Your recording of ${name}`,
  problem: {
    "voice-off": "Saying it isn't available right now. You can type how to say it instead.",
    "too-soon": "That was pressed a moment ago. Wait a few seconds, then press 🔊 Hear it again.",
    "play-refused": "The sound is ready but your browser would not play it. Press 🔊 Hear it again.",
    "still-waiting": "No sound yet. Wait a minute, then press 🔊 Hear it again.",
    busy: "Finish the other recording first.",
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
