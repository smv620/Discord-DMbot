import type { Item } from "../whats-new-types";

export const entry: Item = {
  date: "2026-10-20",
  title: "Approve the text before any audio",
  what: "DMbot makes no voice-over or music until you have read the recap and pressed Approve the text, on your DM screen or on the recap page. Then you pick voice-over, background music, both, or text only (no audio), and you see only what your plan includes. If you change the text afterwards, you approve it again. If a mood or pause mark in the text is wrong, DMbot shows what to fix, and nothing is approved until you fix it.",
  held: "Waits for narrated recaps (RECAP_AUDIO_LIVE, #1385) and for #2083 to deploy. When it is on, move this up with that entry (#2084).",
  order: 14,
};
