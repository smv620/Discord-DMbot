import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-20",
  title: "Speak a note to DMbot",
  what: "Under Tell DMbot what to fix, press Speak instead and say what to change (up to a minute). DMbot turns it into words in the box and deletes the recording as soon as the words are ready; nothing is kept but the words. You check the words before you press Send. It counts toward your listening time, and DMbot only listens to people who have agreed to be recorded.",
  held: "Waits for the voice routes (#2113 part 2) to deploy, the microphone header to be live, and for dev1 to see a spoken note turned into words. When it is live, move this up and turn on RECAP_VOICE_LIVE in recap-words.ts (it needs RECAP_FIX_LIVE too).",
  order: 11,
};
