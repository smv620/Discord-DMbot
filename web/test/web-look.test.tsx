/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Story Graph's settings (#3620): what the viewer chose is kept in this browser and restored, reset puts today's look
// back, a kind left unticked drops its names and their lines, spacing moves the names, sizes follow the choice, and a
// line's width follows how many connections it holds (and not at all when that is switched off).
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { WebEdge, WebGraph, WebNode } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import WebMap, { lineWidthOf } from "../src/account/WebMap";
import { forceStep, settle, startNodes, linkPairs } from "../src/account/webForce";
import { DEFAULT_LOOK, LOOK_RANGE, readLook, saveLook } from "../src/account/webPrefs";
import { graphSettingsWords as w } from "../src/content/graph-settings-words";
import { webPage as words } from "../src/content/web-words";

beforeEach(() => {
  window.localStorage.clear();
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce"), addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const nodes: WebNode[] = [
  { id: "m", name: "Mira Voss", kind: "npc" },
  { id: "g", name: "Garrick", kind: "npc" },
  { id: "h", name: "Far Hall", kind: "place" },
  { id: "o", name: "The Order", kind: "group" },
];
const link = (id: string, text: string) => ({ id, words: text, secret: false, direction: "a-b" as const });
const edges: WebEdge[] = [
  { a: "m", b: "g", label: "serves", more: 3, links: [link("l1", "serves"), link("l2", "owes"), link("l3", "fears"), link("l4", "knows")] },
  { a: "m", b: "h", label: "lives at", more: 0, links: [link("l5", "lives at")] },
  { a: "g", b: "o", label: "leads", more: 1, links: [link("l6", "leads"), link("l7", "serves")] },
];

describe("what the viewer chose", () => {
  it("is kept, restored, and put back to today's look by a reset", () => {
    expect(readLook()).toEqual(DEFAULT_LOOK);
    saveLook({ kinds: ["place"], spacing: 1.4, nodeSize: 1.2, textSize: 1.3, lineWidth: false });
    expect(readLook()).toEqual({ kinds: ["place"], spacing: 1.4, nodeSize: 1.2, textSize: 1.3, lineWidth: false });
    saveLook({ ...DEFAULT_LOOK });
    expect(window.localStorage.getItem("dmbot:web-look")).toBeNull();
  });

  it("keeps a slider inside its range and shrugs off bad data", () => {
    window.localStorage.setItem("dmbot:web-look", JSON.stringify({ kinds: ["npc", 4], spacing: 99, nodeSize: -2, textSize: "big", lineWidth: "no" }));
    expect(readLook()).toEqual({ kinds: ["npc"], spacing: LOOK_RANGE.spacing[1], nodeSize: LOOK_RANGE.nodeSize[0], textSize: 1, lineWidth: true });
    window.localStorage.setItem("dmbot:web-look", "{not json");
    expect(readLook()).toEqual(DEFAULT_LOOK);
  });

  it("works with storage that throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readLook()).toEqual(DEFAULT_LOOK);
    expect(() => saveLook({ ...DEFAULT_LOOK, spacing: 1.5 })).not.toThrow();
    vi.restoreAllMocks();
  });
});

describe("spacing for Spread out", () => {
  const spread = (s: number) => {
    const list = startNodes(nodes, edges, "m", {});
    settle(list, linkPairs(list, edges), 0, "free", new Map(), s);
    const mid = list.find((n) => n.id === "m")!;
    return list.filter((n) => n.id !== "m").reduce((t, n) => t + Math.hypot(n.x - mid.x, n.y - mid.y), 0) / 3;
  };
  it("puts the names farther apart when it is more, and closer when it is less", () => {
    expect(spread(1.6)).toBeGreaterThan(spread(1));
    expect(spread(0.7)).toBeLessThan(spread(1));
  });
  it("is today's spacing at 1", () => {
    const a = startNodes(nodes, edges, "m", {});
    const b = startNodes(nodes, edges, "m", {});
    forceStep(a, linkPairs(a, edges), 0, "free", new Map(), 1);
    forceStep(b, linkPairs(b, edges), 0, "free", new Map(), 1, 1);
    expect(a.map((n) => [n.x, n.y])).toEqual(b.map((n) => [n.x, n.y]));
  });
});

