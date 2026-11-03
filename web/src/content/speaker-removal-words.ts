// The words of "Remove this speaker's lines" on the Transcripts page (#3305 PR 2, #3306): a game master removes everything one
// person said in this campaign, for a person who is not on Discord (a member removes their own lines from Discord). Plain
// words; the wait says what it is doing, how long it has run and when it last moved, and never shows a line of text.
export const speakerRemovalWords = {
  heading: "People whose lines you kept",
  outsideHeading: "People who aren't in Discord",
  intro: "You kept these people's lines when you imported older sessions. Remove one person's lines if they ask you to.",
  memberHeading: "People you ticked in",
  memberIntro: "These server members hadn't agreed in Discord, and you ticked that they agreed. A member can also remove their own lines.",
  linesLabel: (n: number): string => `${n.toLocaleString("en-US")} ${n === 1 ? "line" : "lines"}`,
  button: "Remove this speaker's lines",
  buttonFor: (name: string): string => `Remove this speaker's lines: ${name}`,
  confirm: (name: string, member = false): string =>
    `Remove everything ${name} said in this campaign? This removes their lines and anything DMbot learned from them${member ? "" : ", and their name too"}. It cannot be undone. Other campaigns are not touched.`,
  confirmYes: "Yes, remove them",
  keep: "Keep them",
  stage: { removing: "Removing their lines. You can leave this page." } as Record<string, string>,
  done: (lines: number, sessions: number): string =>
    `${lines.toLocaleString("en-US")} ${lines === 1 ? "line" : "lines"} removed from ${sessions.toLocaleString("en-US")} ${sessions === 1 ? "session" : "sessions"}. This campaign only.`,
  nothing: "There are no lines left to remove.",
  failedDo: "Something went wrong with the removal. Check the list, then try again.",
  problems: {
    "too-soon": "A removal is already going. Wait a minute, then try again.",
    "not-allowed": "Only a game master of this campaign can remove lines.",
    "not-found": "That person isn't in this campaign any more.",
  } as Record<string, string>,
  genericProblem: "Something went wrong. Try again.",
};
