/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Story Graph reads itself again every few seconds, and a button freezes and restarts that.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi, type WebGraph } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web, { autoDelay } from "../src/account/Web";
import { webPage as w, webPage as words } from "../src/content/web-words";

const PATH = "/account/web/100.cmp1";

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/** The pretend API without its version door: a turn then reads the whole graph, as it did before #3294. */
function withoutVersion(api: AccountApi): AccountApi {
  const copy = { ...api };
  delete copy.webVersion;
  return copy;
}

/** The pretend API, counting how often the web is read, and growing by one name from the second read on
 * when `grow` is set. */
function counting(grow: boolean): { api: AccountApi; reads: () => number } {
  // (without the version door: the turn then reads the whole graph, as it did before #3294)
  const base = withoutVersion(mockApi("table"));
  let reads = 0;
  const api: AccountApi = {
    ...base,
    async webGraph(ref, query): Promise<WebGraph> {
      reads += 1;
      const got = await base.webGraph!(ref, query);
      if (!grow || reads < 2) return got;
      return { ...got, nodes: [...got.nodes, { id: "late", name: "Late Arrival", kind: "npc" }] };
    },
  };
  return { api, reads: () => reads };
}

async function ready(api: AccountApi) {
  render(<Web api={api} path={PATH} />);
  await screen.findByText(/names?, \d+ lines?/);
}

const tick = async (ms: number): Promise<void> => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};

describe("reading itself again", () => {
  it("offers Freeze the graph at first and reads again about every 5 seconds", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    const { api, reads } = counting(false);
    await ready(api);
    expect(screen.getByRole("button", { name: w.freezeGraph })).toBeTruthy();
    expect(screen.getByText(w.updatingNote)).toBeTruthy();
    const first = reads();
    await tick(5400);
    expect(reads()).toBe(first); // (5.5 s with this jitter)
    await tick(300);
    expect(reads()).toBe(first + 1);
    await tick(5600);
    expect(reads()).toBe(first + 2);
  });

  it("waits a little more or less each time: between 5 and 6 seconds", async () => {
    // the first wait is the shortest possible, the one after it the longest
    vi.spyOn(Math, "random").mockReturnValueOnce(0).mockReturnValue(0.999);
    const { api, reads } = counting(false);
    await ready(api);
    const first = reads();
    await tick(5100);
    expect(reads()).toBe(first + 1); // the shortest wait is 5 s
    await tick(5100);
    expect(reads()).toBe(first + 1); // the longest wait is just under 6 s: not yet
    await tick(1000);
    expect(reads()).toBe(first + 2);
  });

  it("draws a name that arrives in the story without being asked", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { api } = counting(true);
    await ready(api);
    expect(screen.queryByText("Late Arrival")).toBeNull();
    await tick(5200);
    expect(screen.getAllByText("Late Arrival").length).toBeGreaterThan(0);
  });

  it("changes nothing on the page when the story has not changed", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { api, reads } = counting(false);
    await ready(api);
    const map = document.querySelector("svg[aria-label^='A map of']");
    await tick(5200);
    expect(reads()).toBeGreaterThan(1);
    expect(document.querySelector("svg[aria-label^='A map of']")).toBe(map);
  });
});

describe("the button", () => {
  it("freezes the graph and says so, then starts it again at once", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { api, reads } = counting(true);
    await ready(api);
    fireEvent.click(screen.getByRole("button", { name: w.freezeGraph }));
    expect(screen.getByRole("button", { name: w.autoUpdateGraph })).toBeTruthy();
    expect(screen.getByText(w.frozenNote)).toBeTruthy();
    const frozen = reads();
    await tick(20000);
    expect(reads()).toBe(frozen);
    expect(screen.queryByText("Late Arrival")).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: w.autoUpdateGraph }));
    });
    await tick(100);
    expect(reads()).toBe(frozen + 1); // it reads at once, not after the wait
    expect(screen.getAllByText("Late Arrival").length).toBeGreaterThan(0);
  });

  it("remembers a frozen graph in this browser", async () => {
    const { api } = counting(false);
    await ready(api);
    fireEvent.click(screen.getByRole("button", { name: w.freezeGraph }));
    cleanup();
    await ready(counting(false).api);
    expect(screen.getByRole("button", { name: w.autoUpdateGraph })).toBeTruthy();
  });

  it("still works when the browser will not keep the choice", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const { api } = counting(false);
    await ready(api);
    fireEvent.click(screen.getByRole("button", { name: w.freezeGraph }));
    expect(screen.getByRole("button", { name: w.autoUpdateGraph })).toBeTruthy();
  });
});

