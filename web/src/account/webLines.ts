/** The Connections list, written the way a sentence reads: subject, then what it does, then object
 * (#2666). Pure logic so the page and its tests share one rule. */
import type { WebEdge, WebLink } from "./api";

/** One connection as a sentence: `from` does `words` to `to`. */
export interface WebLine {
  key: string;
  edge: WebEdge;
  link: WebLink;
  fromId: string;
  from: string;
  words: string;
  toId: string;
  to: string;
}

/** How many names each name is connected to in the drawn web: the order's measure of "relevant". */
export function connectionCounts(edges: WebEdge[]): Map<string, number> {
  const near = new Map<string, Set<string>>();
  const add = (id: string, other: string): void => {
    const set = near.get(id) ?? new Set<string>();
    set.add(other);
    near.set(id, set);
  };
  for (const e of edges) {
    add(e.a, e.b);
    add(e.b, e.a);
  }
  return new Map([...near].map(([id, others]) => [id, others.size]));
}

/** Which end the words start at, from the edge's `a` and `b`. The door says "a-b", "b-a" or "both". */
function readsFrom(link: WebLink): "a" | "b" | "both" {
  return link.direction === "a-b" ? "a" : link.direction === "b-a" ? "b" : "both";
}

/** One link as a sentence read from `fromId` when that end is the name in the middle; with no
 * middle name (or one that is not on this link) it reads as the words go. A link that reads into
 * the middle name uses its `back` words; one with none is written as it reads, from the far name,
 * so no line ever reads backwards. */
export function readLink(edge: WebEdge, link: WebLink, fromId: string | null, nameOf: (id: string) => string): WebLine {
  const key = `${edge.a}|${edge.b}|${link.id}`;
  const reads = readsFrom(link);
  const [subject, object] = reads === "b" ? [edge.b, edge.a] : [edge.a, edge.b];
  const line = (from: string, words: string, to: string): WebLine => ({
    key,
    edge,
    link,
    fromId: from,
    from: nameOf(from),
    words,
    toId: to,
    to: nameOf(to),
  });
  if (fromId !== edge.a && fromId !== edge.b) return line(subject, link.words, object);
  const far = fromId === edge.a ? edge.b : edge.a;
  if (reads === "both" || subject === fromId) return line(fromId, link.words, far);
  // It reads from the far name into the middle one.
  return link.back ? line(fromId, link.back, far) : line(subject, link.words, object);
}

/** The name in the middle's own connections, then the rest ("Further out"). Each is most relevant
 * first: the far name with the most connections, then by name; a link is its own line. */
export function listLines(
  edges: WebEdge[],
  center: string | null,
  nameOf: (id: string) => string,
): { own: WebLine[]; further: WebLine[] } {
  const counts = connectionCounts(edges);
  const count = (id: string): number => counts.get(id) ?? 0;
  const own: WebLine[] = [];
  const further: WebLine[] = [];
  for (const e of edges) {
    const touches = center !== null && (e.a === center || e.b === center);
    for (const l of e.links) (touches ? own : further).push(readLink(e, l, touches ? center : null, nameOf));
  }
  // The middle name's list is ordered by the far name; further out, by the one the sentence starts with.
  const far = (x: WebLine): { id: string; name: string } =>
    x.fromId === center ? { id: x.toId, name: x.to } : { id: x.fromId, name: x.from };
  const byFar = (x: WebLine, y: WebLine): number =>
    count(far(y).id) - count(far(x).id) || far(x).name.localeCompare(far(y).name) || x.key.localeCompare(y.key);
  const bySubject = (x: WebLine, y: WebLine): number =>
    count(y.fromId) - count(x.fromId) || x.from.localeCompare(y.from) || x.to.localeCompare(y.to) || x.key.localeCompare(y.key);
  // With no name in the middle there is nothing to be further out from: one list, sentence-ordered.
  if (center === null) return { own: further.sort(bySubject), further: [] };
  return { own: own.sort(byFar), further: further.sort(bySubject) };
}
