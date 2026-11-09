// The words of "Share this campaign's story to improve DMbot" on a campaign's settings (#3741), in the same shape as
// pause-words.ts. Only a game master changes it; the panel asks before turning it on and never asks before turning it off.
export const shareStoryWords = {
  heading: "Share this campaign's story to improve DMbot",
  lead: "When this is on, short quotes from your campaign's story are sent privately to DMbot's developers, so they can see where DMbot missed something. Players are never shown them. People who said no to helping improve DMbot are always left out.",
  onState: "Sharing is on.",
  offState: "Sharing is off.",
  turnOn: "Turn sharing on",
  turnOff: "Turn sharing off",
  askOn: "Turn sharing on? Short quotes from your story will be sent privately to DMbot's developers.",
  yesOn: "Yes, turn it on",
  cancel: "Cancel",
  confirmLabel: "Confirm",
  waiting: "Waiting for DMbot to pick it up.",
  working: "DMbot is changing it.",
  quiet: "DMbot is slow to answer. Your choice is still waiting. You can leave this page and check back later.",
  doneOn: "Sharing is on.",
  doneOff: "Sharing is off. Anything waiting to be checked or sent was removed.",
  failed: "That didn't work, and nothing was changed. Try again in a moment.",
  problems: {
    "not-allowed": "Only a game master of this campaign can change this.",
  } as Record<string, string>,
};
