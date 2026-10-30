// How a line reads from the name in focus (owner request after #3039). A connection is worded from one end ("owns"),
// and some have their own wording for the other ("is owned by", the link's `back`). On the map, the line of the name
// in focus says just the verb as that name would say it, with an arrow toward the other name; a line with no
// wording from that end keeps the line and says nothing, and has no arrowhead. The full sentences stay in the panel.
// (Named `map…` because the panel's `readLink` in webLines.ts follows other rules: it falls back to the far name's reading.)
import type { WebEdge, WebLink } from "./api";

export interface LineReading {
  /** The verb as the focused name says it, or null when this end has no wording. */
  verb: string | null;
  /** The arrow points from the focused name to the other: only for a connection that goes one way and has a verb here. */
  arrow: boolean;
}

/** One connection, read from `from` (one of the edge's two names). */
export function mapReadLink(edge: Pick<WebEdge, "a" | "b">, link: Pick<WebLink, "words" | "direction" | "back">, from: string): LineReading {
  if (link.direction === "both") return { verb: link.words.trim() === "" ? null : link.words, arrow: false };
  const source = link.direction === "a-b" ? edge.a : edge.b;
  const text = from === source ? link.words : (link.back ?? "");
  const verb = text.trim() === "" ? null : text;
  return { verb, arrow: verb !== null };
}

/** A whole line (one or more connections) read from `from`: the first verb there is, how many more there are, and
 * whether to draw the arrowhead (every connection that has a verb goes one way; a mutual one has none). */
export function mapReadLine(edge: WebEdge, from: string): { verb: string | null; more: number; arrow: boolean } {
  const readings = edge.links.map((l) => mapReadLink(edge, l, from)).filter((r) => r.verb !== null);
  if (readings.length === 0) return { verb: null, more: 0, arrow: false };
  return { verb: readings[0]!.verb, more: readings.length - 1, arrow: readings.every((r) => r.arrow) };
}