describe("when nobody is looking", () => {
  it("does not read while the tab is hidden, and reads at once when it comes back", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { api, reads } = counting(false);
    await ready(api);
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    const first = reads();
    await tick(20000);
    expect(reads()).toBe(first);
    hidden.mockReturnValue(false);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await tick(100);
    expect(reads()).toBeGreaterThanOrEqual(first + 1); // (at once; the next turn's timer may also fall due)
  });

  it("keeps quiet when a read fails, and tries again at the next turn", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const base = withoutVersion(mockApi("table"));
    let reads = 0;
    const api: AccountApi = {
      ...base,
      async webGraph(ref, query) {
        reads += 1;
        if (reads === 2) throw new Error("network");
        return base.webGraph!(ref, query);
      },
    };
    await ready(api);
    await tick(5200);
    expect(reads).toBe(2);
    expect(document.querySelector("svg[aria-label^='A map of']")).toBeTruthy();
    expect(screen.queryByRole("alert")?.textContent ?? "").toBe("");
    await tick(5200);
    expect(reads).toBe(3);
  });
});

describe("what a read may and may not do", () => {
  it("shows a change to a secret or a know word, not only new names", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const base = withoutVersion(mockApi("table"));
    let reads = 0;
    const api: AccountApi = {
      ...base,
      async webGraph(ref, query): Promise<WebGraph> {
        reads += 1;
        const got = await base.webGraph!(ref, query);
        if (reads < 2) return got;
        // the same names and lines, with one name turned secret in the meantime
        return { ...got, nodes: got.nodes.map((n, i) => (i === 0 ? { ...n, name: `${n.name}`, secret: true } : n)) };
      },
    };
    await ready(api);
    const marks = (): number => (document.body.textContent ?? "").split(words.secretMark).length - 1;
    const before = marks();
    await tick(5200);
    expect(reads).toBeGreaterThan(1);
    expect(marks()).toBeGreaterThan(before);
  });

  it("does not read a web that needs the big-web question, and does not draw it", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const base = withoutVersion(mockApi("table"));
    let big = false;
    let graphReads = 0;
    const api: AccountApi = {
      ...base,
      async webSize(ref, query) {
        const got = await base.webSize!(ref, query);
        return big ? { ...got, names: 99999 } : got;
      },
      async webGraph(ref, query) {
        graphReads += 1;
        return base.webGraph!(ref, query);
      },
    };
    await ready(api);
    const first = graphReads;
    big = true;
    await tick(5200);
    expect(graphReads).toBe(first); // asked the size only, read nothing
    expect(document.querySelector("svg[aria-label^='A map of']")).toBeTruthy();
  });

  it("tells a person who has been signed out, as any read would", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const base = withoutVersion(mockApi("table"));
    let reads = 0;
    const api: AccountApi = {
      ...base,
      async webGraph(ref, query) {
        reads += 1;
        if (reads === 2) throw new ApiError("signed-out");
        return base.webGraph!(ref, query);
      },
    };
    await ready(api);
    await tick(5200);
    expect(document.querySelector("svg[aria-label^='A map of']")).toBeNull();
  });

  it("drops a read that was in flight when the graph was frozen", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const base = withoutVersion(mockApi("table"));
    let reads = 0;
    let release: () => void = () => {};
    const api: AccountApi = {
      ...base,
      async webGraph(ref, query): Promise<WebGraph> {
        reads += 1;
        const got = await base.webGraph!(ref, query);
        if (reads < 2) return got;
        await new Promise<void>((resolve) => (release = resolve));
        return { ...got, nodes: [...got.nodes, { id: "late", name: "Late Arrival", kind: "npc" }] };
      },
    };
    await ready(api);
    await tick(5200); // the second read is now waiting
    fireEvent.click(screen.getByRole("button", { name: w.freezeGraph }));
    await act(async () => {
      release();
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(screen.queryByText("Late Arrival")).toBeNull();
  });
});

