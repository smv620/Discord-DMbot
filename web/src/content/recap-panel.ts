// The recap page's audio part (#1418): the three players, and where the audio has got to. Plain
// words: "narration" and "music", never the makers' words. The failed line is the API's own
// sentence (audio_error, a short fixed reason), shown as it is. All of it shows only while
// RECAP_AUDIO_LIVE is true (recap-words.ts).
export const panelWords = {
  heading: "Narration and music",
  voice: "Voice",
  music: "Music",
  mix: "Both mixed",
  play: "Play",
  pause: "Pause",
  position: "Where you are in the recap",
  voiceVolume: "Voice volume",
  musicVolume: "Music volume",
  wontPlay: "The sound couldn't play. Try Play again, or use Both mixed below.",
  cuesHeading: "Where the music changes",
  cuesNote:
    "Coloured tags are the mood the music changes to. Dashed grey tags are pauses or pace for the narrator. Only you see this; your players get the text without them.",
  lead: "Voice and music play together. Switch one off to hear the other alone.",
  mixNote: "One file with both, for downloads or other apps. It plays on its own.",
  switchOn: "On",
  switchOff: "Off",
  volume: "Volume",
  off: "There is no audio for this recap.",
  queued: "The audio is being made. This page shows it when it is ready.",
  failed: "The audio couldn't be made. The text is fine.",
  stale:
    "The audio was made from the earlier text. Make it again after you save.",
  stalePosted:
    "This audio was made before a name's spelling was fixed. Press Make the audio again to update it on this page. The message already in Discord keeps the old audio.",
  audioOff: "Audio is switched off for now, so it can't be made. Try again later.",
  again: "Make the audio again",
  asked: "Making the audio again. This page shows it when it is ready.",
  needsApproval: "Approve the new text first.",
  tooSoon: "You asked a few minutes ago. Try again in a little while.",
  expired: "The audio link ran out. Reload the page to play it.",
};
