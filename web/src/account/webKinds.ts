// How each kind of name is drawn on the Story Graph (#2992): a style key per kind, so a name has its own
// colour, its own shape and its own word, and colour is never the only signal. Kinds the API sends that have
// no style of their own (an idea, a clue, a people) share "other": a plain grey circle with a dashed edge.
export type KindStyle = "npc" | "player_character" | "place" | "group" | "item" | "event" | "creature" | "quest" | "other";

/** The styles in the order the legend and the areas give them: the people first. */
export const KIND_STYLES: KindStyle[] = ["player_character", "npc", "creature", "group", "place", "item", "event", "quest", "other"];

const STYLE_OF: Record<string, KindStyle> = {
  npc: "npc",
  deity: "npc", // a god is an NPC (#2562)
  character: "npc",
  player_character: "player_character",
  place: "place",
  group: "group",
  faction: "group",
  item: "item",
  event: "event",
  creature: "creature",
  quest: "quest",
  mystery: "quest",
  hook: "quest",
  goal: "quest",
  promise: "quest",
  thread: "quest",
};

export function kindStyle(kind: string): KindStyle {
  return STYLE_OF[kind] ?? "other";
}
