/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The short summary of a picked name on the Story Graph (#3243): one request per pick, a spoiler gate for players,
// and words that show DMbot is still working. The switch is mocked so each test can turn the panel on or off.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import NodeSummary from "../src/account/NodeSummary";
import { ApiError, type AccountApi, type NodeSummary as Summary } from "../src/account/api";
import { nodeSummaryWords as w } from "../src/content/node-summary-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/node-summary-switch", () => ({
  get NODE_SUMMARY_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
  vi.useRealTimers();
});

const REF = "100.cmp1";
const text = (t: string, version: "dm" | "players" = "dm"): Summary => ({ text: t, needsPeek: false, version });

/** A request the test settles by hand, so it can come back late. */
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function stub(impl: (ref: string, id: string, peek?: boolean) => Promise<Summary>) {
  return { webNodeSummary: vi.fn(impl) } as unknown as AccountApi & { webNodeSummary: ReturnType<typeof vi.fn> };
}

describe("the summary of a picked name", () => {
  it("shows nothing while the switch is off", () => {
    live.on = false;
    const api = stub(async () => text("Mara keeps the lantern."));
    render(<NodeSummary api={api} refId={REF} id="n1" />);
    expect(api.webNodeSummary).not.toHaveBeenCalled();
    expect(screen.queryByText("Mara keeps the lantern.")).toBeNull();
  });

  it("loads once for a pick and shows the text, with the hint for the DM", async () => {
    const api = stub(async () => text("Mara keeps the lantern."));
    render(<NodeSummary api={api} refId={REF} id="n1" />);
    expect(await screen.findByText("Mara keeps the lantern.")).toBeTruthy();
    expect(api.webNodeSummary).toHaveBeenCalledTimes(1);
    expect(api.webNodeSummary).toHaveBeenCalledWith(REF, "n1", false);
    expect(screen.getByText(w.dmHint)).toBeTruthy();
  });

  it("a player sees the text but no DM hint", async () => {
    const api = stub(async () => text("Mara keeps the lantern.", "players"));
    render(<NodeSummary api={api} refId={REF} id="n1" />);
    await screen.findByText("Mara keeps the lantern.");
    expect(screen.queryByText(w.dmHint)).toBeNull();
  });

  it("a new pick ignores the answer to the older one", async () => {
    const first = deferred<Summary>();
    const second = deferred<Summary>();
    const api = stub((_ref, id) => (id === "n1" ? first.promise : second.promise));
    const { rerender } = render(<NodeSummary api={api} refId={REF} id="n1" />);
    rerender(<NodeSummary api={api} refId={REF} id="n2" />);
    second.resolve(text("The ferry is Brann's."));
    expect(await screen.findByText("The ferry is Brann's.")).toBeTruthy();
    first.resolve(text("Old answer."));
    await act(async () => {
      await first.promise;
    });
    expect(screen.queryByText("Old answer.")).toBeNull();
  });

  it("after 5 and 30 seconds the loading words change, and count up", async () => {
    vi.useFakeTimers();
    const pending = deferred<Summary>();
    const api = stub(() => pending.promise);
    render(<NodeSummary api={api} refId={REF} id="n1" />);
    expect(screen.getByText(w.loading)).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByText(w.slow(5))).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(25000);
    });
    expect(screen.getByText(w.stalled(30))).toBeTruthy();
  });

  it("a player's first name is behind a gate, and Show it anyway asks with peek", async () => {
    const api = stub(async (_ref, _id, peek) =>
      peek ? text("Mara keeps the lantern.", "players") : { text: "Mara keeps the lantern.", needsPeek: true, version: "players" },
    );
    render(<NodeSummary api={api} refId={REF} id="n1" />);
    expect(await screen.findByText(w.spoilers)).toBeTruthy();
    // The gated text must not be anywhere in the page, not only out of one query.
    expect(document.body.textContent ?? "").not.toContain("Mara keeps the lantern.");
    expect(document.querySelectorAll("p").length).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: w.showAnyway }));
    expect(await screen.findByText("Mara keeps the lantern.")).toBeTruthy();
    expect(api.webNodeSummary).toHaveBeenLastCalledWith(REF, "n1", true);
  });

  it("a name not on the graph says so", async () => {
    const api = stub(async () => {
      throw new ApiError("not-found");
    });
    render(<NodeSummary api={api} refId={REF} id="gone" />);
    expect(await screen.findByText(w.notFound)).toBeTruthy();
  });

  it("an error says so, and Try again is disabled for a second after each press", async () => {
    const api = stub(async () => {
      throw new ApiError("network");
    });
    render(<NodeSummary api={api} refId={REF} id="n1" />);
    expect(await screen.findByText(w.error)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.tryAgain }));
    // The second answer is an error too, straight away: its Try again is still cooling down.
    await waitFor(() => expect(api.webNodeSummary).toHaveBeenCalledTimes(2));
    const again = (await screen.findByRole("button", { name: w.tryAgain })) as HTMLButtonElement;
    expect(again.disabled).toBe(true);
    await waitFor(() => expect((screen.getByRole("button", { name: w.tryAgain }) as HTMLButtonElement).disabled).toBe(false), {
      timeout: 2000,
    });
  });

  it("no word about rank, roles or importance appears in any word or state", async () => {
    const banned = /\b(rank|role|roles|important|importance|main character)\b/i;
    for (const value of Object.values(w)) {
      if (typeof value === "string") expect(value).not.toMatch(banned);
      else expect(value(30)).not.toMatch(banned);
    }
    for (const version of ["dm", "players"] as const) {
      cleanup();
      const api = stub(async () => text("Mara keeps the lantern.", version));
      render(<NodeSummary api={api} refId={REF} id="n1" />);
      await screen.findByText("Mara keeps the lantern.");
      expect(document.body.textContent ?? "").not.toMatch(banned);
    }
  });
});
