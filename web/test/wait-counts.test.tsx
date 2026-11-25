/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The half-second counts read on the lore upload page (#3534 part 3; core #5286, behind WAIT_COUNTS_LIVE): it asks again about
// twice a second only while a job runs and the tab is seen, stops when the tab is hidden or the job is over, never shows an
// older reading over a newer look, shows its numbers sooner and nothing else, and does nothing while its switch is off.
import { act, cleanup, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type AccountApi, ApiError, type LoreUpload, type WaitCountsLore } from "../src/account/api";
import Lore from "../src/account/Lore";
import { mockApi } from "../src/account/mock";
import { COUNTS_EVERY_MS, useWaitCounts } from "../src/account/useWaitCounts";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/wait-counts-switch", () => ({
  get WAIT_COUNTS_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
  vi.useRealTimers();
  Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
});

const ID = "0".repeat(31) + "1";
const running: LoreUpload = { id: ID, state: "working", stage: "reading", error: "", source: "paste", choice: "world", namesFound: 0, linksFound: 0, done: 2, total: 10, createdAt: 100, updatedAt: 110, serverNow: 112 };
const reading = (over: Partial<WaitCountsLore> = {}): WaitCountsLore => ({ kind: "lore", state: "working", stage: "reading", done: 5, total: 10, wordsDone: 4200, callsMade: 3, namesFound: 0, linksFound: 0, createdAt: 100, updatedAt: 111, serverNow: 113, ...over });

function Probe({ api, active }: { api: AccountApi; active: boolean }) {
  const got = useWaitCounts(api, "100.cmp1", "lore", ID, active);
  return <p data-testid="words">{got ? String(got.wordsDone) : "none"}</p>;
}

describe("the half-second counts read", () => {
  it("asks again after half a second, one at a time, and stops when the job is over", async () => {
    vi.useFakeTimers();
    let looks = 0;
    const api = mockApi("table");
    api.waitCounts = async () => {
      looks += 1;
      return reading({ wordsDone: looks * 100, state: looks >= 3 ? "done" : "working" });
    };
    render(<Probe api={api} active />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(looks).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(COUNTS_EVERY_MS * 5);
    });
    expect(looks).toBe(3); // (the third said done: no more asking)
    expect(screen.getByTestId("words").textContent).toBe("300");
  });

  it("does not ask at all for a job that is not running, or while its switch is off", async () => {
    vi.useFakeTimers();
    const api = mockApi("table");
    const ask = vi.fn(async () => reading());
    api.waitCounts = ask;
    render(<Probe api={api} active={false} />);
    live.on = false;
    cleanup();
    render(<Probe api={api} active />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(COUNTS_EVERY_MS * 4);
    });
    expect(ask).not.toHaveBeenCalled();
    expect(screen.getByTestId("words").textContent).toBe("none");
  });

  it("stops while the tab is hidden and starts again when it is seen", async () => {
    vi.useFakeTimers();
    const ask = vi.fn(async () => reading());
    const api = mockApi("table");
    api.waitCounts = ask;
    render(<Probe api={api} active />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(COUNTS_EVERY_MS + 10);
    });
    const before = ask.mock.calls.length;
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(COUNTS_EVERY_MS * 6);
    });
    expect(ask.mock.calls.length).toBeLessThanOrEqual(before + 1); // (at most the look already on its way)
    const hiddenCount = ask.mock.calls.length;
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(COUNTS_EVERY_MS + 10);
    });
    expect(ask.mock.calls.length).toBeGreaterThan(hiddenCount);
  });

  it("stops for good when a read fails, leaving the slower look to carry on", async () => {
    vi.useFakeTimers();
    const ask = vi.fn(async () => {
      throw new ApiError("not-found");
    });
    const api = mockApi("table");
    api.waitCounts = ask;
    render(<Probe api={api} active />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(COUNTS_EVERY_MS * 6);
    });
    expect(ask).toHaveBeenCalledTimes(1);
  });
});

describe("on the lore page", () => {
  const path = "/account/lore/100.cmp1";

  it("shows the words and calls from the quick reading, and the numbers only", async () => {
    const api = mockApi("table");
    api.loreRead = async () => running;
    api.waitCounts = async () => reading();
    render(<Lore api={api} path={path} />);
    expect(await screen.findByText(/4,200 words/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("Ashen");
  });

  it("shows no quick numbers while its switch is off", async () => {
    live.on = false;
    const api = mockApi("table");
    api.loreRead = async () => running;
    const ask = vi.fn(async () => reading());
    api.waitCounts = ask;
    render(<Lore api={api} path={path} />);
    await waitFor(() => expect(document.querySelector(".work-progress")).toBeTruthy());
    expect(ask).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toMatch(/4,200/);
  });

  it("ignores a quick reading older than the last full look", async () => {
    const api = mockApi("table");
    api.loreRead = async () => running;
    api.waitCounts = async () => reading({ updatedAt: 50, wordsDone: 9999 });
    render(<Lore api={api} path={path} />);
    await waitFor(() => expect(document.querySelector(".work-progress")).toBeTruthy());
    await new Promise((ok) => setTimeout(ok, COUNTS_EVERY_MS + 100));
    expect(document.body.textContent).not.toMatch(/9,999/);
  });
});
