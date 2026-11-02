/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Story Graph reads itself again every few seconds, and a button freezes and restarts that.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AccountApi, WebGraph } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Web from "../src/account/Web";
import { webPage as w } from "../src/content/web-words";

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

/** The pretend API, counting how often the web is read, and growing by one name from the second read on
 * when `grow` is set. */
function counting(grow: boolean): { api: AccountApi; reads: () => number } {
  const base = mockApi("table");
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
    expect(reads()).toBe(first + 1);
  });

  it("keeps quiet when a read fails, and tries again at the next turn", async () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    const base = mockApi("table");
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
