// The words for splitting a name that is really a list, and for saying a name is a group (#3406; the
// website half of #3383). Nothing here is shown until the bot's half is on and the What's new entry
// that holds these words leaves held.
export const nameGroupWords = {
  split: "These are separate names",
  splitIcon: "✂️",
  yesGroup: "Yes, that group",
  isGroup: "They're a group",
  groupIcon: "👥",
  groupAsk: "What is the group called?",
  groupMake: "Make the group",
  groupCancel: "Cancel",
  // Says which group the name looks like, and who is already in it. "is" for one member, "are" for several.
  hint: (group: string, members: readonly string[]): string =>
    `It looks like the group ${group}. ${members.join(", ")} ${members.length === 1 ? "is" : "are"} already in that group.`,
  undo: "Undo",
  working: "Working on it…",
  // Keyed by the bot's refusal word. A word not in this list gets `other`.
  errors: {
    not_a_list: "That isn't a list of names, so it can't be split. Keep it as one name.",
    not_found: "That name isn't waiting any more. Reload the page to see the list.",
    spelling_taken: "A name with that spelling is already in your campaign. Pick another name for the group.",
    answered: "One of those names was already answered, so this can't be undone. Keep the names as they are.",
    not_dm: "Only a game master of this campaign can do that.",
    bad_spelling: "That can't be used as a group's name. Try another name for the group.",
  },
  other: "That didn't go through. Try again in a minute.",
  // The sentence for a refusal word. Anything else, including null and undefined, gets `other`.
  reason: (code: string | null | undefined): string =>
    code !== null && code !== undefined && Object.hasOwn(nameGroupWords.errors, code)
      ? nameGroupWords.errors[code as keyof typeof nameGroupWords.errors]
      : nameGroupWords.other,
};
