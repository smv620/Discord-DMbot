// @vitest-environment happy-dom
// The Story Graph's free layout and its map (#2934): names settle apart, can be moved and stay where
// they were dropped, the lines go quiet until a name is in focus, and the person's choices stay in this
// browser. The layout is checked as pure code (no clock, no random numbers) and the map as a rendered page.
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WebMap from "../src/account/WebMap";
import type { WebEdge, WebNode } from "../src/account/api";
import { MAX_SIM_NAMES, MAX_SIM_STEPS, edgeKind, forceStep, kindAnchors, linkPairs, orderedKinds, settle, startNodes } from "../src/account/webForce";
import { readMode, readPlaces, saveMode, savePlaces } from "../src/account/webPrefs";
import { webPage as words } from "../src/content/web-words";

const nodes: WebNode[] = [
  { id: "m", name: "Mira Voss", kind: "npc" },
  { id: "g", name: "Garrick", kind: "npc" },
  { id: "h", name: "Far Hall", kind: "place" },
  { id: "o", name: "The Order", kind: "group" },
];
const link = (id: string, text: string) => ({ id, words: text, secret: false, direction: "a-b" as const });
const edges: WebEdge[] = [
  { a: "m", b: "g", label: "serves", more: 1, links: [link("l1", "serves"), link("l2", "owes")] },
  { a: "m", b: "h", label: "lives at", more: 0, links: [link("l3", "lives at")] },
  { a: "g", b: "o", label: "leads", more: 0, links: [link("l4", "leads")] },
];

const setup = (reduced = true) => {
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: reduced && q.includes("reduce"), addEventListener() {}, removeEventListener() {} }));
};

beforeEach(() => {
  window.localStorage.clear();
  setup();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const sim = (mode: "free" | "groups" = "free") => {
  const list = startNodes(nodes, edges, "m");
  const links = linkPairs(list, edges);
  const anchors = kindAnchors([...new Set(list.map((n) => n.kind))].sort());
  return { list, links, anchors, mode };
};

describe("the free layout", () => {
  it("settles to the same places every time, and stops before the step limit once calm", () => {
    const a = sim();
    const b = sim();
    const stepsA = settle(a.list, a.links, 0, "free", a.anchors);
    settle(b.list, b.links, 0, "free", b.anchors);
    expect(a.list.map((n) => [n.x, n.y])).toEqual(b.list.map((n) => [n.x, n.y]));
    expect(stepsA).toBeLessThan(MAX_SIM_STEPS);
  });

  it("keeps every name clear of the others", () => {
    const { list, links, anchors } = sim();
    settle(list, links, 0, "free", anchors);
    for (let i = 0; i < list.length; i += 1)
      for (let j = i + 1; j < list.length; j += 1) expect(Math.hypot(list[i]!.x - list[j]!.x, list[i]!.y - list[j]!.y)).toBeGreaterThan(60);
  });

  it("does not move a pinned name", () => {
    const { list, links, anchors } = sim();
    list[2]!.pinned = true;
    list[2]!.x = 400;
    list[2]!.y = -300;
    settle(list, links, 0, "free", anchors);
    expect([list[2]!.x, list[2]!.y]).toEqual([400, -300]);
  });

  it("separates two names that start on the very same spot", () => {
    const { list, links, anchors } = sim();
    for (const n of list) {
      n.x = 5;
      n.y = 5;
    }
    settle(list, links, 0, "free", anchors);
    expect(new Set(list.map((n) => `${n.x.toFixed(1)},${n.y.toFixed(1)}`)).size).toBe(list.length);
  });

  it("does no work at all for a web bigger than the cap, so a huge campaign never freezes the page", () => {
    const many = Array.from({ length: MAX_SIM_NAMES + 1 }, (_, i) => ({ id: `n${i}`, name: `N${i}`, kind: "npc" }));
    const list = startNodes(many, [], "n0");
    const before = list.map((n) => [n.x, n.y]);
    expect(settle(list, [], 0, "free", kindAnchors(["npc"]))).toBe(0);
    expect(list.map((n) => [n.x, n.y])).toEqual(before);
  });

  it("pulls names of one kind together in the grouped layout, whatever joins them to the middle", () => {
    // Half are NPCs and half places, all joined to the middle by the same kind of line.
    const many = [
      ...Array.from({ length: 4 }, (_, i) => ({ id: `p${i}`, name: `P${i}`, kind: "npc" })),
      ...Array.from({ length: 4 }, (_, i) => ({ id: `q${i}`, name: `Q${i}`, kind: "place" })),
      { id: "c", name: "Centre", kind: "player_character" },
    ];
    const some: WebEdge[] = many.slice(0, 8).map((n, i) => ({ a: "c", b: n.id, label: "x", more: 0, links: [{ ...link(`k${i}`, "x"), kind: "other" as const }] }));
    const list = startNodes(many, some, "c", {});
    expect(list.find((n) => n.id === "p0")!.group).toBe("npc");
    expect(list.find((n) => n.id === "q0")!.group).toBe("place");
    const links = linkPairs(list, some);
    settle(list, links, list.findIndex((n) => n.id === "c"), "groups", kindAnchors(["npc", "place"]));
    const mean = (g: string) => {
      const m = list.filter((n) => n.group === g);
      return { x: m.reduce((t, n) => t + n.x, 0) / m.length, y: m.reduce((t, n) => t + n.y, 0) / m.length };
    };
    const within = (g: string) => {
      const c = mean(g);
      const m = list.filter((n) => n.group === g);
      return m.reduce((t, n) => t + Math.hypot(n.x - c.x, n.y - c.y), 0) / m.length;
    };
    const apart = Math.hypot(mean("npc").x - mean("place").x, mean("npc").y - mean("place").y);
    expect(apart).toBeGreaterThan(within("npc"));
    expect(apart).toBeGreaterThan(within("place"));
  });

  it("works out a line's kind from its connections, and orders the kinds of name people first", () => {
    const e = (a: string, b: string, ...kinds: (string | undefined)[]) => ({ a, b, links: kinds.map((kind) => ({ kind })) });
    expect(edgeKind(e("a", "b", "family"))).toBe("family");
    expect(edgeKind(e("a", "b", "family", "family"))).toBe("family");
    expect(edgeKind(e("a", "b", "family", "work"))).toBe("other");
    expect(edgeKind(e("a", "b", undefined))).toBe("other");
    expect(orderedKinds(["item", "place", "npc", "player_character", "npc", "zebra", "event"])).toEqual(["player_character", "npc", "place", "item", "event", "zebra"]);
  });

  it("starts a name at its saved place, pinned, and ignores a line to a name that is not here", () => {
    const list = startNodes(nodes, edges, "m", { g: [120, -40] });
    expect(list.find((n) => n.id === "g")).toMatchObject({ x: 120, y: -40, pinned: true });
    expect(linkPairs([{ id: "m" }], edges)).toEqual([]);
    expect(forceStep([], [], 0, "free", new Map(), 1)).toBe(0);
  });
});

describe("what the person chose, kept in this browser", () => {
  it("remembers the layout and each campaign's moved names, and shrugs off bad data", () => {
    saveMode("groups");
    expect(readMode()).toBe("groups");
    savePlaces("1.a", { g: [10, 20] });
    expect(readPlaces("1.a")).toEqual({ g: [10, 20] });
    expect(readPlaces("2.b")).toEqual({});
    window.localStorage.setItem("dmbot:web-places:3.c", JSON.stringify({ x: [1, "no"], y: [1, 2, 3], z: [4, 5], w: "bad" }));
    expect(readPlaces("3.c")).toEqual({ z: [4, 5] });
    window.localStorage.setItem("dmbot:web-places:4.d", "{not json");
    expect(readPlaces("4.d")).toEqual({});
    savePlaces("1.a", {});
    expect(window.localStorage.getItem("dmbot:web-places:1.a")).toBeNull();
  });

  it("works with storage that throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readMode()).toBe("free");
    expect(readPlaces("1.a")).toEqual({});
    expect(() => saveMode("groups")).not.toThrow();
    expect(() => savePlaces("1.a", { g: [1, 2] })).not.toThrow();
    vi.restoreAllMocks();
  });
});

const draw = (extra: Partial<Parameters<typeof WebMap>[0]> = {}) =>
  render(<WebMap nodes={nodes} edges={edges} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} {...extra} />);
