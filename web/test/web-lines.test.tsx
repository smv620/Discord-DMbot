/** @jsxImportSource preact */
// @vitest-environment happy-dom
/** The Connections list reads subject, what it does, object, from the name in the middle (#2666). */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/preact";
import Web from "../src/account/Web";
import { mockApi } from "../src/account/mock";
import type { WebEdge, WebGraph, WebLink } from "../src/account/api";
import { connectionCounts, listLines, readLink } from "../src/account/webLines";

const PATH = "/account/web/100.cmp1";
const names: Record<string, string> = {
  t: "Tavin Ashgrove",
  m: "Mira Voss",
  a: "Aldric Greyfen",
  c: "the Lantern Circle",
  z: "Zed Far",
};
const nameOf = (id: string): string => names[id] ?? id;
const link = (id: string, words: string, direction: WebLink["direction"], back?: string | null): WebLink => ({
  id,
  words,
  secret: false,
  direction,
  ...(back !== undefined ? { back } : {}),
});
// The door sorts each pair by id: here "a" < "c" < "m" < "t" < "z".
const edge = (a: string, b: string, links: WebLink[]): WebEdge => ({ a, b, label: links[0]!.words, more: links.length - 1, links });

// Tavin is a member of the Circle; Mira is a member too; the Circle is led by Aldric (stored the
// other way round: Aldric leads the Circle, with no reading back); Mira and Zed are allies.
const edges: WebEdge[] = [
  edge("c", "t", [link("1", "has as a member", "a-b", "is a member of")]),
  edge("c", "m", [link("2", "has as a member", "a-b", "is a member of")]),
  edge("a", "c", [link("3", "leads", "a-b", null)]),
  edge("m", "z", [link("4", "is allied with", "both")]),
];
// (each stored with the subject first; the words read a to b)

describe("readLink", () => {
  it("reads a one-way link from the middle name when it starts there", () => {
    const e = edge("t", "c", [link("1", "is a member of", "a-b", "has as a member")]);
    const l = readLink(e, e.links[0]!, "t", nameOf);
    expect([l.from, l.words, l.to]).toEqual(["Tavin Ashgrove", "is a member of", "the Lantern Circle"]);
  });

  it("uses the words back when the link reads into the middle name", () => {
    const e = edge("t", "c", [link("1", "is a member of", "a-b", "has as a member")]);
    const l = readLink(e, e.links[0]!, "c", nameOf);
    expect([l.from, l.words, l.to]).toEqual(["the Lantern Circle", "has as a member", "Tavin Ashgrove"]);
  });

  it("reads a link whose stored order is the other way round the same", () => {
    // "the Lantern Circle is led by Aldric Greyfen": the words read from the second name (b-a)
    const e = edge("a", "c", [link("1", "is led by", "b-a", "leads")]);
    expect(readLink(e, e.links[0]!, "a", nameOf)).toMatchObject({ from: "Aldric Greyfen", words: "leads", to: "the Lantern Circle" });
    expect(readLink(e, e.links[0]!, "c", nameOf)).toMatchObject({ from: "the Lantern Circle", words: "is led by", to: "Aldric Greyfen" });
  });

  it("reads a both-ways link from the middle name, either end", () => {
    const e = edge("m", "z", [link("4", "is allied with", "both")]);
    expect(readLink(e, e.links[0]!, "m", nameOf)).toMatchObject({ from: "Mira Voss", words: "is allied with", to: "Zed Far" });
    expect(readLink(e, e.links[0]!, "z", nameOf)).toMatchObject({ from: "Zed Far", words: "is allied with", to: "Mira Voss" });
  });

  it("writes a link with no reading back as it reads, from the far name, never reversed", () => {
    const e = edge("a", "c", [link("3", "leads", "a-b", null)]);
    const l = readLink(e, e.links[0]!, "c", nameOf);
    expect([l.from, l.words, l.to]).toEqual(["Aldric Greyfen", "leads", "the Lantern Circle"]);
  });

  it("reads as the words go when no middle name is given", () => {
    const e = edge("t", "c", [link("1", "is a member of", "b-a", "has as a member")]);
    expect(readLink(e, e.links[0]!, null, nameOf)).toMatchObject({ from: "the Lantern Circle", to: "Tavin Ashgrove" });
  });
});

describe("listLines", () => {
  it("counts each name's connections", () => {
    expect(connectionCounts(edges).get("c")).toBe(3);
    expect(connectionCounts(edges).get("z")).toBe(1);
  });

  it("puts the middle name's own lines first, most connected far name first, then by name", () => {
    const { own } = listLines(edges, "c", nameOf);
    expect(own.map((l) => `${l.from} ${l.words} ${l.to}`)).toEqual([
      // Mira has 2 connections (the Circle, Zed); Tavin and Aldric have 1 each, so by name
      "the Lantern Circle has as a member Mira Voss",
      "Aldric Greyfen leads the Lantern Circle",
      "the Lantern Circle has as a member Tavin Ashgrove",
    ]);
  });

  it("keeps every link between the same two names as its own line", () => {
    const two = [edge("a", "c", [link("1", "leads", "a-b", null), link("2", "guards", "a-b", null)])];
    expect(listLines(two, "c", nameOf).own).toHaveLength(2);
  });

  it("has one list and nothing further out when no name is in the middle", () => {
    const got = listLines(edges, null, nameOf);
    expect(got.further).toEqual([]);
    expect(got.own).toHaveLength(4);
  });

  it("lists what does not touch the middle name as further out, sentence-ordered", () => {
    const { further } = listLines(edges, "c", nameOf);
    expect(further.map((l) => `${l.from} ${l.words} ${l.to}`)).toEqual(["Mira Voss is allied with Zed Far"]);
  });
});

describe("the page", () => {
  afterEach(cleanup);
  const graph = (canEdit: boolean, center: string, shown: WebEdge[] = edges): WebGraph => ({
    center,
    nodes: Object.entries(names).map(([id, name]) => ({ id, name, kind: "npc" as const })),
    edges: shown,
    truncated: false,
    maxHops: 3,
    canEdit,
  });
  const show = async (center: string, canEdit = true, shown: WebEdge[] = edges) => {
    const api = mockApi("table");
    api.webSize = async () => ({ names: 5 });
    api.webGraph = async () => graph(canEdit, center, shown);
    render(<Web api={api} path={PATH} />);
    await screen.findByRole("heading", { name: "Connections" });
  };
  const section = () => screen.getByRole("heading", { name: "Connections" }).parentElement!;

  it("writes the lines from the name in the middle, with arrows for the eye and words for a reader", async () => {
    await show("c");
    const list = section();
    expect(list.querySelector(".visually-hidden")?.textContent).toContain("the Lantern Circle has as a member Mira Voss");
    expect(list.textContent).toContain("the Lantern Circle → has as a member → Mira Voss");
    expect(list.textContent).toContain("Aldric Greyfen → leads → the Lantern Circle");
    expect(document.body.textContent).not.toContain("↔");
  });

  it("shows a Further out list for what does not touch the middle", async () => {
    await show("c");
    expect(screen.getByRole("heading", { name: "Further out" })).toBeTruthy();
    expect(section().textContent).toContain("Mira Voss → is allied with → Zed Far");
  });

  it("has no Further out list when everything touches the middle", async () => {
    await show("c", true, edges.slice(0, 3));
    expect(screen.queryByRole("heading", { name: "Further out" })).toBeNull();
  });
});
