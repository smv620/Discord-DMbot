// The words for "Speak instead" under "Tell DMbot what to fix" (#2114; core #2113 part 2). The DM
// records a short note; DMbot turns it into words and deletes the recording at once, then the words
// appear in the box to check before Send. Nothing is kept but the words. A DM who has not agreed to
// be recorded gets nothing (the consent rule). Shown only while RECAP_VOICE_LIVE is true (recap-words.ts).
// A note is a minute at most (Supervisor, 2026-10-20): a minute of speech is about 1,000 letters, which is
// what the typed box holds, and the API's own limit is two minutes. The 15-second warning comes before it.
// Recording stops at 59 seconds, not 60: the meter rounds a clip up to a whole minute, so a note a hair
// over 60 seconds would be charged two.
export const VOICE_MAX_SECONDS = 60;
/** Recording stops at 59 seconds: a clip just over a minute would be charged two minutes of listening time. */
export const VOICE_STOP_AT_SECONDS = 59;
export const VOICE_WARN_AT_SECONDS = 45;
export const VOICE_MAX_BYTES = 2 * 1024 * 1024;

const clock = (s: number): string => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

export const voiceWords = {
  speak: "🎤 Speak instead",
  stop: "Stop",
  stopLabel: "Stop recording",
  starting: "Waiting for your microphone…",
  recordingNow: "Recording. Press Stop when you're done.",
  timer: (seconds: number): string => `${clock(seconds)} of ${clock(VOICE_MAX_SECONDS)}`,
  hint: "Up to a minute.",
  warn: "15 seconds left.",
  cancel: "Cancel",
  cancelLabel: "Cancel and throw the recording away",
  cancelled: "Recording thrown away. Nothing was sent.",
  cutOff: "Your note was longer than 1,000 letters, so the end was cut. Check it before Send.",
  atLimit: "That is the longest a note can be (1 minute), so recording stopped.",
  empty: "Nothing was recorded. Try again, or type your note instead.",
  sending: "Turning your note into words… this takes a few seconds. Stay on this page.",
  slow: "This is taking longer than usual. Press Check again, or type your note instead.",
  checkAgain: "Check again",
  heard: "Here is what DMbot heard. Check it, change it if you like, then press Send.",
  privacy:
    "Your recording goes to our speech service, is turned into words, and is deleted as soon as the words are ready. We keep only the words.",
  listening: "This counts toward your listening time.",
  listeningButton: "About listening time",
  unsupported: "This browser can't record here. Try Chrome or Firefox, or type your note instead.",
  problem: {
    denied:
      "Your browser blocked the microphone. Tap the lock icon next to the address, allow Microphone, then try again. Or type your note instead.",
    "no-mic": "DMbot couldn't find a microphone. Type your note instead.",
    "too-big": "That recording is too big. Keep it shorter, or type your note instead.",
    "bad-type": "This browser records in a way DMbot can't read. Type your note instead.",
    "bad-clip": "DMbot couldn't hear that recording. Try again, or type your note instead.",
    too_long: "That recording is too long. Keep it to a minute, or type your note instead.",
    no_words: "DMbot didn't hear any words. Speak closer to the microphone and try again.",
    transcribe_failed: "DMbot couldn't turn that into words. Try again, or type your note instead.",
    "no-consent":
      "Agree to recording in Discord first, then try again. DMbot only listens to people who have agreed to be recorded; it sends you a button in a private message to agree. Or type your note instead.",
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
