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
    "This audio was made before a name's spelling was fixed. Press the button below to update it on this page. The message already in Discord keeps the old audio.",
  audioOff: "Audio is switched off for now, so it can't be made. Try again later.",
  again: "Make the audio again",
  asked: "Making the audio again. This page shows it when it is ready.",
  needsApproval: "Approve the new text first.",
  tooSoon: "You asked a few minutes ago. Try again in a little while.",
  expired: "The audio link ran out. Reload the page to play it.",
};

// "Remake the voice" after editing the words (#2630). The API answers with a fixed code; the page says it
// in plain words: what is made again, and why when it is everything. Counts and codes only.
export const remakeWords = {
  voiceButton: "Remake the voice",
  allButton: "Remake voice and music",
  voice: (changed: number, total: number): string =>
    `Only the voice will be made again, for the ${changed} changed ${changed === 1 ? "paragraph" : "paragraphs"} (of ${total}). The music stays.`,
  all: {
    paragraphs: "You added or removed a paragraph, so the voice and the music will both be made again.",
    moods: "You moved or changed a music or pause tag, so the voice and the music will both be made again.",
    size: "You changed a lot of the text, so the voice and the music will both be made again to fit.",
    no_parts: "The old voice is no longer stored, so the voice and the music will both be made again.",
  } as Record<string, string>,
  allFallback: "The voice and the music will both be made again.",
  same: "The words the narrator reads haven't changed, so pressing the button makes nothing new.",
  lastVoice: "Last remake: voice only. The music was kept.",
  lastAll: "Last remake: voice and music.",
  asked: "Started. The step, how far it has got and when it last moved show here.",
  stage: {
    audio_voice: "Speaking the paragraphs",
    audio_music: "Making the music",
    audio_mix: "Mixing the voice and the music",
  } as Record<string, string>,
  unit: { paragraphs: "paragraphs" } as Record<string, string>,
};

// "Fix a name everywhere" in the recap editor (#2630): the DM types the wrong spelling and the right one,
// and every use in the text box is replaced. Nothing is saved until Save; the names list is not touched.
export const fixNameWords = {
  heading: "Fix a name everywhere",
  wrongLabel: "Wrong spelling",
  rightLabel: "Right spelling",
  button: "Replace everywhere",
  none: (wrong: string): string => `"${wrong}" isn't in the text. It must be the whole word; capitals don't matter.`,
  done: (wrong: string, right: string, times: number): string =>
    `Replaced "${wrong}" with "${right}" ${times} ${times === 1 ? "time" : "times"}.`,
  note: "Changes the text in the box only. Nothing is kept until you press Save. The names list stays as it is.",
};