describe("a line's width", () => {
  it("grows with its connections to four, then stops, and the chosen line is two wider", () => {
    expect([1, 2, 3, 4, 9].map((n) => lineWidthOf(n, true, false)?.strokeWidth)).toEqual([2, 3, 4.5, 6, 6]);
    expect(lineWidthOf(2, true, true)?.strokeWidth).toBe(5);
  });
  it("sets nothing when switched off", () => {
    expect(lineWidthOf(3, false, false)).toBeUndefined();
  });
  it("is drawn on each line of the map by its count, and left to the style sheet when off", () => {
    const widths = () => [...document.querySelectorAll(".web-line")].map((l) => (l as SVGElement).style.strokeWidth);
    const { rerender } = render(<WebMap nodes={nodes} edges={edges} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} look={DEFAULT_LOOK} />);
    expect(widths().slice(0, 3).sort()).toEqual(["2px", "3px", "6px"]);
    rerender(<WebMap nodes={nodes} edges={edges} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} look={{ ...DEFAULT_LOOK, lineWidth: false }} />);
    expect(widths().slice(0, 3)).toEqual(["", "", ""]);
  });
});

describe("the size of names and words", () => {
  it("draws a name's shape bigger or smaller, and sets the words' size", () => {
    render(<WebMap nodes={nodes} edges={edges} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} look={{ ...DEFAULT_LOOK, nodeSize: 1.5, textSize: 1.2 }} />);
    const shape = document.querySelector(".web-node:not(.centre) circle") as SVGElement;
    expect(Number(shape.getAttribute("r"))).toBe(24 * 1.5);
    expect((document.querySelector(".web-svg") as SVGElement).style.getPropertyValue("--web-text")).toBe(`${14 * 1.2}px`);
  });
});

const graph = (): WebGraph => ({ center: "m", nodes, edges, truncated: false, maxHops: 2, canEdit: true, view: "full" });
async function openPage() {
  const api = mockApi("table");
  api.webGraph = async () => graph();
  api.webSize = async () => ({ names: 4 });
  render(<Web api={api} path="/account/web/100.cmp1" />);
  await screen.findByText(/names?, \d+ lines?/);
}

describe("the settings on the page", () => {
  it("lists the kinds with counts, and a kind left ticked is the only one drawn, with its lines", async () => {
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: words.settingsButton }));
    const panel = (await screen.findByRole("heading", { name: w.heading })).closest("section") as HTMLElement;
    expect(within(panel).getByLabelText(w.kindCount("NPCs", 2))).toBeTruthy();
    fireEvent.click(within(panel).getByLabelText(/^Places? \(1\)|Far Hall|Place/i));
    await waitFor(() => expect(document.querySelectorAll(".web-node").length).toBe(2)); // the middle name stays, and the place
    expect(document.querySelectorAll(".web-edge").length).toBe(1);
    expect(JSON.parse(window.localStorage.getItem("dmbot:web-look") ?? "{}").kinds).toHaveLength(1);
  });

  it("keeps the middle name when its kind is not ticked, and ignores a saved kind this campaign has no names of", async () => {
    saveLook({ ...DEFAULT_LOOK, kinds: ["place"] });
    await openPage();
    await waitFor(() => expect(document.querySelectorAll(".web-node").length).toBe(2)); // the middle name (an NPC) and the place
    cleanup();
    saveLook({ ...DEFAULT_LOOK, kinds: ["vehicle"] });
    await openPage();
    await waitFor(() => expect(document.querySelectorAll(".web-node").length).toBe(4)); // nothing here is a vehicle: everything is shown
  });

  it("reset puts every choice back and says so", async () => {
    saveLook({ ...DEFAULT_LOOK, textSize: 1.4, lineWidth: false });
    await openPage();
    fireEvent.click(screen.getByRole("button", { name: words.settingsButton }));
    fireEvent.click(await screen.findByRole("button", { name: w.reset }));
    expect(await screen.findByText(w.resetDone)).toBeTruthy();
    expect(window.localStorage.getItem("dmbot:web-look")).toBeNull();
  });
});
