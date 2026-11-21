/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Plot threads panel on the Names page (#3696, slice 1): read only, DM only, behind PLOT_THREADS_LIVE. Every word the answer carries is
// shown as core sent it; a player or a refusal draws nothing at all.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type PlotThread, type PlotThreads } from "../src/account/api";
import PlotThreadsPanel from "../src/account/PlotThreads";
import { mockApi } from "../src/account/mock";
import { plotThreadsWords as w } from "../src/content/plot-threads-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/plot-threads-switch", () => ({
  get PLOT_THREADS_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

const REF = "100.cmp1";

/** A thread with nothing in it, so each test adds only what it checks. */
function thread(over: Partial<PlotThread> = {}): PlotThread {
  return {
    id: "a1".repeat(16),
    name: "The drowned bell",
    kind: "thread",
    state: "going",
    stateWords: "Happening now",
    history: [],
    involved: [],
    movers: [],
    cameFrom: [],
    ledTo: [],
    promise: null,
    quiet: 0,
    stalled: false,
    ...over,
  };
}
function answer(over: Partial<PlotThreads> = {}, threads: PlotThread[] = [thread()]): PlotThreads {
  return { after: 3, threads, stalledCount: 0, total: threads.length, ...over };
}
/** The page with the given answer, once it has loaded. */
async function open(got: PlotThreads | (() => Promise<PlotThreads>)) {
  const api = mockApi("table");
  api.plotThreads = typeof got === "function" ? got : async () => got;
  render(<PlotThreadsPanel api={api} refId={REF} />);
  return api;
}

describe("the Plot threads panel (#3696, slice 1)", () => {
  it("draws nothing while PLOT_THREADS_LIVE is off, and makes no read", async () => {
    live.on = false;
    const api = mockApi("table");
    const { container } = render(<PlotThreadsPanel api={api} refId={REF} />);
    expect(container.textContent).toBe("");
    expect(api.calls.some((c) => c.startsWith("plotThreads:"))).toBe(false);
  });

  it("says loading first, then shows the heading and the intro", async () => {
    await open(answer());
    expect(screen.getByRole("status").textContent).toBe(w.loading);
    expect(await screen.findByRole("heading", { name: w.heading })).toBeTruthy();
    expect(screen.getByText(w.intro)).toBeTruthy();
  });

  it("says so when the read fails", async () => {
    await open(async () => {
      throw new ApiError("network");
    });
    expect((await screen.findByRole("alert")).textContent).toBe(w.failed);
  });

  it("says there are no threads yet", async () => {
    await open(answer({ total: 0 }, []));
    expect(await screen.findByText(w.none)).toBeTruthy();
  });

  it("a refused read (a player, or another campaign's DM) draws nothing and is not an error", async () => {
    const api = mockApi("table");
    let asked = false;
    api.plotThreads = async () => {
      asked = true;
      throw new ApiError("not-allowed");
    };
    const { container } = render(<PlotThreadsPanel api={api} refId={REF} />);
    await waitFor(() => expect(asked).toBe(true));
    await waitFor(() => expect(container.textContent).toBe(""));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says how many are stalled, and the badge with how long they have been quiet", async () => {
    await open(
      answer({ stalledCount: 1 }, [thread({ name: "The missing courier", state: "hinted", stateWords: "Hinted", stalled: true, quiet: 3 })]),
    );
    expect(await screen.findByText("1 thread hasn't moved for 3 or more sessions.")).toBeTruthy();
    expect(screen.getByText(w.quiet(3), { exact: false })).toBeTruthy();
  });

  it("uses the stalled line the answer sends ready-made, not the one it would build", async () => {
    await open(answer({ stalledCount: 2, stalledLine: "2 threads haven't moved for 3 or more sessions, as core words it." }, [thread()]));
    expect(await screen.findByText("2 threads haven't moved for 3 or more sessions, as core words it.")).toBeTruthy();
  });

  it("builds the plural stalled line when the answer does not send one", async () => {
    await open(answer({ stalledCount: 2 }, [thread()]));
    expect(await screen.findByText("2 threads haven't moved for 3 or more sessions.")).toBeTruthy();
  });

  it("a thread shows its people with roles, the events that moved it, and its history behind a toggle", async () => {
    await open(
      answer({}, [
        thread({
          history: ["Open (Session 2)", "checked, still going (Session 4)"],
          involved: [
            { id: "p1", name: "Cerric", role: "quest giver" },
            { id: "p2", name: "Tavin", role: "" },
          ],
          movers: [
            { id: "p1", name: "Cerric", endsIt: false, session: 4 },
            { id: "p2", name: "Tavin", endsIt: true, session: 5 },
          ],
        }),
      ]),
    );
    const card = (await screen.findByText("The drowned bell")).closest("article") as HTMLElement;
    expect(within(card).getByText(`${w.involved} Cerric (quest giver), Tavin`)).toBeTruthy();
    expect(within(card).getByText(`${w.movedBy} Cerric, ${w.endedIt("Tavin")}`)).toBeTruthy();
    const toggle = within(card).getByRole("button", { name: w.seeHistory });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // The list is always in the page, so the toggle's aria-controls points at a real element, open or shut.
    const listId = toggle.getAttribute("aria-controls");
    expect(listId).toMatch(/^plot-history-/);
    expect(document.getElementById(listId!)).not.toBeNull();
    fireEvent.click(toggle);
    expect(within(card).getByRole("button", { name: w.hideHistory }).getAttribute("aria-expanded")).toBe("true");
    expect(within(card).getByText("Open (Session 2)")).toBeTruthy();
    expect(document.getElementById(listId!)!.hidden).toBe(false);
  });

  it("a promise shows made by, made to and due, and a line with nothing in it is not shown", async () => {
    await open(
      answer({}, [
        thread({
          name: "Mira's word",
          kind: "promise",
          state: "open",
          stateWords: "Promised",
          promise: { by: ["Mira"], to: ["Guard 1"], due: "before the next full moon" },
        }),
      ]),
    );
    const card = (await screen.findByText("Mira's word")).closest("article") as HTMLElement;
    expect(within(card).getByText(`${w.madeBy} Mira`)).toBeTruthy();
    expect(within(card).getByText(`${w.madeTo} Guard 1`)).toBeTruthy();
    expect(within(card).getByText(`${w.due} before the next full moon`)).toBeTruthy();
    expect(within(card).queryByText(w.involved)).toBeNull();
    expect(within(card).queryByText(w.cameFrom)).toBeNull();
  });

  it("a thread with no state says so", async () => {
    await open(answer({}, [thread({ name: "The hooded stranger", kind: "mystery", state: null, stateWords: "" })]));
    const card = (await screen.findByText("The hooded stranger")).closest("article") as HTMLElement;
    expect(within(card).getByText(w.noState)).toBeTruthy();
    expect(within(card).getByText("· mystery", { selector: "span" })).toBeTruthy();
  });

  it("Came from and Led to name at most five, then say how many more", async () => {
    const names = ["One", "Two", "Three", "Four", "Five", "Six", "Seven"].map((name, i) => ({ id: `n${i}`, name }));
    await open(answer({}, [thread({ cameFrom: names, ledTo: names.slice(0, 2) })]));
    const card = (await screen.findByText("The drowned bell")).closest("article") as HTMLElement;
    expect(within(card).getByText(`${w.cameFrom} One, Two, Three, Four, Five ${w.more(2)}`)).toBeTruthy();
    expect(within(card).getByText(`${w.ledTo} One, Two`)).toBeTruthy();
  });

  it("says when the list is cut at 100 of the total", async () => {
    const many = Array.from({ length: 100 }, (_, i) => thread({ id: `t${String(i).padStart(3, "0")}`.padEnd(32, "0"), name: `Thread ${i}` }));
    await open(answer({ total: 150 }, many));
    expect(await screen.findByText(w.showing(150))).toBeTruthy();
    expect(screen.getByRole("region", { name: w.listLabel }).querySelectorAll("article")).toHaveLength(100);
  });

  it("draws nothing when the API has no plot-threads method (an older build)", async () => {
    const api = mockApi("table");
    delete api.plotThreads;
    const { container } = render(<PlotThreadsPanel api={api} refId={REF} />);
    expect(container.textContent).toBe("");
  });

  it("a sign-out hands over to the sign-in page and draws nothing more", async () => {
    const api = mockApi("table");
    api.plotThreads = async () => {
      throw new ApiError("signed-out");
    };
    const onSignedOut = vi.fn();
    render(<PlotThreadsPanel api={api} refId={REF} onSignedOut={onSignedOut} />);
    await waitFor(() => expect(onSignedOut).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a change of campaign drops the old campaign's threads until the new answer comes", async () => {
    const api = mockApi("table");
    api.plotThreads = async () => answer({}, [thread({ name: "Old campaign bell" })]);
    const { rerender } = render(<PlotThreadsPanel api={api} refId={REF} />);
    await screen.findByText("Old campaign bell");
    api.plotThreads = () => new Promise<PlotThreads>(() => {});
    rerender(<PlotThreadsPanel api={api} refId="200.cmp2" />);
    await waitFor(() => expect(screen.queryByText("Old campaign bell")).toBeNull());
    expect(screen.getByRole("status").textContent).toBe(w.loading);
  });

  it("a player's read is refused by the mock and draws nothing", async () => {
    const api = mockApi("table");
    const { container } = render(<PlotThreadsPanel api={api} refId="100.cmp1.web-player" />);
    await waitFor(() => expect(api.calls.some((c) => c.startsWith("plotThreads:"))).toBe(true));
    await waitFor(() => expect(container.textContent).toBe(""));
  });

  it("a read that runs for fifteen seconds says it is taking longer than usual", async () => {
    vi.useFakeTimers();
    try {
      const api = mockApi("table");
      api.plotThreads = () => new Promise<PlotThreads>(() => {});
      render(<PlotThreadsPanel api={api} refId={REF} />);
      expect(screen.queryByText(w.slow)).toBeNull();
      await vi.advanceTimersByTimeAsync(15000);
      expect(screen.getByText(w.slow)).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});
