// The words of "Did they mean…?" on the Questions page (#4841 B1, #4857): core's own words for it (transcript/questions.py and
// ui/heard_review.py), with no markdown. A name or a word past its room is cut the way core cuts it (`_short`): the characters
// that fit, then "…".

/** Cut `text` to `max` characters, the last of them "…" when it is longer (core's `_short`). */
export function cutWords(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

export const heardWords = {
  title: "Did they mean…?",
  question: (speaker: string | null, heard: string): string => `DMbot heard ${speaker ?? "a player"} say "${cutWords(heard, 60)}". Did they mean…`,
  notSure: "Not sure? Press ⏳ Later.",
  moreAfter: (n: number): string => `${n} more after this.`,
  optionLabel: (name: string): string => cutWords(name, 25),
  keep: (heard: string): string => `Keep "${cutWords(heard, 18)}"`,
  later: "⏳ Later",
  kept: (heard: string): string => `✅ Got it: DMbot won't change "${cutWords(heard, 60)}" in this campaign.`,
  fixed: (heard: string, name: string): string =>
    `✅ Got it: "${cutWords(heard, 60)}" is now written ${name}, in that line and from now on in this campaign. Earlier lines stay as heard.`,
  lineKept: (heard: string, name: string): string =>
    `✅ Got it: from now on, "${cutWords(heard, 60)}" is written ${name} in this campaign. Earlier lines stay as heard. That line stays as heard.`,
  gotIt: "✅ Got it.",
  expired: "⌛ This question is closed, so the words stay as heard.",
  gone: "This question is closed: that person stopped being recorded.",
  onlyDm: "Only the DM can answer this.",
  nameGone: "That name isn't in the campaign anymore, so the words stay as heard.",
  cantCheck: "DMbot can't check the names right now. Try again in a moment.",
  notSaved: "Couldn't save that. Nothing was changed. Press it again.",
  couldNot: "Something went wrong. Nothing was changed. Press the button again.",
  allChecked: "✅ All checked. DMbot will ask after a session if it isn't sure of a name.",
  // Questions are waiting, but each one has been put off with ⏳ Later: they come back when the list is read again.
  allLater: "Those are put off for now. Reload the page to see them again.",
  failed: "Couldn't load this.",
  loading: "Loading…",
  saving: "Saving…",
};
