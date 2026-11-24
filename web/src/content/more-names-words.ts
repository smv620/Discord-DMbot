// The words of "📝 More names" on the Questions page (#5081): Discord's own (`ui/more_names.py`), so both places say the same.
// Counts only: nothing here holds a name from a transcript.
export const moreNamesWords = {
  button: "📝 More names",
  /** What the button does, said once under it. */
  lead: "Turn the names waiting from your imported sessions into names to check.",
  stage: { waiting: "Waiting for DMbot", working: "Adding names" } as Record<string, string>,
  working: "Adding names to check…",
  slow: "This is taking a while. It carries on by itself: you can leave this page.",
  /** The result line, as Discord's reply says it (made = names now waiting in the check; left = still behind the button). */
  result: (made: number, left: number): string => {
    if (made > 0) {
      const first = `📝 ${made} more ${made === 1 ? "name" : "names"} to check from your imported sessions. Say which are in your game under New names to check.`;
      return left > 0 ? `${first} ${left} more ${left === 1 ? "waits" : "wait"}: press 📝 More names again after these.` : first;
    }
    return left > 0
      ? `DMbot already knew those names. ${left} more ${left === 1 ? "waits" : "wait"}: press 📝 More names again.`
      : "✅ No more names are waiting.";
  },
  /** Said after a press: the count on the line above is the bot's, counted about every minute. */
  countLater: "The count above updates within a minute.",
  failed: "Something went wrong, so no names were added. They are still waiting: try again later.",
  notAllowed: "Only the game master can do this.",
  tooSoon: "Please wait a moment, then press the button again.",
} as const;
