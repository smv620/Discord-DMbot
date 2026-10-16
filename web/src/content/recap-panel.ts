// The recap page's audio part (#1418): the three players, and where the audio has got to. Plain
// words: "narration" and "music", never the makers' words. The failed line is the API's own
// sentence (audio_error, a short fixed reason), shown as it is. All of it shows only while
// RECAP_AUDIO_LIVE is true (recap-words.ts).
export const panelWords = {
  heading: "Narration and music",
  voice: "Narration",
  music: "Music and ambience",
  mix: "Both together",
  off: "There is no audio for this recap.",
  queued: "The audio is being made. This page shows it when it is ready.",
  failed: "The audio couldn't be made. The text is fine.",
  stale:
    "The audio was made from the earlier text. Make it again after you save.",
  again: "Make the audio again",
  asked: "Making the audio again. This page shows it when it is ready.",
  tooSoon: "You asked a few minutes ago. Try again in a little while.",
  expired: "The audio link ran out. Reload the page to play it.",
};
