// The words of the Session notes panel on the Transcripts page (#3027). Plain words: a numbered list of what happened.
export const sessionNotesWords = {
  heading: "Session notes",
  intro: "A numbered list of what happened in each game, kept with the campaign. Only this campaign's game masters see it. It stays when a game's transcript is deleted.",
  title: (sessionNumber: number | null, day: string): string => (sessionNumber ? `Session ${sessionNumber}` : `Session of ${day}`),
  line: (day: string, beats: number): string => `${day} · ${beats === 1 ? "1 beat" : `${beats} beats`}`,
  open: "Open",
  hide: "Hide",
  openFor: (title: string): string => `Open the notes for ${title}`,
  hideFor: (title: string): string => `Hide the notes for ${title}`,
  opening: "Opening the notes…",
  failed: "Couldn't open these notes. Try again in a minute.",
  tryAgain: "Try again",
  empty: "Nothing was written down for this game.",
  who: (names: string): string => `Who: ${names}`,
  where: (place: string): string => `Where: ${place}`,
};
