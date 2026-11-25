// The words of "🔗 Look-alikes" (#5259): Discord's own (`ui/look_alikes.py`), with the markdown taken out. Names, kinds and counts are the
// campaign's own; nothing here is a transcript word.
const plural = (n: number, one: string, many: string): string => (n === 1 ? one : many);
/** "an NPC" / "a place": Discord's article rule. */
const a = (kind: string): string => (kind.slice(0, 1).toLowerCase().match(/[aeiou]/) || kind === "NPC" ? "an" : "a");
const cut = (name: string, n: number): string => (name.length > n ? `${name.slice(0, n - 1)}…` : name);

export const lookAlikeWords = {
  title: "🔗 Look-alikes",
  heading: (count: number): string => `🔗 Look-alikes (${count < 100 ? count : "99+"})`,
  loading: "Looking for look-alike names…",
  failed: "Couldn't load the look-alikes. Nothing was changed. Open this page again in a minute.",
  none: "That's all the groups. No more look-alikes right now.",
  question: (number: number, of: number): string => `Are these the same?${of > 1 ? ` (${number} of ${of})` : ""}`,
  line: (name: string, kind: string, heard: number): string => `${cut(name, 80)} (${kind}${heard > 1 ? `, heard ${heard} times` : ""})`,
  explain:
    "If they are, DMbot keeps one name and the others become its other names (they still count as the same person or thing). Tick the names that are the same, pick the one to keep, then press 🔗 Merge. If none of them are the same, press None are the same.",
  tickLabel: "Names that are the same",
  keepLabel: "Name to keep",
  merge: "🔗 Merge",
  mergeFor: (name: string): string => `Merge into ${name}`,
  apart: "None are the same",
  oneTicked: "Only one name is ticked. Tick another, or press None are the same.",
  keepUnticked: "The name to keep is unticked. Pick another name to keep.",
  // The one confirmation, naming every name it merges (a name of another kind says so in the same message).
  confirm: (others: { name: string; kind: string }[], keep: { name: string; kind: string }): string => {
    const shown = others.map((n) => cut(n.name, 60)).join(", ");
    const odd = others.filter((n) => n.kind !== keep.kind).map((n) => `${cut(n.name, 60)} is ${a(n.kind)} ${n.kind}; it becomes ${a(keep.kind)} ${keep.kind}'s other name.`);
    return `Merge ${shown} into ${cut(keep.name, 60)}? They become other names for ${cut(keep.name, 60)}. Connections, notes and mentions go together. Nothing is deleted, and you can undo it.${odd.length ? ` ${odd.join(" ")}` : ""}`;
  },
  yes: "🔗 Yes, merge them",
  no: "No, go back",
  stage: "Merging names",
  working: "Saving your answer…",
  slow: "Still saving. Nothing is lost: the names are merged, or left as they are, when it is done.",
  done: (n: number, keep: string): string =>
    `🔗 Done: ${n} ${plural(n, "name is", "names are")} now other ${plural(n, "name", "names")} for ${cut(keep, 60)}.`,
  noted: "👍 Noted: none of those will be suggested together again.",
  undoHint: (count: number, keep: string): string =>
    `Wrong? Undo splits ${count === 1 ? "both names" : `all ${count + 1} names`} from ${cut(keep, 60)} again.`,
  undo: "Undo",
  undone: "Undone. The names are separate again.",
  tooLate: "That can't be undone any more, because a name was changed since. Use the Names page to split them.",
  changed: "One of those names has changed. Open the look-alikes again.",
  couldNot: "That didn't work, and nothing was changed. Please try again.",
  notAllowed: "Only the DM can merge names. Ask your DM to do it.",
  more: (n: number): string => `${n} more ${plural(n, "group waits", "groups wait")}. They show when these are done.`,
} as const;
