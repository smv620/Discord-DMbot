// The words of a road's or route place's condition (#4451, page half): the ux-critic's words on #4258, kept exactly.
export type RoadValue = "open" | "hard" | "closed" | "gone" | "other";

export const roadConditionWords = {
  button: "🚧 Condition",
  menuPlaceholder: "How is it now?",
  /** The choices in the menu, in order. "none" is "Not sure" and takes the condition off. */
  choices: [
    { value: "open", label: "Open", hint: "Nothing in the way" },
    { value: "hard", label: "Hard going", hint: "Slow or risky, but you can get through" },
    { value: "closed", label: "Closed", hint: "Blocked or snowed in, for now" },
    { value: "gone", label: "Gone", hint: "Collapsed or washed away for good" },
    { value: "other", label: "Something else…", hint: "Say it in your own words (60 characters at most)" },
    { value: "none", label: "Not sure", hint: "Take the condition off" },
  ] as const,
  /** The label for a value; an "other" value shows the DM's own words. */
  label: (value: RoadValue, words: string): string => {
    const found: Record<RoadValue, string> = { open: "Open", hard: "Hard going", closed: "Closed", gone: "Gone", other: words };
    return found[value];
  },
  /** "Closed since session 5 (was open)"; no session, "Closed (was open)"; no earlier value, "Closed". The first letter is a capital. */
  line: (value: RoadValue, words: string, since: number | null, before: { value: RoadValue; words: string } | null): string => {
    const now = capital(roadConditionWords.label(value, words));
    const was = before ? ` (was ${roadConditionWords.label(before.value, before.words).toLowerCase()})` : "";
    return `🚧 Condition: ${now}${since !== null ? ` since session ${since}` : ""}${was}`;
  },
  readNote: " (DMbot read this. Players see it once you press ✅ Condition is right.)",
  undoLabel: "↩ Undo",
  conditionRight: "✅ Condition is right",
  hide: "🙈 Hide from players",
  show: "👁️ Show to players",
  gone: "That's gone. Open the page again to see it as it is now.",
  couldNot: "Couldn't do that just now. Nothing changed. Try again in a moment.",
  /** After a change: "🚧 Noted: Icewind Pass is closed.", with the earlier value, "was open; now it's closed.", own words, or no condition. */
  noted: (name: string, value: RoadValue | "none", words: string, before: RoadValue | null): string => {
    if (value === "none") return `🚧 Noted: no condition for ${name}.`;
    if (value === "other") return `🚧 Noted for ${name}: ${words}.`;
    const now = roadConditionWords.label(value, words).toLowerCase();
    if (before !== null && before !== value) return `🚧 Noted: ${name} was ${roadConditionWords.label(before, "").toLowerCase()}; now it's ${now}.`;
    return `🚧 Noted: ${name} is ${now}.`;
  },
};

function capital(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}
