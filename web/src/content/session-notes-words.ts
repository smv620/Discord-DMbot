// The words of the Session notes panel on the Transcripts page (#3027). Plain words: what happened in each game.
export const sessionNotesWords = {
  heading: "Session notes",
  intro: "A numbered list of what happened in each game, kept with the campaign. Only this campaign's game masters see it. It stays when a game's transcript is deleted.",
  noSessions: "No session notes yet. They are kept when DMbot makes a game's recap.",
  title: (sessionNumber: number | null, day: string): string => (sessionNumber !== null ? `Session ${sessionNumber}` : `Session of ${day}`),
  // With a number the date leads the line; with no number the title already has the date (#3138).
  line: (day: string | null, things: number): string => {
    const count = things === 1 ? "1 thing that happened" : `${things} things that happened`;
    return day ? `${day} · ${count}` : count;
  },
  open: "Open",
  hide: "Hide",
  openFor: (title: string): string => `Open the notes for ${title}`,
  hideFor: (title: string): string => `Hide the notes for ${title}`,
  opening: "Opening the notes…",
  // After three seconds the wait says how long it has run, and counts on each second (CLAUDE.md "Every wait").
  stillOpening: (seconds: number): string => `Still opening the notes… ${seconds} seconds so far.`,
  failed: "Couldn't open these notes.",
  tryAgain: "Try again",
  empty: "Nothing was written down for this game.",
  who: (names: string): string => `Who: ${names}`,
  where: (place: string): string => `Where: ${place}`,
};