describe("backing off while nothing changes (#3294, until a graph version exists)", () => {
  it("waits 5 s for three quiet reads, then 15 s, 30 s and at most 60 s", () => {
    expect([0, 1, 2].map(autoDelay)).toEqual([5000, 5000, 5000]);
    expect([3, 4, 5, 6, 20].map(autoDelay)).toEqual([15000, 30000, 60000, 60000, 60000]);
  });

  it("slows down after three reads that found the same graph", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0); // no jitter: the waits are exact
    const { api, reads } = counting(false);
    await ready(api);
    const first = reads();
    await tick(5100 * 3); // three quiet reads, 5 s apart
    expect(reads()).toBe(first + 3);
    await tick(14000); // the next wait is 15 s: nothing yet
    expect(reads()).toBe(first + 3);
    await tick(1500);
    expect(reads()).toBe(first + 4);
    await tick(29000); // and then 30 s
    expect(reads()).toBe(first + 4);
    await tick(1500);
    expect(reads()).toBe(first + 5);
  });

  it("goes back to 5 s when the graph changes", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const base = withoutVersion(mockApi("table"));
    let reads = 0;
    let grown = false;
    const api: AccountApi = {
      ...base,
      async webGraph(ref, query): Promise<WebGraph> {
        reads += 1;
        const got = await base.webGraph!(ref, query);
        return grown ? { ...got, nodes: [...got.nodes, { id: "late", name: "Late Arrival", kind: "npc" }] } : got;
      },
    };
    await ready(api);
    await tick(5100 * 3 + 15500); // three quiet reads and the 15 s one: now waiting 30 s
    const before = reads;
    grown = true;
    await tick(30500); // the read after the 30 s wait sees the change
    expect(reads).toBe(before + 1);
    await tick(5100); // changed, so the next wait is 5 s again
    expect(reads).toBe(before + 2);
  });

  it("goes back to 5 s when the tab comes back", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { api, reads } = counting(false);
    await ready(api);
    await tick(5100 * 3 + 15500);
    const before = reads();
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    document.dispatchEvent(new Event("visibilitychange")); // a read at once, and the pace is quick again
    await tick(50);
    expect(reads()).toBe(before + 1);
    await tick(5100); // and the next one is 5 s later, not after the long wait that was running
    expect(reads()).toBe(before + 2);
  });

  it("does not count a hidden tab's turns as quiet", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { api, reads } = counting(false);
    await ready(api);
    Object.defineProperty(document, "hidden", { value: true, configurable: true });
    await tick(5100 * 6); // six turns pass with the tab hidden: no reads
    const hiddenReads = reads();
    Object.defineProperty(document, "hidden", { value: false, configurable: true });
    await tick(5100);
    expect(reads()).toBe(hiddenReads + 1); // still the quick pace once it is visible again
  });

  it("goes back to 5 s when the button is pressed", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { api, reads } = counting(false);
    await ready(api);
    await tick(5100 * 3 + 15500);
    fireEvent.click(screen.getByRole("button", { name: w.freezeGraph }));
    fireEvent.click(screen.getByRole("button", { name: w.autoUpdateGraph })); // frozen, then back on: reads at once
    await tick(50);
    const after = reads();
    await tick(5100);
    expect(reads()).toBe(after + 1); // the quick pace again, not 30 s
  });
});

