// @vitest-environment happy-dom
// The relationship web's free layout and its map (#2934): names settle apart, can be moved and stay where
// they were dropped, the lines go quiet until a name is in focus, and the person's choices stay in this
// browser. The layout is checked as pure code (no clock, no random numbers) and the map as a rendered page.
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import WebMap from "../src/account/WebMap";
import type { WebEdge, WebNode } from "../src/account/api";
import { MAX_SIM_NAMES, MAX_SIM_STEPS, forceStep, kindAnchors, linkPairs, settle, startNodes } from "../src/account/webForce";
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

  it("pulls names of one kind together in the grouped layout", () => {
    const many = [
      ...Array.from({ length: 4 }, (_, i) => ({ id: `p${i}`, name: `P${i}`, kind: "npc" })),
      ...Array.from({ length: 4 }, (_, i) => ({ id: `q${i}`, name: `Q${i}`, kind: "place" })),
      { id: "c", name: "Centre", kind: "npc" },
    ];
    const some: WebEdge[] = many.slice(0, 8).map((n) => ({ a: "c", b: n.id, label: "knows", more: 0, links: [] }));
    const list = startNodes(many, some, "c");
    const links = linkPairs(list, some);
    settle(list, links, list.findIndex((n) => n.id === "c"), "groups", kindAnchors(["npc", "place"]));
    const mean = (kind: string) => {
      const m = list.filter((n) => n.kind === kind && n.id !== "c");
      return { x: m.reduce((t, n) => t + n.x, 0) / m.length, y: m.reduce((t, n) => t + n.y, 0) / m.length };
    };
    const within = (kind: string) => {
      const c = mean(kind);
      const m = list.filter((n) => n.kind === kind && n.id !== "c");
      return m.reduce((t, n) => t + Math.hypot(n.x - c.x, n.y - c.y), 0) / m.length;
    };
    const apart = Math.hypot(mean("npc").x - mean("place").x, mean("npc").y - mean("place").y);
    expect(apart).toBeGreaterThan(within("npc"));
    expect(apart).toBeGreaterThan(within("place"));
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
    // Each text holds its tooltip (the full words) first and what is drawn last.
    const texts = () => [...document.querySelectorAll(".web-edge-text")].map((t) => t.lastChild?.textContent);
    expect(texts()).toEqual(["2 connections"]);
    fireEvent.pointerEnter(nodeOf("Far Hall"));
    expect(texts()).toEqual(["2 connections", "lives at"]);
  });

  it("opens a line from the keyboard", () => {
    const onLine = vi.fn();
    draw({ onLine });
    const hit = document.querySelector(".web-hit") as SVGLineElement;
    expect(hit.getAttribute("aria-label")).toBe(words.lineBetween("Mira Voss", "Garrick"));
    fireEvent.keyDown(hit, { key: "Enter" });
    expect(onLine).toHaveBeenCalledWith("m", "g");
  });

  it("puts a name in the middle on Enter, and moves it with the arrow keys, keeping it there", () => {
    const onCenter = vi.fn();
    draw({ onCenter });
    const garrick = nodeOf("Garrick");
    const before = garrick.getAttribute("transform");
    fireEvent.keyDown(garrick, { key: "ArrowRight" });
    expect(nodeOf("Garrick").getAttribute("transform")).not.toBe(before);
    expect(nodeOf("Garrick").querySelector(".web-pin")).not.toBeNull();
    expect(readPlaces("1.a").g).toBeDefined();
    fireEvent.keyDown(nodeOf("Garrick"), { key: "Enter" });
    expect(onCenter).toHaveBeenCalledWith("g");
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

  it("switches the layout, remembers it, and labels the areas by kind", () => {
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
