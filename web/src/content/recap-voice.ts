// The words for "Speak instead" under "Tell DMbot what to fix" (#2114; core #2113 part 2). The DM
// records a short note; DMbot turns it into words and deletes the recording at once, then the words
// appear in the box to check before Send. Nothing is kept but the words. A DM who has not agreed to
// be recorded gets nothing (the consent rule). Shown only while RECAP_VOICE_LIVE is true (recap-words.ts).
export const VOICE_MAX_SECONDS = 120;
export const VOICE_MAX_BYTES = 2 * 1024 * 1024;

const clock = (s: number): string => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

export const voiceWords = {
  speak: "🎤 Speak instead",
  stop: "Stop",
  stopLabel: "Stop recording",
  starting: "Waiting for your microphone…",
  recording: (seconds: number): string =>
    `Recording ${clock(seconds)} of ${clock(VOICE_MAX_SECONDS)}. Press Stop when you're done.`,
  atLimit: `That is the longest a note can be (${clock(VOICE_MAX_SECONDS)}), so recording stopped.`,
  sending: "Turning your note into words…",
  slow: "This is taking longer than usual. Press Check again, or type your note instead.",
  checkAgain: "Check again",
  heard: "Here's what I heard. Check it, change it if you like, then press Send.",
  privacy: "Your recording is turned into text and then deleted. It is never kept.",
  listening: "This counts toward your listening time.",
  listeningButton: "About listening time",
  unsupported: "This browser can't record here. Type your note instead.",
  problem: {
    denied:
      "Your browser didn't let DMbot use the microphone. Allow it in the browser's settings for this site, or type your note instead.",
    "no-mic": "DMbot couldn't find a microphone. Type your note instead.",
    "too-big": "That recording is too long. Keep it under 2 minutes, or type your note instead.",
    "bad-type": "This browser records in a form DMbot can't read. Type your note instead.",
    "bad-clip": "DMbot couldn't hear that recording. Try again, or type your note instead.",
    too_long: "That recording is too long. Keep it under 2 minutes, or type your note instead.",
    no_words: "DMbot didn't hear any words. Speak closer to the microphone and try again.",
    transcribe_failed: "DMbot couldn't turn that into words. Try again, or type your note instead.",
    "no-consent":
      "DMbot only listens to people who have agreed to be recorded. Agree in Discord, then try again, or type your note instead.",
    "not-allowed":
      "Recaps are turned off for this campaign or its plan right now. Check ⚙️ Settings → 📜 Recaps.",
    "not-editable":
      "This recap can't change now (it was posted or skipped, or it is being written). Open the recap list to see where it is.",
    "too-soon": "You sent a recording a moment ago. Wait a minute, then try again.",
    "not-found": "We can't find that recap. Reload the page.",
    "signed-out": "You were signed out. Sign in again.",
    network: "We couldn't reach DMbot. Check your connection and try again.",
    server: "Something went wrong on our side. Try again in a minute.",
  } as Record<string, string> & { server: string },
} as const;
