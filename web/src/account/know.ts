// Who knows a name or a connection on the Story Graph, in the DM's full view (#3167). With a `knowWord` the
// marks and faded look follow it; without one, the older `secret` and `revealed` flags still decide, so a player,
// the Players View, See as… and the overview tray look exactly as before.
import type { KnowWord } from "./api";

/** The states that are faded: only the DM (or the NPCs) know, so the players do not see them yet. */
const FADED: KnowWord[] = ["secret", "hidden_from_the_players", "known_to_an_npc"];

/** The 🤫 mark shows for a secret only. */
export function markSecret(x: { secret?: boolean; knowWord?: KnowWord }): boolean {
  return x.knowWord ? x.knowWord === "secret" : x.secret === true;
}

/** Whether a name is faded, from its state when it has one, else from the not-revealed flag. */
export function dimmedName(x: { revealed?: boolean; knowWord?: KnowWord }): boolean {
  return x.knowWord ? FADED.includes(x.knowWord) : x.revealed === false;
}

/** A graph as a player or a See as… read sees it: no state, however it came (the page never shows one it was not
 * meant to have, even from an API that over-sends it). */
export function withoutKnow<T extends { knowWord?: KnowWord }>(x: T): T {
  if (x.knowWord === undefined) return x;
  const { knowWord: _drop, ...rest } = x;
  return rest as T;
}
export function plainGraph<G extends { nodes: { knowWord?: KnowWord }[]; edges: { links: { knowWord?: KnowWord }[] }[] }>(g: G): G {
  return { ...g, nodes: g.nodes.map(withoutKnow), edges: g.edges.map((e) => ({ ...e, links: e.links.map(withoutKnow) })) };
}