const nodeOf = (name: string) => [...document.querySelectorAll(".web-node")].find((n) => n.textContent?.includes(name)) as SVGGElement;

const middleButton = () => [...document.querySelectorAll(".web-side button")].find((b) => b.textContent === words.putInMiddle) as HTMLButtonElement | undefined;

describe("the map", () => {
  it("fades every name and line that is not joined to the name in focus, and brings them back", () => {
    draw();
    fireEvent.pointerEnter(nodeOf("Far Hall"));
    expect(nodeOf("Far Hall").classList.contains("web-faded")).toBe(false);
    expect(nodeOf("Mira").classList.contains("web-faded")).toBe(false);
    expect(nodeOf("Garrick").classList.contains("web-faded")).toBe(true);
    expect(nodeOf("The Order").classList.contains("web-faded")).toBe(true);
    expect(document.querySelectorAll(".web-edge.web-faded")).toHaveLength(2);
    fireEvent.pointerLeave(nodeOf("Far Hall"));
    expect(document.querySelectorAll(".web-faded")).toHaveLength(0);
  });

  it("does the same for the keyboard", () => {
    draw();
    fireEvent.focus(nodeOf("The Order"));
    expect(nodeOf("Mira").classList.contains("web-faded")).toBe(true);
    fireEvent.blur(nodeOf("The Order"));
    expect(document.querySelectorAll(".web-faded")).toHaveLength(0);
  });

  it("says a line's words only for the name in focus, and counts a line that holds several connections", () => {
    draw();
    // Each text holds its tooltip (the line's full words) first and what is drawn last.
    const texts = () => [...document.querySelectorAll(".web-edge-text")].map((t) => t.lastChild?.textContent);
    expect(texts()).toEqual(["2 connections"]);
    fireEvent.pointerEnter(nodeOf("Mira"));
    expect(texts()).toEqual(["serves (+1)", "lives at"]); // (read from Mira, who is where they are worded from)
    expect(document.querySelectorAll(".web-arrow")).toHaveLength(2);
  });

  it("reads a line from the focused end: the other wording with an arrow away, or nothing and no arrowhead", () => {
    const owned: WebEdge[] = [
      { a: "m", b: "g", label: "owns", more: 0, links: [{ ...link("l1", "owns"), back: "is owned by" }] },
      { a: "m", b: "h", label: "lives at", more: 0, links: [link("l3", "lives at")] },
    ];
    const texts = () => [...document.querySelectorAll(".web-edge-text")].map((t) => t.lastChild?.textContent);
    render(<WebMap nodes={nodes} edges={owned} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
    fireEvent.pointerEnter(nodeOf("Garrick")); // the owned end: it has its own wording
    expect(texts()).toEqual(["is owned by"]);
    expect(document.querySelectorAll(".web-arrow")).toHaveLength(1);
    fireEvent.pointerLeave(nodeOf("Garrick"));
    fireEvent.pointerEnter(nodeOf("Far Hall")); // the end with no wording: the line stays, with no words and no arrowhead
    expect(texts()).toEqual([]);
    expect(document.querySelectorAll(".web-arrow")).toHaveLength(0);
    expect(document.querySelectorAll(".web-line").length).toBeGreaterThan(0);
  });

  it("keeps the chosen line's full words when the name in focus touches it, and its tooltip keeps the full words", () => {
    const texts = () => [...document.querySelectorAll(".web-edge-text")].map((t) => t.lastChild?.textContent);
    render(<WebMap nodes={nodes} edges={edges} center="m" refId="1.a" selected={{ a: "m", b: "g" }} onCenter={() => {}} onLine={() => {}} />);
    fireEvent.pointerEnter(nodeOf("Mira"));
    expect(texts()).toContain("serves (+1)"); // (the chosen line: its full words, as the server worded them)
    expect([...document.querySelectorAll(".web-edge-text title")].map((t) => t.textContent)).toContain("serves (+1)");
  });

  it("says how many when a bundle has no wording from the focused end, and a hover wins over the pick", () => {
    const bundle: WebEdge[] = [{ a: "m", b: "g", label: "serves", more: 1, links: [link("l1", "serves"), link("l2", "owes")] }];
    const texts = () => [...document.querySelectorAll(".web-edge-text")].map((t) => t.lastChild?.textContent);
    render(<WebMap nodes={nodes} edges={bundle} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
    fireEvent.pointerEnter(nodeOf("Garrick")); // the end with no wording
    expect(texts()).toEqual(["2 connections"]);
    expect(document.querySelectorAll(".web-arrow")).toHaveLength(0);
    fireEvent.click(nodeOf("Mira")); // picks Mira; but Garrick is still hovered
    expect(texts()).toEqual(["2 connections"]);
    fireEvent.pointerLeave(nodeOf("Garrick"));
    expect(texts()).toEqual(["serves (+1)"]); // (now read from the pick)
  });

  it("draws the arrowhead beside the other name and pointing at it", () => {
    render(<WebMap nodes={nodes} edges={[edges[1]!]} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
    fireEvent.pointerEnter(nodeOf("Mira"));
    const d = document.querySelector(".web-arrow")!.getAttribute("d")!;
    expect(d).toMatch(/^M-?[\d.]+ -?[\d.]+L-?[\d.]+ -?[\d.]+L-?[\d.]+ -?[\d.]+Z$/);
    expect(d).not.toContain("NaN");
  });

  it("reads from the name that was picked when nothing is hovered, and a mutual line has the words but no arrowhead", () => {
    const mutual: WebEdge[] = [{ a: "m", b: "g", label: "allies with", more: 0, links: [{ ...link("l1", "allies with"), direction: "both" }] }];
    const texts = () => [...document.querySelectorAll(".web-edge-text")].map((t) => t.lastChild?.textContent);
    render(<WebMap nodes={nodes} edges={mutual} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
    expect(texts()).toEqual([]);
    fireEvent.click(nodeOf("Garrick"));
    fireEvent.pointerLeave(nodeOf("Garrick"));
    expect(texts()).toEqual(["allies with"]);
    expect(document.querySelectorAll(".web-arrow")).toHaveLength(0);
  });

  it("opens a line from the keyboard", () => {
    const onLine = vi.fn();
    draw({ onLine });
    const hit = document.querySelector(".web-hit") as SVGLineElement;
    expect(hit.getAttribute("aria-label")).toBe(words.lineBetween("Mira Voss", "Garrick"));
    fireEvent.keyDown(hit, { key: "Enter" });
    expect(onLine).toHaveBeenCalledWith("m", "g");
  });

  it("picks a name on Enter, and moves it with the arrow keys, keeping it there", () => {
    const onCenter = vi.fn();
    draw({ onCenter });
    const garrick = nodeOf("Garrick");
    const before = garrick.getAttribute("transform");
    fireEvent.keyDown(garrick, { key: "ArrowRight" });
    expect(nodeOf("Garrick").getAttribute("transform")).not.toBe(before);
    expect(nodeOf("Garrick").querySelector(".web-pin")).not.toBeNull();
    expect(readPlaces("1.a").g).toBeDefined();
    fireEvent.keyDown(nodeOf("Garrick"), { key: "Enter" });
    expect(onCenter).not.toHaveBeenCalled(); // (Enter picks; the panel's button puts it in the middle)
    expect(document.querySelector("#web-side-h")!.textContent).toBe("Garrick");
    fireEvent.click(middleButton()!);
    expect(onCenter).toHaveBeenCalledWith("g");
  });

  it("one click picks a name and the panel stays on it; two quick clicks put it in the middle", () => {
    const onCenter = vi.fn();
    draw({ onCenter });
    vi.spyOn(Date, "now").mockReturnValue(5000); // (two clicks in the same instant, whatever the runner's speed)
    expect(document.querySelector("#web-side-h")!.textContent).toBe("Mira Voss"); // (the middle name to start with)
    fireEvent.click(nodeOf("Garrick"));
    expect(onCenter).not.toHaveBeenCalled();
    expect(document.querySelector("#web-side-h")!.textContent).toBe("Garrick");
    fireEvent.pointerLeave(nodeOf("Garrick"));
    expect(document.querySelector("#web-side-h")!.textContent).toBe("Garrick"); // (it stays on the name clicked)
    fireEvent.click(nodeOf("Garrick"));
    expect(onCenter).toHaveBeenCalledTimes(1);
    expect(onCenter).toHaveBeenCalledWith("g");
  });

  it("two clicks on two different names are not a double click, and a slow second click is not either", () => {
    const onCenter = vi.fn();
    draw({ onCenter });
    const now = vi.spyOn(Date, "now");
    now.mockReturnValue(1000);
    fireEvent.click(nodeOf("Garrick"));
    fireEvent.click(nodeOf("Mira Voss"));
    expect(onCenter).not.toHaveBeenCalled();
    now.mockReturnValue(1000);
    fireEvent.click(nodeOf("Garrick"));
    now.mockReturnValue(1000 + 900);
    fireEvent.click(nodeOf("Garrick"));
    expect(onCenter).not.toHaveBeenCalled();
    now.mockRestore();
  });

  it("falls back to the middle name when the picked name is no longer drawn", () => {
    const view = draw();
    fireEvent.click(nodeOf("Garrick"));
    fireEvent.pointerLeave(nodeOf("Garrick"));
    expect(document.querySelector("#web-side-h")!.textContent).toBe("Garrick");
    view.rerender(<WebMap nodes={nodes.filter((n) => n.id !== "g")} edges={[]} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
    expect(document.querySelector("#web-side-h")!.textContent).toBe("Mira Voss");
  });

  it("starts the panel on the new middle name when the middle changes", () => {
    const view = draw();
    fireEvent.click(nodeOf("Garrick"));
    fireEvent.pointerLeave(nodeOf("Garrick"));
    view.rerender(<WebMap nodes={nodes} edges={edges} center="g" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
    expect(document.querySelector("#web-side-h")!.textContent).toBe("Garrick");
    view.rerender(<WebMap nodes={nodes} edges={edges} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
    expect(document.querySelector("#web-side-h")!.textContent).toBe("Mira Voss"); // (the old pick did not come back)
  });

  it("tap, drag, tap on one name is not a double click", async () => {
    withBox();
    const onCenter = vi.fn();
    draw({ onCenter });
    const now = vi.spyOn(Date, "now").mockReturnValue(7000);
    fireEvent.click(nodeOf("Garrick"));
    drag(nodeOf("Garrick"), [150, 90]);
    fireEvent.click(nodeOf("Garrick")); // (the click that ends the drag)
    fireEvent.click(nodeOf("Garrick"));
    expect(onCenter).not.toHaveBeenCalled();
    now.mockRestore();
  });

  it("offers Put it in the middle only for a name that is not already the middle", () => {
    draw();
    expect(middleButton()).toBeUndefined();
    fireEvent.click(nodeOf("Garrick"));
    expect(middleButton()!).toBeTruthy();
  });

  it("keeps a moved name where it was dropped when the map is drawn again, and lets everything go on request", () => {
    savePlaces("1.a", { h: [300, 200] });
    draw();
    expect(nodeOf("Far Hall").getAttribute("transform")).toBe("translate(300.0 200.0)");
    expect(nodeOf("Far Hall").querySelector(".web-pin")).not.toBeNull();
    const letGo = [...document.querySelectorAll(".web-tools button")].find((b) => b.textContent === words.letGo) as HTMLButtonElement;
    expect(letGo.disabled).toBe(false);
    fireEvent.click(letGo);
    expect(readPlaces("1.a")).toEqual({});
    expect(nodeOf("Far Hall").querySelector(".web-pin")).toBeNull();
  });

  it("does not leak one campaign's moved names into another", () => {
    savePlaces("1.a", { h: [300, 200] });
    draw({ refId: "2.b" });
    expect(nodeOf("Far Hall").getAttribute("transform")).not.toBe("translate(300.0 200.0)");
  });

  it("switches the layout, remembers it, and labels the areas by kind of connection", () => {
    draw();
    expect(document.querySelector(".web-area")).toBeNull();
    const group = document.querySelector(`[role="group"][aria-label="${words.layoutLabel}"]`)!;
    const [free, kind] = [...group.querySelectorAll("button")] as HTMLButtonElement[];
    expect(free!.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(kind!);
    expect(kind!.getAttribute("aria-pressed")).toBe("true");
    expect(readMode()).toBe("groups");
    expect(document.querySelectorAll(".web-area").length).toBeGreaterThan(0);
  });

  it("starts in the grouped layout when that was the last choice", () => {
    saveMode("groups");
    draw();
    expect(document.querySelectorAll(".web-area").length).toBeGreaterThan(0);
  });

  it("settles over a few frames when motion is allowed, then stops", async () => {
    setup(false);
    const frames: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
    vi.stubGlobal("cancelAnimationFrame", () => {});
    draw();
    const first = nodeOf("Garrick").getAttribute("transform");
    await act(async () => {
      for (let i = 0; i < 400 && frames.length; i += 1) frames.shift()!(0);
    });
    await waitFor(() => expect(nodeOf("Garrick").getAttribute("transform")).not.toBe(first));
    expect(frames).toHaveLength(0);
  });
});

const withBox = () =>
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, right: 600, bottom: 600, width: 600, height: 600, x: 0, y: 0, toJSON() {} } as DOMRect);
const drag = (el: Element, to: [number, number]) => {
  fireEvent.pointerDown(el, { clientX: 300, clientY: 300, pointerId: 1 });
  fireEvent.pointerMove(window, { clientX: 300 + to[0] / 2, clientY: 300 + to[1] / 2, pointerId: 1 });
  fireEvent.pointerMove(window, { clientX: 300 + to[0], clientY: 300 + to[1], pointerId: 1 });
  fireEvent.pointerUp(window, { pointerId: 1 });
};

describe("moving a name by hand", () => {
  afterEach(() => vi.restoreAllMocks());

  it("a drag moves the name, pins it, remembers it, and is not taken for a tap", async () => {
    withBox();
    const onCenter = vi.fn();
    draw({ onCenter });
    const before = nodeOf("Garrick").getAttribute("transform");
    drag(nodeOf("Garrick"), [150, 90]);
    expect(nodeOf("Garrick").getAttribute("transform")).not.toBe(before);
    expect(nodeOf("Garrick").querySelector(".web-pin")).not.toBeNull();
    expect(readPlaces("1.a").g).toBeDefined();
    fireEvent.click(nodeOf("Garrick"));
    expect(onCenter).not.toHaveBeenCalled();
    await new Promise((r) => setTimeout(r, 5));
    fireEvent.click(nodeOf("Garrick")); // (the first real click picks)
    expect(onCenter).not.toHaveBeenCalled();
    fireEvent.click(nodeOf("Garrick")); // (the second is the double click)
    expect(onCenter).toHaveBeenCalledWith("g");
  });

  it("a small movement is a tap, not a drag", () => {
    withBox();
    draw();
    fireEvent.pointerDown(nodeOf("Garrick"), { clientX: 300, clientY: 300, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 302, clientY: 301, pointerId: 1 });
    fireEvent.pointerUp(window, { pointerId: 1 });
    expect(nodeOf("Garrick").querySelector(".web-pin")).toBeNull();
    expect(readPlaces("1.a")).toEqual({});
  });

  it("moves at once, with no animation frame asked for, when less motion is wanted", () => {
    withBox();
    const ask = vi.fn(() => 0);
    vi.stubGlobal("requestAnimationFrame", ask);
    draw();
    drag(nodeOf("Garrick"), [150, 90]);
    fireEvent.click(document.querySelector(".web-tools button:last-of-type")!);
    expect(ask).not.toHaveBeenCalled();
  });

  it("keeps the places of moved names that are not on screen right now", () => {
    savePlaces("1.a", { gone: [1, 2] });
    draw();
    fireEvent.keyDown(nodeOf("Garrick"), { key: "ArrowRight" });
    expect(readPlaces("1.a").gone).toEqual([1, 2]);
    expect(readPlaces("1.a").g).toBeDefined();
  });
});

describe("keeping the map calm", () => {
  afterEach(() => vi.restoreAllMocks());

  it("does not settle again when the parent draws the same names and lines in new arrays", () => {
    setup(false);
    const ask = vi.fn(() => 1);
    vi.stubGlobal("requestAnimationFrame", ask);
    vi.stubGlobal("cancelAnimationFrame", () => {});
    const props = { center: "m", refId: "1.a", selected: null, onCenter: () => {}, onLine: () => {} };
    const { rerender } = render(<WebMap nodes={nodes} edges={edges} {...props} />);
    const first = ask.mock.calls.length;
    rerender(<WebMap nodes={[...nodes]} edges={edges.map((e) => ({ ...e }))} {...props} selected={{ a: "m", b: "g" }} />);
    expect(ask.mock.calls.length).toBe(first);
  });

  it("forgets a name in focus that is no longer on the map, so nothing stays faded", () => {
    const props = { center: "m", refId: "1.a", selected: null, onCenter: () => {}, onLine: () => {} };
    const { rerender } = render(<WebMap nodes={nodes} edges={edges} {...props} />);
    fireEvent.pointerEnter(nodeOf("Far Hall"));
    expect(document.querySelectorAll(".web-faded").length).toBeGreaterThan(0);
    rerender(<WebMap nodes={nodes.filter((n) => n.id !== "h")} edges={edges.filter((e) => e.b !== "h")} {...props} />);
    expect(document.querySelectorAll(".web-faded")).toHaveLength(0);
  });

  it("makes only the lines of the name in focus keyboard stops", () => {
    draw();
    const stops = () => [...document.querySelectorAll(".web-hit")].filter((l) => (l.getAttribute("tabindex") ?? l.getAttribute("tabIndex")) === "0");
    expect(stops()).toHaveLength(0);
    fireEvent.focus(nodeOf("Far Hall"));
    expect(stops()).toHaveLength(1);
  });

  it("says so and turns the layout choice off for a web too big to move around", () => {
    const many = Array.from({ length: MAX_SIM_NAMES + 1 }, (_, i) => ({ id: `n${i}`, name: `N${i}`, kind: "npc" }));
    render(<WebMap nodes={many} edges={[]} center="n0" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
    const group = document.querySelector(`[role="group"][aria-label="${words.layoutLabel}"]`)!;
    for (const b of group.querySelectorAll("button")) expect((b as HTMLButtonElement).disabled).toBe(true);
    expect(document.body.textContent).toContain(words.layoutTooBig);
  });
});


describe("the details beside the map", () => {
  it("shows the middle name's connections in words, then the name in focus's, and a secret is marked", () => {
    const withSecret: WebEdge[] = [{ ...edges[0]!, links: [link("l1", "serves"), { ...link("l2", "owes"), secret: true }] }, ...edges.slice(1)];
    render(<WebMap nodes={nodes} edges={withSecret} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
    const side = () => document.querySelector(".web-side")!;
    expect(side().querySelector("h3")?.textContent).toBe("Mira Voss");
    expect(side().textContent).toContain("Mira Voss serves Garrick");
    expect(side().textContent).toContain("Mira Voss lives at Far Hall");
    expect(side().textContent).toContain(words.secretMark);
    fireEvent.pointerEnter(nodeOf("The Order"));
    expect(side().querySelector("h3")?.textContent).toBe("The Order");
    expect(side().textContent).toContain("Garrick leads The Order");
    expect(side().textContent).not.toContain("serves");
  });

  it("keeps a name in place and lets it go again, from a button, for the keyboard", () => {
    draw();
    fireEvent.pointerEnter(nodeOf("Garrick"));
    const button = () => document.querySelector(".web-side button") as HTMLButtonElement;
    expect(button().textContent).toBe(words.keepHere);
    fireEvent.click(button());
    expect(nodeOf("Garrick").querySelector(".web-pin")).not.toBeNull();
    expect(readPlaces("1.a").g).toBeDefined();
    expect(button().textContent).toBe(words.letMove);
    fireEvent.click(button());
    expect(nodeOf("Garrick").querySelector(".web-pin")).toBeNull();
    expect(readPlaces("1.a").g).toBeUndefined();
  });
});

const kinded: WebEdge[] = [
  { a: "m", b: "g", label: "serves", more: 0, links: [{ ...link("l1", "serves"), kind: "work" }] },
  { a: "m", b: "h", label: "kin of", more: 0, links: [{ ...link("l3", "kin of"), kind: "family" }] },
  { a: "g", b: "o", label: "leads", more: 0, links: [{ ...link("l4", "leads"), kind: "group" }] },
];
const drawKinded = () => render(<WebMap nodes={nodes} edges={kinded} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);

describe("kinds of connection", () => {
  it("gives each line the class of its kind, and mixed or missing kinds the neutral one", () => {
    drawKinded();
    const kinds = [...document.querySelectorAll(".web-edge")].map((l) => [...l.classList].find((c) => c.startsWith("web-link-")));
    expect(kinds).toEqual(["web-link-work", "web-link-family", "web-link-group"]);
    cleanup();
    draw();
    expect([...document.querySelectorAll(".web-edge")].every((l) => l.classList.contains("web-link-other"))).toBe(true);
  });

  it("has a chip for each kind that is there, on by default, and no chips without kinds", () => {
    drawKinded();
    const chips = [...document.querySelectorAll(".web-chip")] as HTMLButtonElement[];
    expect(chips.map((c) => c.textContent?.trim())).toEqual(["Family", "Clubs and groups", "Work"]);
    expect(chips.every((c) => c.getAttribute("aria-pressed") === "true")).toBe(true);
  });

  it("hides the lines of a kind when its chip is turned off, and brings them back", () => {
    drawKinded();
    const chip = (label: string) => [...document.querySelectorAll(".web-chip")].find((c) => c.textContent?.includes(label)) as HTMLButtonElement;
    fireEvent.click(chip("Work"));
    expect(chip("Work").getAttribute("aria-pressed")).toBe("false");
    expect(document.querySelectorAll(".web-edge.web-link-work")).toHaveLength(0);
    expect(document.querySelectorAll(".web-edge")).toHaveLength(2);
    fireEvent.click(chip("Work"));
    expect(document.querySelectorAll(".web-edge.web-link-work")).toHaveLength(1);
  });

  it("marks each connection with its kind's word and a dot, not by colour alone", () => {
    drawKinded();
    const side = document.querySelector(".web-side")!;
    expect(side.textContent).toContain("Work: ");
    expect(side.textContent).toContain("Family: ");
    expect(side.querySelectorAll(".web-dot")).toHaveLength(2);
    expect(document.querySelector(".web-legend")!.textContent).toContain("Work");
  });

  it("labels the grouped areas with what the names are, never with the kinds of connection (#2990)", () => {
    saveMode("groups");
    drawKinded();
    const labels = [...document.querySelectorAll(".web-area-text")].map((t) => t.textContent);
    // The pretend names are NPCs, a place and a group; each kind present has one area, each with the Names page's words.
    expect(labels).toEqual(expect.arrayContaining(["NPCs", "Places"]));
    expect(labels.every((l) => !["Family", "Work", "Something else", "Friends and allies"].includes(l ?? ""))).toBe(true);
    expect(new Set(labels).size).toBe(labels.length); // an empty kind has no area, a kind has one
    // The lines keep their own colour and the chips are still there.
    expect(document.querySelector(".web-legend")!.textContent).toContain("Work");
  });

  it("gives no area to a kind that has no names, and keeps the middle name out of every area", () => {
    saveMode("groups");
    drawKinded();
    const labels = [...document.querySelectorAll(".web-area-text")].map((t) => t.textContent);
    expect(labels).not.toContain("Items");
    expect(labels).not.toContain("Events");
    expect(document.querySelectorAll(".web-node.centre")).toHaveLength(1);
  });
});

describe("kinds of connection, edge cases", () => {
  it("treats a kind this page does not know as the neutral one", () => {
    expect(edgeKind({ links: [{ kind: "rival" }] })).toBe("other");
    expect(edgeKind({ links: [{ kind: "rival" }, { kind: "work" }] })).toBe("other");
  });

  it("lights no name through a line that is hidden", () => {
    drawKinded();
    fireEvent.click([...document.querySelectorAll(".web-chip")].find((c) => c.textContent?.includes("Work")) as HTMLButtonElement);
    fireEvent.pointerEnter(nodeOf("Mira"));
    // Garrick is joined to Mira only by the hidden work line, so he fades with the rest.
    expect(nodeOf("Garrick").classList.contains("web-faded")).toBe(true);
    expect(nodeOf("Far Hall").classList.contains("web-faded")).toBe(false);
    expect(document.querySelector(".web-side")!.textContent).not.toContain("serves");
  });

  it("settles again when a connection's kind changes with the same number of lines", () => {
    setup(false);
    const ask = vi.fn(() => 1);
    vi.stubGlobal("requestAnimationFrame", ask);
    vi.stubGlobal("cancelAnimationFrame", () => {});
    const props = { center: "m", refId: "1.a", selected: null, onCenter: () => {}, onLine: () => {} };
    const { rerender } = render(<WebMap nodes={nodes} edges={kinded} {...props} />);
    const first = ask.mock.calls.length;
    const changed = kinded.map((e, i) => (i === 0 ? { ...e, links: [{ ...e.links[0]!, kind: "family" as const }] } : e));
    rerender(<WebMap nodes={nodes} edges={changed} {...props} />);
    expect(ask.mock.calls.length).toBeGreaterThan(first);
  });

  it("says a hidden kind is hidden to a screen reader", () => {
    drawKinded();
    const chip = [...document.querySelectorAll(".web-chip")].find((c) => c.textContent?.includes("Work")) as HTMLButtonElement;
    expect(chip.textContent).not.toContain("(hidden)");
    fireEvent.click(chip);
    expect(chip.textContent).toContain(words.hiddenMark);
  });
});

describe("full screen (#2981)", () => {
  const button = (name: string) => [...document.querySelectorAll(".web-tools button")].find((b) => b.textContent === name) as HTMLButtonElement;

  it("fills the window on request, says so, and is left by the button or Escape", () => {
    draw();
    const map = document.querySelector(".web-map")!;
    expect(map.classList.contains("web-full")).toBe(false);
    expect(button(words.fullScreen).getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(button(words.fullScreen));
    expect(map.classList.contains("web-full")).toBe(true);
    expect(button(words.leaveFullScreen).getAttribute("aria-pressed")).toBe("true");
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.click(button(words.leaveFullScreen));
    expect(map.classList.contains("web-full")).toBe(false);
    expect(document.body.style.overflow).toBe("");
    fireEvent.click(button(words.fullScreen));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(map.classList.contains("web-full")).toBe(false);
    expect(document.body.style.overflow).toBe("");
  });

  it("draws the chosen line's panel inside the map, so full screen still shows it", () => {
    draw({ children: <section class="web-line-panel">The line</section> });
    fireEvent.click(button(words.fullScreen));
    const map = document.querySelector(".web-map.web-full")!;
    expect(map.querySelector(".web-line-panel")?.textContent).toBe("The line");
  });

  it("gives the page back its scrolling when the map goes away while full screen", () => {
    const { unmount } = draw();
    fireEvent.click(button(words.fullScreen));
    unmount();
    expect(document.body.style.overflow).toBe("");
  });

  it("keeps the wide page and the details under the map in the style sheet", () => {
    // jsdom lays nothing out, so the width rules are read from the file that the page loads.
    const css = readFileSync(resolve(process.cwd(), "src/styles/web-map.css"), "utf8");
    expect(css).toMatch(/body:has\(\.account-wide\) \.wrap \{\s*max-width: 110rem/);
    expect(css).toMatch(/\.account\.account-wide \{\s*max-width: none/);
    // The details panel never goes beside the map: one column at every width (owner, #2984).
    expect(css).not.toMatch(/grid-template-columns: minmax\(0, 1fr\) 19rem/);
    const page = readFileSync(resolve(process.cwd(), "src/pages/account/web.astro"), "utf8");
    expect(page).toContain("account account-wide");
  });
});

describe("each kind of name has its own colour, shape and word (#2992)", () => {
  const kinds = ["npc", "player_character", "place", "faction", "item", "event", "creature", "quest", "concept"];
  const many: WebNode[] = [
    { id: "c0", name: "Centre", kind: "npc" },
    ...kinds.slice(1).map((kind, i) => ({ id: `k${i}`, name: `Name ${kind}`, kind })),
  ];
  const wired: WebEdge[] = many.slice(1).map((n, i) => ({ a: "c0", b: n.id, label: "x", more: 0, links: [link(`w${i}`, "x")] }));
  const css = () => readFileSync(resolve(process.cwd(), "src/styles/web-map.css"), "utf8");

  it("draws every style as its own class and its own shape", () => {
    render(<WebMap nodes={many} edges={wired} center="c0" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
    const style = (name: string) => [...nodeOf(name).classList].find((c) => c.startsWith("web-kind-"));
    expect(kinds.map((k) => style(k === "npc" ? "Centre" : `Name ${k}`))).toEqual([
      "web-kind-npc", "web-kind-player_character", "web-kind-place", "web-kind-group", "web-kind-item",
      "web-kind-event", "web-kind-creature", "web-kind-quest", "web-kind-other",
    ]);
    const shape = (name: string) => {
      const el = nodeOf(name).querySelector("circle:not(.web-hit), rect, polygon")!;
      return `${el.tagName}:${el.getAttribute("points")?.split(" ").length ?? ""}:${el.getAttribute("stroke-dasharray") ?? ""}:${nodeOf(name).querySelectorAll(".web-pc-ring").length}`;
    };
    const shapes = kinds.map((k) => shape(k === "npc" ? "Centre" : `Name ${k}`));
    expect(new Set(shapes).size).toBe(kinds.length); // colour is never the only signal
  });

  it("gives each kind a colour token for light and dark, and its legend a word, a shape and a colour", () => {
    for (const style of ["npc", "player_character", "place", "group", "item", "event", "creature", "quest", "other"]) {
      expect(css().match(new RegExp(`--kind-${style}:`, "g"))).toHaveLength(3); // light, dark by system, dark by choice
      expect(css()).toContain(`.web-kind-${style} { --kind: var(--kind-${style}); }`);
    }
    render(<WebMap nodes={many} edges={wired} center="c0" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
    const legend = document.querySelector(".web-legend")!;
    for (const word of ["NPCs", "PCs", "Places", "Groups", "Items", "Events", "Creatures", "Quests", "Ideas"]) expect(legend.textContent).toContain(word);
    expect(legend.querySelectorAll('svg[class^="web-kind-"]').length).toBe(kinds.length);
  });

  it("tints each kind's area with the colour of its names", () => {
    saveMode("groups");
    render(<WebMap nodes={many} edges={wired} center="c0" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
    const areas = [...document.querySelectorAll(".web-area")];
    expect(areas.length).toBeGreaterThan(0);
    for (const area of areas) {
      const label = area.querySelector(".web-area-text")!.textContent!;
      const names = [...document.querySelectorAll(".web-node")].filter((n) => n.classList.contains([...area.classList].find((c) => c.startsWith("web-kind-"))!));
      expect(names.length, label).toBeGreaterThan(0); // the wash is the colour of names that are there
    }
    expect(css()).toMatch(/\.web-area\[class\*="web-kind-"\] circle \{\s*fill: var\(--kind\);\s*fill-opacity: 0\.14/);
  });
});

describe("pinch to zoom (two fingers)", () => {
  afterEach(() => vi.restoreAllMocks());
  const scaleOf = () => Number(/scale\(([\d.]+)\)/.exec(document.querySelector(".web-svg > g")!.getAttribute("transform") ?? "")?.[1] ?? 1);
  const spread = (a: Element, b: Element) => {
    fireEvent.pointerDown(a, { clientX: 250, clientY: 300, pointerId: 1 });
    fireEvent.pointerDown(b, { clientX: 350, clientY: 300, pointerId: 2 });
    fireEvent.pointerMove(window, { clientX: 200, clientY: 300, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 400, clientY: 300, pointerId: 2 });
  };

  it("zooms in when two fingers on the empty space move apart", () => {
    withBox();
    draw();
    const svg = document.querySelector(".web-svg")!;
    expect(scaleOf()).toBe(1);
    spread(svg, svg);
    expect(scaleOf()).toBeGreaterThan(1.2);
    fireEvent.pointerUp(window, { pointerId: 1 });
    fireEvent.pointerUp(window, { pointerId: 2 });
  });

  it("zooms too when the first finger lands on a name, and the name is not dragged or pinned", () => {
    withBox();
    draw();
    const svg = document.querySelector(".web-svg")!;
    const before = nodeOf("Garrick").getAttribute("transform");
    spread(nodeOf("Garrick"), svg);
    expect(scaleOf()).toBeGreaterThan(1.2);
    fireEvent.pointerUp(window, { pointerId: 1 });
    fireEvent.pointerUp(window, { pointerId: 2 });
    expect(nodeOf("Garrick").querySelector(".web-pin")).toBeNull();
    expect(nodeOf("Garrick").getAttribute("transform")).toBe(before);
  });

  it("zooms out when the fingers come together, and one finger pans again afterwards", () => {
    withBox();
    draw();
    const svg = document.querySelector(".web-svg")!;
    spread(svg, svg);
    const zoomed = scaleOf();
    fireEvent.pointerMove(window, { clientX: 290, clientY: 300, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 310, clientY: 300, pointerId: 2 });
    expect(scaleOf()).toBeLessThan(zoomed);
    fireEvent.pointerUp(window, { pointerId: 1 });
    fireEvent.pointerUp(window, { pointerId: 2 });
    const was = document.querySelector(".web-svg > g")!.getAttribute("transform");
    fireEvent.pointerDown(svg, { clientX: 300, clientY: 300, pointerId: 3 });
    fireEvent.pointerMove(window, { clientX: 340, clientY: 310, pointerId: 3 });
    fireEvent.pointerUp(window, { pointerId: 3 });
    expect(document.querySelector(".web-svg > g")!.getAttribute("transform")).not.toBe(was);
  });
});

describe("Cluster by quest (#2992)", () => {
  const bell = { id: "q-bell", name: "Find the Lost Bell", links: 2 };
  const heist = { id: "q-heist", name: "The Keep Heist", links: 1 };
  const withQuests: WebNode[] = [
    { id: "m", name: "Mira Voss", kind: "npc", quests: [bell, heist] }, // in two: sits in the bell, the one with more links
    { id: "g", name: "Garrick", kind: "npc", quests: [heist] },
    { id: "h", name: "Far Hall", kind: "place", quests: [] }, // in none
    { id: "o", name: "The Order", kind: "group", quests: [bell] },
  ];
  const wired: WebEdge[] = [
    { a: "m", b: "g", label: "x", more: 0, links: [link("a", "x")] },
    { a: "m", b: "h", label: "x", more: 0, links: [link("b", "x")] },
    { a: "m", b: "o", label: "x", more: 0, links: [link("c", "x")] },
  ];
  const drawQuests = (list: WebNode[] = withQuests, center = "g") => {
    saveMode("quests");
    return render(<WebMap nodes={list} edges={wired} center={center} refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
  };
  const labels = () => [...document.querySelectorAll(".web-area-text")].map((t) => t.textContent);

  it("offers the third layout only when the door sent quests, and remembers it", () => {
    drawQuests();
    const group = document.querySelector(".web-tools")!;
    expect([...group.querySelectorAll("button")].map((b) => b.textContent)).toEqual(expect.arrayContaining([words.layoutFree, words.layoutGroups, words.layoutQuests]));
    expect(readMode()).toBe("quests");
    cleanup();
    // A player is sent no quests: no third button, and a saved "quests" is the spread layout for them.
    saveMode("quests");
    render(<WebMap nodes={nodes} edges={edges} center="m" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
    expect([...document.querySelectorAll(".web-tools button")].some((b) => b.textContent === words.layoutQuests)).toBe(false);
    expect(document.querySelectorAll(".web-area")).toHaveLength(0);
    expect(document.querySelector('.web-tools button[aria-pressed="true"]')!.textContent).toBe(words.layoutFree);
  });

  it("gives each quest a labelled area, a name in two quests the one with more links, and the rest a place of their own", () => {
    drawQuests(withQuests, "g"); // Garrick is the middle, so the others are drawn in areas
    expect(labels()).toEqual(expect.arrayContaining(["Find the Lost Bell", words.noQuest]));
    // Mira (bell, then heist) and the Order (bell) share the bell area; Far Hall is in no quest.
    const area = (label: string) => [...document.querySelectorAll(".web-area")].find((a) => a.querySelector(".web-area-text")!.textContent === label)!;
    expect(area("Find the Lost Bell").classList.contains("web-kind-quest")).toBe(true); // the quest colour, as a light wash
    expect(area(words.noQuest).classList.contains("web-kind-other")).toBe(true);
    expect(labels()).not.toContain("The Keep Heist"); // Garrick, the only one in the heist, is the middle name
  });

  it("says which other quests a name is in", () => {
    drawQuests(withQuests, "m"); // the panel shows the middle name, Mira, who is in the bell and the heist
    expect(document.querySelector(".web-side")!.textContent).toContain(words.alsoIn("The Keep Heist"));
    cleanup();
    drawQuests(withQuests, "g"); // Garrick is in one quest only: nothing to add
    expect(document.querySelector(".web-side")!.textContent).not.toContain("Also in");
  });

  it("puts a quest that is drawn in its own area, and names areas of quests that are not drawn", () => {
    const quest: WebNode = { id: "q-bell", name: "Find the Lost Bell", kind: "quest", quests: [] };
    const list = [...withQuests, quest];
    const lines: WebEdge[] = [...wired, { a: "m", b: "q-bell", label: "x", more: 0, links: [link("d", "x")] }];
    saveMode("quests");
    render(<WebMap nodes={list} edges={lines} center="g" refId="1.a" selected={null} onCenter={() => {}} onLine={() => {}} />);
    expect(labels().filter((l) => l === "Find the Lost Bell")).toHaveLength(1); // the drawn quest and its names share one area
  });
});
