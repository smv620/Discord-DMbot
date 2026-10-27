// The words for "Where it was said" on a name to check (#2403; core #2329): up to two short windows of
// the session's own transcript around where DMbot heard the name, as Discord's name card shows them. The
// lines are the campaign's transcript, shown only to its DM, who may read transcripts. A line whose
// words were removed (a player pressed Stop recording me) is shown as a placeholder, never with its words
// or its speaker. Shown only while NAME_PLACES_LIVE is true (recap-words.ts).
export const namePlaceWords = {
  heading: "Where it was said",
  session: (n: number): string => `Session ${n}`,
  // Seconds into the session, as the transcript pages show them.
  time: (seconds: number): string => {
    const s = Math.max(0, Math.floor(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const rest = String(s % 60).padStart(2, "0");
    return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${rest}` : `${m}:${rest}`;
  },
  lineRemoved: "A line that was removed",
  // A whole window with nothing left to show; it is never hidden without saying so.
  gone: (n: number): string => `A line from session ${n}, no longer available.`,
  openTranscript: "Open in transcript",
  openTranscriptFor: (n: number): string => `Open in transcript: session ${n}`,
  more: (n: number): string => (n === 1 ? "1 more place in the transcripts." : `${n} more places in the transcripts.`),
} as const;