describe("not redrawing under a hand (#3324)", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("a read is skipped while a name is being dragged and made after it is let go", async () => {
    // Reduced motion places the names at once, so there are names to take hold of in the test.
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce"), addEventListener() {}, removeEventListener() {} }));
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    const { api, reads } = counting(false);
    await ready(api);
    await tick(1000);
    const node = document.querySelector(".web-node") as SVGGElement;
    const before = reads();
    fireEvent.pointerDown(node, { clientX: 300, clientY: 300, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 340, clientY: 340, pointerId: 1 });
    await tick(11000);
    expect(reads()).toBe(before);
    fireEvent.pointerUp(window, { clientX: 340, clientY: 340, pointerId: 1 });
    await tick(6000);
    expect(reads()).toBeGreaterThan(before);
  });

  it("a read that was on its way when the drag began does not redraw the map under the hand", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce"), addEventListener() {}, removeEventListener() {} }));
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    const base = counting(false);
    // The web changes (a late name appears) from the read that is held onwards. The held read is held until the
    // test lets it go, so it is still in flight when the drag starts.
    let grown = false;
    let release: (() => void) | null = null;
    let holding = false;
    const late = (got: WebGraph): WebGraph => ({ ...got, nodes: [...got.nodes, { id: "late", name: "Late Arrival", kind: "npc" }] });
    const api: AccountApi = {
      ...base.api,
      async webGraph(ref, query) {
        if (!holding && base.reads() >= 2) {
          holding = true;
          const got = await base.api.webGraph!(ref, query);
          await new Promise<void>((resolve) => (release = resolve));
          return late(got);
        }
        const got = await base.api.webGraph!(ref, query);
        return grown ? late(got) : got;
      },
    };
    await ready(api);
    await tick(1000);
    await tick(12000); // the next reads run; the one after them is held
    expect(holding).toBe(true);
    fireEvent.pointerDown(document.querySelector(".web-node") as SVGGElement, { clientX: 300, clientY: 300, pointerId: 1 });
    fireEvent.pointerMove(window, { clientX: 340, clientY: 340, pointerId: 1 });
    grown = true;
    await act(async () => {
      release!();
    });
    await tick(100);
    expect(screen.queryByText("Late Arrival")).toBeNull();
    fireEvent.pointerUp(window, { clientX: 340, clientY: 340, pointerId: 1 });
    await tick(6000);
    expect(screen.getAllByText("Late Arrival").length).toBeGreaterThan(0);
  });
});

describe("the version number (#3294)", () => {
  /** A pretend API whose story has a version number the test sets, counting the full reads. */
  function versioned(): { api: AccountApi; reads: () => number; asked: () => number; story: { version: number }; viewerSeen: string[] } {
    const base = mockApi("table");
    const story = { version: 1 };
    const viewerSeen: string[] = [];
    let reads = 0;
    let asked = 0;
    const api: AccountApi = {
      ...base,
      async webVersion() {
        asked += 1;
        return { version: story.version };
      },
      async webGraph(ref, query): Promise<WebGraph> {
        reads += 1;
        viewerSeen.push(query.view ?? "");
        const got = await base.webGraph!(ref, query);
        return story.version === 1 ? got : { ...got, nodes: [...got.nodes, { id: "late", name: "Late Arrival", kind: "npc" }] };
      },
    };
    return { api, reads: () => reads, asked: () => asked, story, viewerSeen };
  }

  it("makes no full read at a turn when the number has not moved", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { api, reads, asked } = versioned();
    await ready(api);
    const first = reads();
    await tick(5200);
    await tick(5200);
    await tick(5200);
    expect(asked()).toBeGreaterThanOrEqual(2); // it did ask the number
    expect(reads()).toBe(first); // and never read the graph again
  });

  it("makes exactly one full read when the number moves, and draws what is new", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { api, reads, story } = versioned();
    await ready(api);
    await tick(5200); // (the first turn reads once to learn the number)
    const before = reads();
    story.version = 2;
    await tick(5200);
    expect(reads()).toBe(before + 1);
    expect(screen.getAllByText("Late Arrival").length).toBeGreaterThan(0);
    await tick(15200); // (and the same number again costs nothing)
    expect(reads()).toBe(before + 1);
  });

  it("reads the whole graph again when the viewer changes, then goes quiet for that viewer", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { api, reads, viewerSeen } = versioned();
    await ready(api);
    await tick(5200);
    const before = reads();
    fireEvent.click(screen.getByLabelText(w.playersView));
    await tick(300);
    expect(reads()).toBeGreaterThan(before);
    expect(viewerSeen).toContain("players");
    await tick(5200); // (one turn to learn the number for this viewer)
    const settled = reads();
    await tick(5200);
    await tick(5200);
    expect(reads()).toBe(settled);
  });

  it("falls back to a full read each turn when the number cannot be had", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const { api, reads } = versioned();
    api.webVersion = async () => {
      throw new ApiError("server");
    };
    await ready(api);
    const first = reads();
    await tick(5200);
    expect(reads()).toBe(first + 1);
  });
});
