// The shared rules for who knows a name or connection (#3167): the mark, the fading, and the guard that keeps a state
// off any read that is not the DM's own full view.
import { describe, expect, it } from "vitest";

import { dimmedName, markSecret, plainGraph, withoutKnow } from "../src/account/know";

describe("the mark and the fading", () => {
  it("marks secret by its state when it has one, and by the old flag when it has none", () => {
    expect(markSecret({ knowWord: "secret", secret: false })).toBe(true);
    expect(markSecret({ knowWord: "known_to_an_npc", secret: true })).toBe(false);
    expect(markSecret({ secret: true })).toBe(true);
    expect(markSecret({})).toBe(false);
  });

  it("fades the three states that the players do not know, and not the other two", () => {
    expect(dimmedName({ knowWord: "secret" })).toBe(true);
    expect(dimmedName({ knowWord: "hidden_from_the_players" })).toBe(true);
    expect(dimmedName({ knowWord: "known_to_an_npc" })).toBe(true);
    expect(dimmedName({ knowWord: "partly_revealed" })).toBe(false);
    expect(dimmedName({ knowWord: "known_to_the_party" })).toBe(false);
  });

  it("falls back to the not-revealed flag when there is no state", () => {
    expect(dimmedName({ revealed: false })).toBe(true);
    expect(dimmedName({ revealed: true })).toBe(false);
    expect(dimmedName({})).toBe(false);
  });
});

describe("a state never leaves the DM's own full view", () => {
  it("takes the state off one name or connection and keeps the rest", () => {
    expect(withoutKnow({ id: "a", knowWord: "secret" })).toEqual({ id: "a" });
    const plain: { id: string; knowWord?: never } = { id: "b" };
    expect(withoutKnow(plain)).toBe(plain);
  });

  it("takes the state off every name and connection of a graph", () => {
    const graph = {
      nodes: [{ id: "n1", knowWord: "secret" as const }, { id: "n2" }],
      edges: [{ links: [{ id: "l1", knowWord: "known_to_the_party" as const }, { id: "l2" }] }],
    };
    const plain = plainGraph(graph);
    expect(plain.nodes.some((n) => "knowWord" in n)).toBe(false);
    expect(plain.edges.flatMap((e) => e.links).some((l) => "knowWord" in l)).toBe(false);
    expect(plain.nodes.map((n) => n.id)).toEqual(["n1", "n2"]);
  });
});
