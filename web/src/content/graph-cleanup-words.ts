// The words of the Story Graph's clean-up (#3740): the people and groups no chain of connections links to a player
// character. Plain words. DMbot never deletes anything by itself; every change is the game master's. Counts only in
// the waits; never a word of a transcript.
export const cleanupWords = {
  open: "Clean up the map",
  close: "Close the clean-up",
  done: "Done",
  nameThem: "Name them or remove them",
  heading: "Clean up the map",
  lead: "These people and groups aren't linked to any player character, or to anyone who is. Nothing here is deleted unless you say so. Add the connection DMbot missed, or leave them as they are.",
  loading: "DMbot is looking for people the map doesn't link to the party.",
  stage: "Reading the story's connections.",
  quiet: "No answer yet. Try again in a minute.",
  down: "DMbot couldn't read the clean-up list. Nothing was changed. Try again.",
  tryAgain: "Try again",
  signedOut: "You're signed out. Sign in again to see the clean-up.",
  nothing: "Nothing is waiting here. Everyone DMbot knows is linked to the party.",
  noPlayerCharacter: "This campaign has no player character yet, so DMbot can't tell who is unlinked. Add one on the Names page first.",
  showing: (shown: number, total: number): string => `Showing ${shown} of ${total}. Link some of these and the rest will show.`,
  count: (people: number, groups: number): string =>
    `${people} ${people === 1 ? "name" : "names"} in ${groups} ${groups === 1 ? "group" : "groups"} aren't linked to the party.`,
  group: (n: number): string => `Group ${n}`,
  linksHeading: "Connections inside this group",
  noLinks: "Not connected to anyone yet.",
  waiting: "waiting for you to keep it",
  scene: "Where DMbot met them:",
  noScene: "DMbot kept no summary of where they came up.",
  refresh: "Look again",
  // The Add a connection form for one entry.
  addHeading: (name: string): string => `Add a connection for ${name}`,
  findLabel: "Who are they connected to?",
  findHint: "Type part of a name.",
  findNone: "DMbot doesn't know a name like that. Try fewer letters.",
  findWorking: "Looking…",
  pick: (name: string): string => `Connect to ${name}`,
  picked: (name: string): string => `Connecting to ${name}.`,
  wordsLabel: "How are they connected?",
  wordsHint: "For example: sister of, works for, travels with.",
  add: "Add the connection",
  adding: "DMbot is adding the connection.",
  added: "Connection added. If it joins them to the party, the group leaves this list.",
  needBoth: "Pick who they are connected to, and say how.",
  addFailed: "That didn't work. Nothing was added. Try again in a minute.",
  // The review line (#3741), counts only, for a campaign that shares its story to improve DMbot.
  reviewWaiting: (minutes: number, fixes: number): string =>
    `DMbot will check your ${fixes === 1 ? "fix" : "fixes"} in ${minutes} ${minutes === 1 ? "minute" : "minutes"}, once you stop making changes.`,
  reviewRunning: "DMbot is checking your fixes.",
  reviewDone: (missed: number): string =>
    missed === 0 ? "Checked: DMbot missed nothing." : `Checked: DMbot missed ${missed} ${missed === 1 ? "thing" : "things"} it should have caught.`,
} as const;
