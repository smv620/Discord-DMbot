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
    state: "active",
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

  it("a failed read says what to do next and offers Try again", async () => {
    await open(async () => {
      throw new ApiError("network");
    });
    expect((await screen.findByRole("alert")).textContent).toBe(w.failed);
    expect(screen.getByRole("button", { name: w.tryAgain })).toBeTruthy();
  });

  it("Try again reads again and draws the threads", async () => {
    const api = mockApi("table");
    let calls = 0;
    api.plotThreads = async () => {
      calls += 1;
      if (calls === 1) throw new ApiError("network");
      return answer();
    };
    render(<PlotThreadsPanel api={api} refId={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: w.tryAgain }));
    expect(await screen.findByText("The drowned bell")).toBeTruthy();
    expect(calls).toBe(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("Try again that fails again shows the failure line again", async () => {
    const api = mockApi("table");
    let calls = 0;
    api.plotThreads = async () => {
      calls += 1;
      throw new ApiError("network");
    };
    render(<PlotThreadsPanel api={api} refId={REF} />);
    fireEvent.click(await screen.findByRole("button", { name: w.tryAgain }));
    await waitFor(() => expect(calls).toBe(2));
    expect((await screen.findByRole("alert")).textContent).toBe(w.failed);
    expect(screen.getByRole("button", { name: w.tryAgain })).toBeTruthy();
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

// Change state (#4934, slice 2a): the five states per thread, Checked, still going on a live thread, and the press that waits on the bot.
describe("change a thread's state (#4934)", () => {
  async function withThreads(threads: PlotThread[]) {
    const api = mockApi("table");
    const reads = { n: 0 };
    api.plotThreads = async () => {
      reads.n += 1;
      return answer({}, threads);
    };
    const sent: { thread: string; state: string }[] = [];
    api.changePlotThreadState = async (_ref, change) => {
      sent.push(change);
    };
    render(<PlotThreadsPanel api={api} refId={REF} />);
    return { api, reads, sent };
  }
  /** The card of one thread, once the list has loaded. */
  async function card(name: string): Promise<HTMLElement> {
    return (await screen.findByText(name)).closest("article") as HTMLElement;
  }

  it("Change state offers the five states in a promise's own words for a promise", async () => {
    await withThreads([thread({ name: "Mira's word", kind: "promise", state: "open", stateWords: "Promised" })]);
    const c = await card("Mira's word");
    fireEvent.click(within(c).getByRole("button", { name: w.changeState }));
    for (const word of ["Made in passing", "Open", "Due soon", "Kept", "Broken or let go"]) {
      expect(within(c).getByRole("button", { name: word })).toBeTruthy();
    }
    expect(within(c).queryByRole("button", { name: "Happening now" })).toBeNull();
  });

  it("Checked, still going is offered only for a live thread", async () => {
    await withThreads([
      thread({ name: "The drowned bell", state: "active" }),
      thread({ id: "f1".repeat(16), name: "The old well", state: "resolved", stateWords: "Finished" }),
    ]);
    const live = await card("The drowned bell");
    fireEvent.click(within(live).getByRole("button", { name: w.changeState }));
    expect(within(live).getByRole("button", { name: w.checked })).toBeTruthy();
    const done = await card("The old well");
    fireEvent.click(within(done).getByRole("button", { name: w.changeState }));
    expect(within(done).queryByRole("button", { name: w.checked })).toBeNull();
  });

  it("pressing a state calls the API, says what it marked, and re-reads the list", async () => {
    const { reads, sent } = await withThreads([thread({ name: "The drowned bell", state: "active" })]);
    const c = await card("The drowned bell");
    fireEvent.click(within(c).getByRole("button", { name: w.changeState }));
    fireEvent.click(within(c).getByRole("button", { name: "Finished" }));
    expect(await screen.findByText(w.markedDone("Finished"))).toBeTruthy();
    expect(sent).toEqual([{ thread: "a1".repeat(16), state: "resolved" }]);
    await waitFor(() => expect(reads.n).toBe(2));
  });

  it("a thread that is gone says so and re-reads the list", async () => {
    const { api, reads } = await withThreads([thread({ name: "The drowned bell", state: "active" })]);
    api.changePlotThreadState = async () => {
      throw new ApiError("gone");
    };
    const c = await card("The drowned bell");
    fireEvent.click(within(c).getByRole("button", { name: w.changeState }));
    fireEvent.click(within(c).getByRole("button", { name: "Finished" }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.gone);
    await waitFor(() => expect(reads.n).toBe(2));
  });

  it("a failed press says nothing changed, and leaves the states open to try again", async () => {
    const { api } = await withThreads([thread({ name: "The drowned bell", state: "active" })]);
    api.changePlotThreadState = async () => {
      throw new ApiError("network");
    };
    const c = await card("The drowned bell");
    fireEvent.click(within(c).getByRole("button", { name: w.changeState }));
    fireEvent.click(within(c).getByRole("button", { name: "Finished" }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.couldNot);
    // The states are still there, so the DM can press again.
    expect(within(c).getByRole("button", { name: "Finished" })).toBeTruthy();
  });

  it("every state button waits while one press is with the bot", async () => {
    const { api, sent } = await withThreads([
      thread({ name: "The drowned bell", state: "active" }),
      thread({ id: "f2".repeat(16), name: "The old well", state: "open", stateWords: "Not started" }),
    ]);
    let release!: () => void;
    api.changePlotThreadState = (_ref, change) =>
      new Promise<void>((resolve) => {
        sent.push(change);
        release = resolve;
      });
    const bell = await card("The drowned bell");
    fireEvent.click(within(bell).getByRole("button", { name: w.changeState }));
    fireEvent.click(within(bell).getByRole("button", { name: "Finished" }));
    await waitFor(() => expect(sent).toHaveLength(1));
    const well = await card("The old well");
    expect((within(well).queryByRole("button", { name: w.changeState }) as HTMLButtonElement).disabled).toBe(true);
    release();
    expect(await screen.findByText(w.markedDone("Finished"))).toBeTruthy();
  });
});

// Add who's involved (#4939, slice 2b): a name and its part, from the campaign's names, like Discord's 🧵 card.
describe("add who's involved (#4939)", () => {
  const NAMES = [
    { id: "n-mira", text: "Mira", kind: "character", saidAs: null },
    { id: "n-hall", text: "Old Hall", kind: "place", saidAs: null },
    { id: "n-quest", text: "The Quest", kind: "quest", saidAs: null },
    { id: "n-person", text: "Someone", kind: "person", saidAs: null },
    { id: "n-deed", text: "A deed", kind: "deed", saidAs: null },
  ];
  /** A thread list that keeps what is added, so the re-read shows it. */
  async function withAdds(opts: { names?: boolean } = {}) {
    const api = mockApi("table");
    let threads = [thread({ name: "The drowned bell", state: "active" })];
    const reads = { n: 0 };
    api.plotThreads = async () => {
      reads.n += 1;
      return answer({}, threads);
    };
    api.campaignNames = async () => (opts.names === false ? [] : NAMES);
    const sent: { thread: string; who: string; role: string }[] = [];
    api.addPlotThreadInvolved = async (_ref, change) => {
      sent.push(change);
      threads = threads.map((t) =>
        t.id === change.thread ? { ...t, involved: [...t.involved, { id: change.who, name: "Mira", role: change.role }] } : t,
      );
    };
    render(<PlotThreadsPanel api={api} refId={REF} />);
    const c = (await screen.findByText("The drowned bell")).closest("article") as HTMLElement;
    return { api, reads, sent, c };
  }

  it("Add who's involved lists names that are not threads, people cards or deeds and narrows as you type", async () => {
    const { c } = await withAdds();
    fireEvent.click(within(c).getByRole("button", { name: w.addButton }));
    expect(await within(c).findByRole("button", { name: "Mira" })).toBeTruthy();
    expect(within(c).getByRole("button", { name: "Old Hall" })).toBeTruthy();
    expect(within(c).queryByRole("button", { name: "The Quest" })).toBeNull();
    expect(within(c).queryByRole("button", { name: "Someone" })).toBeNull();
    expect(within(c).queryByRole("button", { name: "A deed" })).toBeNull();
    fireEvent.input(within(c).getByLabelText(w.searchLabel), { target: { value: "hal" } });
    expect(within(c).getByRole("button", { name: "Old Hall" })).toBeTruthy();
    expect(within(c).queryByRole("button", { name: "Mira" })).toBeNull();
  });

  it("Add stays off until a name is picked", async () => {
    const { c } = await withAdds();
    fireEvent.click(within(c).getByRole("button", { name: w.addButton }));
    const add = await within(c).findByRole("button", { name: w.addSubmit });
    expect((add as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(await within(c).findByRole("button", { name: "Mira" }));
    expect((add as HTMLButtonElement).disabled).toBe(false);
  });

  it("a name can be added with no part, and a blank part is sent as empty", async () => {
    const { sent, c } = await withAdds();
    fireEvent.click(within(c).getByRole("button", { name: w.addButton }));
    fireEvent.click(await within(c).findByRole("button", { name: "Mira" }));
    fireEvent.input(within(c).getByLabelText(w.partLabel), { target: { value: "   " } });
    fireEvent.click(within(c).getByRole("button", { name: w.addSubmit }));
    expect(await screen.findByText(w.added)).toBeTruthy();
    expect(sent).toEqual([{ thread: "a1".repeat(16), who: "n-mira", role: "" }]);
  });

  it("a name with no part shows the name alone in the involved line", async () => {
    const api = mockApi("table");
    api.plotThreads = async () => answer({}, [thread({ name: "The drowned bell", involved: [{ id: "n-mira", name: "Mira", role: "" }] })]);
    render(<PlotThreadsPanel api={api} refId={REF} />);
    const c = (await screen.findByText("The drowned bell")).closest("article") as HTMLElement;
    expect(within(c).getByText(`${w.involved} Mira`)).toBeTruthy();
  });

  it("a name list that fails to load says so, not nothing called that yet, and loads again when reopened", async () => {
    const api = mockApi("table");
    api.plotThreads = async () => answer({}, [thread({ name: "The drowned bell" })]);
    let loads = 0;
    api.campaignNames = async () => {
      loads += 1;
      throw new ApiError("network");
    };
    render(<PlotThreadsPanel api={api} refId={REF} />);
    const c = (await screen.findByText("The drowned bell")).closest("article") as HTMLElement;
    fireEvent.click(within(c).getByRole("button", { name: w.addButton }));
    expect((await within(c).findByRole("alert")).textContent).toBe(w.namesFailed);
    expect(within(c).queryByText(w.noMatch)).toBeNull();
    // The toggle and the form both say Close; either closes it.
    fireEvent.click(within(c).getAllByRole("button", { name: w.closeChange })[0]!);
    fireEvent.click(within(c).getByRole("button", { name: w.addButton }));
    await waitFor(() => expect(loads).toBe(2));
  });

  it("a suggestion fills the part", async () => {
    const { c } = await withAdds();
    fireEvent.click(within(c).getByRole("button", { name: w.addButton }));
    fireEvent.click(await within(c).findByRole("button", { name: "villain" }));
    expect((within(c).getByLabelText(w.partLabel) as HTMLInputElement).value).toBe("villain");
  });

  it("adding calls the API, says Added, and the thread shows the new name", async () => {
    const { sent, c } = await withAdds();
    fireEvent.click(within(c).getByRole("button", { name: w.addButton }));
    fireEvent.click(await within(c).findByRole("button", { name: "Mira" }));
    fireEvent.input(within(c).getByLabelText(w.partLabel), { target: { value: "quest giver" } });
    fireEvent.click(within(c).getByRole("button", { name: w.addSubmit }));
    expect(await screen.findByText(w.added)).toBeTruthy();
    expect(sent).toEqual([{ thread: "a1".repeat(16), who: "n-mira", role: "quest giver" }]);
    expect(await within(c).findByText(`${w.involved} ${w.person("Mira", "quest giver")}`)).toBeTruthy();
  });

  it("a name that is gone says so and re-reads the list", async () => {
    const { api, reads, c } = await withAdds();
    api.addPlotThreadInvolved = async () => {
      throw new ApiError("gone");
    };
    fireEvent.click(within(c).getByRole("button", { name: w.addButton }));
    fireEvent.click(await within(c).findByRole("button", { name: "Mira" }));
    fireEvent.input(within(c).getByLabelText(w.partLabel), { target: { value: "target" } });
    fireEvent.click(within(c).getByRole("button", { name: w.addSubmit }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.addGone);
    await waitFor(() => expect(reads.n).toBe(2));
  });

  it("a failed add says nothing changed, and keeps the form open", async () => {
    const { api, c } = await withAdds();
    api.addPlotThreadInvolved = async () => {
      throw new ApiError("network");
    };
    fireEvent.click(within(c).getByRole("button", { name: w.addButton }));
    fireEvent.click(await within(c).findByRole("button", { name: "Mira" }));
    fireEvent.input(within(c).getByLabelText(w.partLabel), { target: { value: "target" } });
    fireEvent.click(within(c).getByRole("button", { name: w.addSubmit }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.couldNot);
    expect(within(c).getByLabelText(w.partLabel)).toBeTruthy();
  });

  it("the form waits while an add is with the bot", async () => {
    const { api, sent, c } = await withAdds();
    let release!: () => void;
    api.addPlotThreadInvolved = (_ref, change) =>
      new Promise<void>((resolve) => {
        sent.push(change);
        release = resolve;
      });
    fireEvent.click(within(c).getByRole("button", { name: w.addButton }));
    fireEvent.click(await within(c).findByRole("button", { name: "Mira" }));
    fireEvent.input(within(c).getByLabelText(w.partLabel), { target: { value: "target" } });
    fireEvent.click(within(c).getByRole("button", { name: w.addSubmit }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect((within(c).getByLabelText(w.partLabel) as HTMLInputElement).disabled).toBe(true);
    expect((within(c).getByRole("button", { name: w.addSubmit }) as HTMLButtonElement).disabled).toBe(true);
    release();
    expect(await screen.findByText(w.added)).toBeTruthy();
  });
});

// New story threads and promises (#4944, slice 2c): a thread of four kinds, a promise with its due note and parties.
describe("new story threads and promises (#4944)", () => {
  const PROMISE = thread({
    id: "p1".repeat(16),
    name: "Mira's word",
    kind: "promise",
    state: "open",
    stateWords: "Open",
    promise: { by: [], to: [], due: "By the full moon" },
  });
  const NAMES_44 = [
    { id: "n-mira", text: "Mira", kind: "character", saidAs: null },
    { id: "n-guild", text: "The Guild", kind: "faction", saidAs: null },
    { id: "n-hall", text: "Old Hall", kind: "place", saidAs: null },
  ];
  /** A list of two threads and a server that keeps what is started, promised and set. */
  async function withNew(opts: { due?: "fail" | "ok"; party?: "gone" } = {}) {
    const api = mockApi("table");
    let threads: PlotThread[] = [thread({ name: "The drowned bell", state: "active" }), PROMISE];
    const reads = { n: 0 };
    api.plotThreads = async () => {
      reads.n += 1;
      return answer({}, threads);
    };
    api.campaignNames = async () => NAMES_44;
    const made: { kind: string; state: string; name: string }[] = [];
    const dues: { promise: string; due: string }[] = [];
    const parties: { promise: string; side: string; who: string }[] = [];
    api.addPlotThread = async (_ref, change) => {
      made.push(change);
      return { id: "c3".repeat(16) };
    };
    api.setPlotThreadDue = async (_ref, change) => {
      if (opts.due === "fail") throw new ApiError("network");
      dues.push(change);
    };
    api.addPlotThreadParty = async (_ref, change) => {
      if (opts.party === "gone") throw new ApiError("gone");
      parties.push(change);
    };
    render(<PlotThreadsPanel api={api} refId={REF} />);
    await screen.findByText("The drowned bell");
    return { api, reads, made, dues, parties, setThreads: (t: PlotThread[]) => (threads = t) };
  }
  const card = async (name: string) => (await screen.findByText(name)).closest("article") as HTMLElement;

  it("New story thread offers four kinds, defaults to a quest, and creates it", async () => {
    const { made } = await withNew();
    fireEvent.click(screen.getByRole("button", { name: w.newButton }));
    expect((screen.getByRole("radio", { name: "A quest" }) as HTMLInputElement).checked).toBe(true);
    for (const label of ["A mystery", "A hook (a lead)", "A goal"]) {
      expect(screen.getByRole("radio", { name: label })).toBeTruthy();
    }
    expect(screen.queryByRole("radio", { name: "Just a hint" })).toBeNull();
    fireEvent.input(screen.getByLabelText(w.newNameLabel), { target: { value: "The Missing Heir" } });
    fireEvent.click(screen.getByRole("button", { name: w.createIt }));
    expect(await screen.findByText(w.started)).toBeTruthy();
    expect(made).toEqual([{ kind: "quest", state: "open", name: "The Missing Heir" }]);
  });

  it("Add a promise saves the due note after the promise is made when one is typed", async () => {
    const { made, dues } = await withNew();
    fireEvent.click(screen.getByRole("button", { name: w.promiseButton }));
    fireEvent.input(screen.getByLabelText(w.promiseNameLabel), { target: { value: "Bring the healer back" } });
    fireEvent.input(screen.getByLabelText(w.promiseDueLabel), { target: { value: "By the full moon" } });
    fireEvent.click(screen.getByRole("button", { name: w.addSubmit }));
    expect(await screen.findByText(w.promiseDone)).toBeTruthy();
    expect(made).toEqual([{ kind: "promise", state: "open", name: "Bring the healer back" }]);
    expect(dues).toEqual([{ promise: "c3".repeat(16), due: "By the full moon" }]);
  });

  it("Add a promise says so when only the due note fails", async () => {
    await withNew({ due: "fail" });
    fireEvent.click(screen.getByRole("button", { name: w.promiseButton }));
    fireEvent.input(screen.getByLabelText(w.promiseNameLabel), { target: { value: "Bring the healer back" } });
    fireEvent.input(screen.getByLabelText(w.promiseDueLabel), { target: { value: "By the full moon" } });
    fireEvent.click(screen.getByRole("button", { name: w.addSubmit }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.dueFailed);
  });

  it("a promise offers its due note and both parties; another kind does not", async () => {
    await withNew();
    const promise = await card("Mira's word");
    expect(within(promise).getByRole("button", { name: w.dueButton })).toBeTruthy();
    expect(within(promise).getByRole("button", { name: w.byButton })).toBeTruthy();
    expect(within(promise).getByRole("button", { name: w.toButton })).toBeTruthy();
    const bell = await card("The drowned bell");
    expect(within(bell).queryByRole("button", { name: w.dueButton })).toBeNull();
    expect(within(bell).queryByRole("button", { name: w.byButton })).toBeNull();
  });

  it("Who promised offers only characters and groups; Promised to also offers places", async () => {
    await withNew();
    const promise = await card("Mira's word");
    fireEvent.click(within(promise).getByRole("button", { name: w.byButton }));
    expect(await within(promise).findByRole("button", { name: "Mira" })).toBeTruthy();
    expect(within(promise).getByRole("button", { name: "The Guild" })).toBeTruthy();
    expect(within(promise).queryByRole("button", { name: "Old Hall" })).toBeNull();
    fireEvent.click(within(promise).getByRole("button", { name: w.byButton }));
    fireEvent.click(within(promise).getByRole("button", { name: w.toButton }));
    expect(await within(promise).findByRole("button", { name: "Old Hall" })).toBeTruthy();
  });

  it("a due note may be empty and clears it", async () => {
    const { dues } = await withNew();
    const promise = await card("Mira's word");
    fireEvent.click(within(promise).getByRole("button", { name: w.dueButton }));
    const box = (await within(promise).findByLabelText(w.dueLabel)) as HTMLInputElement;
    expect(box.value).toBe("By the full moon");
    fireEvent.input(box, { target: { value: "" } });
    fireEvent.click(within(promise).getByRole("button", { name: w.dueSave }));
    expect(await screen.findByText(w.saved)).toBeTruthy();
    expect(dues).toEqual([{ promise: PROMISE.id, due: "" }]);
  });

  it("a thread or name that is gone says so and re-reads the list", async () => {
    const { reads } = await withNew({ party: "gone" });
    const promise = await card("Mira's word");
    fireEvent.click(within(promise).getByRole("button", { name: w.byButton }));
    fireEvent.click(await within(promise).findByRole("button", { name: "Mira" }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.addGone);
    await waitFor(() => expect(reads.n).toBe(2));
  });

  it("every form waits while a press is with the bot", async () => {
    const { api } = await withNew();
    let release!: () => void;
    api.addPlotThread = () =>
      new Promise<{ id: string }>((resolve) => {
        release = () => resolve({ id: "c4".repeat(16) });
      });
    fireEvent.click(screen.getByRole("button", { name: w.newButton }));
    fireEvent.input(screen.getByLabelText(w.newNameLabel), { target: { value: "The Missing Heir" } });
    fireEvent.click(screen.getByRole("button", { name: w.createIt }));
    await waitFor(() => expect((screen.getByLabelText(w.newNameLabel) as HTMLInputElement).disabled).toBe(true));
    expect((screen.getByRole("button", { name: w.promiseButton }) as HTMLButtonElement).disabled).toBe(true);
    release();
    expect(await screen.findByText(w.started)).toBeTruthy();
  });
});

// Link an event and Led to… (#4949, slice 2d): an event moved the thread along or ended it; a thread led to another.
describe("link an event and led to (#4949)", () => {
  const BELL = thread({ name: "The drowned bell", state: "active" });
  const PROM = thread({ id: "p1".repeat(16), name: "Mira's word", kind: "promise", state: "open", stateWords: "Open", promise: { by: [], to: [], due: "" } });
  const NAMES_49 = [
    { id: "e1", text: "The ambush at the ford", kind: "event", saidAs: null },
    { id: "n-mira", text: "Mira", kind: "character", saidAs: null },
    { id: BELL.id, text: "The drowned bell", kind: "thread", saidAs: null },
  ];
  async function withLinks(opts: { ledFail?: boolean; linkGone?: boolean } = {}) {
    const api = mockApi("table");
    let threads: PlotThread[] = [BELL, PROM];
    const reads = { n: 0 };
    api.plotThreads = async () => {
      reads.n += 1;
      return answer({}, threads);
    };
    api.campaignNames = async () => NAMES_49;
    const linked: Record<string, unknown>[] = [];
    const led: { from: string; to: string }[] = [];
    api.linkPlotThreadEvent = async (_ref, change) => {
      if (opts.linkGone) throw new ApiError("gone");
      linked.push(change);
      const ev = "newEvent" in change ? change.newEvent : NAMES_49.find((n) => n.id === change.event)!.text;
      threads = threads.map((t) =>
        t.id === change.thread ? { ...t, movers: [...t.movers, { id: "m1", name: ev, endsIt: change.how === "ended", session: null }] } : t,
      );
    };
    api.plotThreadLedTo = async (_ref, change) => {
      if (opts.ledFail) throw new ApiError("network");
      led.push(change);
    };
    render(<PlotThreadsPanel api={api} refId={REF} />);
    await screen.findByText("The drowned bell");
    const bell = (await screen.findByText("The drowned bell")).closest("article") as HTMLElement;
    return { api, reads, linked, led, bell };
  }

  it("Link an event lists only events and asks whether it moved or ended the thread", async () => {
    const { bell } = await withLinks();
    fireEvent.click(within(bell).getByRole("button", { name: w.linkButton }));
    expect(await within(bell).findByRole("button", { name: "The ambush at the ford" })).toBeTruthy();
    expect(within(bell).queryByRole("button", { name: "Mira" })).toBeNull();
    fireEvent.click(within(bell).getByRole("button", { name: "The ambush at the ford" }));
    expect(within(bell).getByText(w.howQuestion)).toBeTruthy();
    expect(within(bell).getByRole("button", { name: w.moved })).toBeTruthy();
    expect(within(bell).getByRole("button", { name: w.ended })).toBeTruthy();
  });

  it("moved and ended call the API with the right how and show the event in Moved by", async () => {
    const { linked, bell } = await withLinks();
    fireEvent.click(within(bell).getByRole("button", { name: w.linkButton }));
    fireEvent.click(await within(bell).findByRole("button", { name: "The ambush at the ford" }));
    fireEvent.click(within(bell).getByRole("button", { name: w.moved }));
    expect(await screen.findByText(w.linked)).toBeTruthy();
    expect(linked).toEqual([{ thread: BELL.id, how: "moved", event: "e1" }]);
    expect(await within(bell).findByText(`${w.movedBy} The ambush at the ford`)).toBeTruthy();
  });

  it("a name nothing matches offers to add it as a new event, and links it", async () => {
    const { linked, bell } = await withLinks();
    fireEvent.click(within(bell).getByRole("button", { name: w.linkButton }));
    fireEvent.input(within(bell).getByLabelText(w.eventLabel), { target: { value: "Gate at dawn" } });
    fireEvent.click(await within(bell).findByRole("button", { name: w.newEventChoice("Gate at dawn") }));
    fireEvent.click(within(bell).getByRole("button", { name: w.moved }));
    expect(await screen.findByText(w.linked)).toBeTruthy();
    expect(linked).toEqual([{ thread: BELL.id, how: "moved", newEvent: "Gate at dawn" }]);
  });

  it("Led to… lists events and threads but not the thread itself, and saves the pick", async () => {
    const { led, bell } = await withLinks();
    fireEvent.click(within(bell).getByRole("button", { name: w.ledButton }));
    expect(await within(bell).findByRole("button", { name: "The ambush at the ford" })).toBeTruthy();
    expect(within(bell).queryByRole("button", { name: "The drowned bell" })).toBeNull();
    fireEvent.click(within(bell).getByRole("button", { name: "The ambush at the ford" }));
    expect(await screen.findByText(w.linked)).toBeTruthy();
    expect(led).toEqual([{ from: BELL.id, to: "e1" }]);
  });

  it("a failed led to says nothing changed", async () => {
    const { api, bell } = await withLinks();
    // The bot's "failed" (a loop or a wrong kind) reaches the page as a server error.
    api.plotThreadLedTo = async () => {
      throw new ApiError("server");
    };
    fireEvent.click(within(bell).getByRole("button", { name: w.ledButton }));
    fireEvent.click(await within(bell).findByRole("button", { name: "The ambush at the ford" }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.ledFailed);
  });

  it("a thread or name that is gone says so and re-reads the list", async () => {
    const { reads, bell } = await withLinks({ linkGone: true });
    fireEvent.click(within(bell).getByRole("button", { name: w.linkButton }));
    fireEvent.click(await within(bell).findByRole("button", { name: "The ambush at the ford" }));
    fireEvent.click(within(bell).getByRole("button", { name: w.moved }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.addGone);
    await waitFor(() => expect(reads.n).toBe(2));
  });

  it("every button waits while a press is with the bot", async () => {
    const { api, bell } = await withLinks();
    let release!: () => void;
    api.linkPlotThreadEvent = () =>
      new Promise<void>((resolve) => {
        release = resolve;
      });
    fireEvent.click(within(bell).getByRole("button", { name: w.linkButton }));
    fireEvent.click(await within(bell).findByRole("button", { name: "The ambush at the ford" }));
    fireEvent.click(within(bell).getByRole("button", { name: w.moved }));
    await waitFor(() => expect((within(bell).getByRole("button", { name: w.moved }) as HTMLButtonElement).disabled).toBe(true));
    expect((within(bell).getByRole("button", { name: w.ended }) as HTMLButtonElement).disabled).toBe(true);
    release();
    expect(await screen.findByText(w.linked)).toBeTruthy();
  });
});
