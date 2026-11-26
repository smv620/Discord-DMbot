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
    `✅ Got it: "${cutWords(heard, 60)}" is now written ${cutWords(name, 60)}, in that line and from now on in this campaign. Earlier lines stay as heard.`,
  lineKept: (heard: string, name: string): string =>
    `✅ Got it: from now on, "${cutWords(heard, 60)}" is written ${cutWords(name, 60)} in this campaign. Earlier lines stay as heard. That line stays as heard.`,
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
  // "Type it…" (#4865): the words are core's, with the markdown taken out.
  typeIt: "Type it…",
  // A typed name that waits for a check, said after its saved line (#4876).
  newName: (name: string): string => `${cutWords(name, 60)} is new: it waits in New names to check.`,
  typeTheName: "Type the name",
  typeLabel: "The name, as it should be written",
  typeHint: "For example: Hrothgar",
  save: "Save",
  cancel: "Cancel",
  typedProblem: {
    empty: "No name was typed. Type one and press Save, or press Cancel.",
    bar: "That has a | sign, which a name can't have. Type just the name and press Save.",
    unreadable: "That has characters DMbot can't read. Type it in by hand and press Save.",
    "too long": "That's longer than 60 characters. Type just the name and press Save.",
    "too many words": "That's more than 8 words. Type just the name and press Save.",
    link: "That's a link, not a name. Type just the name and press Save.",
    secret: "That would put a secret name in the transcript, which everyone in the server can read, so DMbot won't write it. Pick another answer, or ignore the question.",
    two: "Two names in this campaign are spelled exactly like that, so DMbot can't tell which you mean. Pick a button, or give one of them a different spelling on the Names page.",
  } as Record<string, string>,
  typedRefused: "That name is already in this campaign (maybe as a secret one), so DMbot can't add it again. Pick a button, or look it up on the Names page.",
};
