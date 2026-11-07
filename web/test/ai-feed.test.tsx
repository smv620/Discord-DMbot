/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The live windows on what DMbot asked the AI (#3710): two read-only windows, newest at the bottom, each entry as
// the server cut it, a reply arriving where its prompt already is, the "not shown" line, a look that stops after
// repeated failures, and no buttons on an entry. Plain text only.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AiFeed from "../src/account/AiFeed";
import { ApiError, type AccountApi, type AiFeedEntry } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import { feedWords as w } from "../src/content/ai-feed-words";

afterEach(cleanup);

const entry = (n: number, over: Partial<AiFeedEntry> = {}): AiFeedEntry => ({
  job: "a".repeat(32),
  n,
  at: 1_790_000_000 + n,
  service: "Anthropic",
  model: "smaller model",
  step: `part ${n}`,
  prompt: `Read part ${n}.`,
  reply: `Found ${n}.`,
  state: "done",
  reason: null,
  ...over,
});

const slow = { timeout: 5000 };
const apiWith = (aiFeed: AccountApi["aiFeed"]): AccountApi => ({ ...mockApi("table"), aiFeed }) as AccountApi;
const props = { campaignRef: "100.cmp1", kind: "lore" as const, job: "a".repeat(32), everyMs: 15 };

describe("AiFeed", () => {
  it("shows what was asked and what came back in two windows, in order, and no buttons", async () => {
    const api = apiWith(async () => ({ lost: 0, entries: [entry(2), entry(1)] }));
    render(<AiFeed api={api} running={false} {...props} />);
    const asked = await screen.findByRole("region", { name: w.windowLabel(w.promptsHeading) });
    const back = screen.getByRole("region", { name: w.windowLabel(w.repliesHeading) });
    expect(asked.textContent).toMatch(/Read part 1\.[\s\S]*Read part 2\./);
    expect(back.textContent).toMatch(/Found 1\.[\s\S]*Found 2\./);
    expect(asked.textContent).toContain("Question 1");
    expect(back.textContent).toContain("Answer 1");
    expect(screen.queryAllByRole("button")).toEqual([]);
  });

  it("says the reply is awaited while a call is waiting, and fills it in on a later look", async () => {
    let looks = 0;
    const api = apiWith(async () => {
      looks += 1;
      return { lost: 0, entries: [looks < 2 ? entry(1, { state: "waiting", reply: null }) : entry(1)] };
    });
    render(<AiFeed api={api} running {...props} />);
    expect(await screen.findByText(new RegExp(w.waitingReply))).toBeTruthy();
    await waitFor(() => expect(screen.getByRole("region", { name: w.windowLabel(w.repliesHeading) }).textContent).toContain("Found 1."));
    expect(screen.getByRole("region", { name: w.windowLabel(w.repliesHeading) }).textContent).not.toContain(w.waitingReply);
  });

  it("asks again from the oldest call still waiting, so one that finishes later is read again", async () => {
    const afters: number[] = [];
    let looks = 0;
    const api = apiWith(async (_r, _k, _j, after) => {
      afters.push(after);
      looks += 1;
      if (looks === 1) return { lost: 0, entries: [entry(1, { state: "waiting", reply: null }), entry(2, { at: 1_790_000_050 })] };
      return { lost: 0, entries: [entry(1, { state: "done", reply: "Found it late." })] };
    });
    render(<AiFeed api={api} running {...props} />);
    await waitFor(() => expect(screen.getByRole("region", { name: w.windowLabel(w.repliesHeading) }).textContent).toContain("Found it late."));
    expect(afters[0]).toBe(0);
    expect(afters[1]).toBe(1_790_000_001); // the waiting call's time, not the newest (…050)
  });

  it("says no answer came back for a call still waiting once the job has ended", async () => {
    const api = apiWith(async () => ({ lost: 0, entries: [entry(1, { state: "waiting", reply: null })] }));
    render(<AiFeed api={api} running={false} {...props} />);
    await screen.findByText(new RegExp(w.noReply));
  });

  it("asks again from the time it reached, and shows each call once", async () => {
    const asked: number[] = [];
    const api = apiWith(async (_r, _k, _j, after) => {
      asked.push(after);
      return { lost: 0, entries: [entry(1)] };
    });
    render(<AiFeed api={api} running {...props} />);
    await waitFor(() => expect(asked.length).toBeGreaterThan(2));
    expect(asked[0]).toBe(0);
    expect(asked[2]).toBe(1_790_000_001);
    expect((screen.getByRole("region", { name: w.windowLabel(w.promptsHeading) }).textContent ?? "").match(/Read part 1\./g)).toHaveLength(1);
  });

  it("stops looking when the job is over, after one last look", async () => {
    const aiFeed = vi.fn(async () => ({ lost: 0, entries: [entry(1)] }));
    const { rerender } = render(<AiFeed api={apiWith(aiFeed)} running {...props} />);
    await waitFor(() => expect(aiFeed.mock.calls.length).toBeGreaterThan(1));
    const api = apiWith(aiFeed);
    rerender(<AiFeed api={api} running={false} {...props} />);
    await new Promise((ok) => setTimeout(ok, 120));
    const settled = aiFeed.mock.calls.length;
    await new Promise((ok) => setTimeout(ok, 120));
    expect(aiFeed.mock.calls.length).toBe(settled);
  });

  it("says how many calls are not shown, and that the look may be out of date after repeated failures", async () => {
    render(<AiFeed api={apiWith(async () => ({ lost: 2, entries: [] }))} running={false} {...props} />);
    expect(await screen.findByText(w.lost(2))).toBeTruthy();
    cleanup();
    render(
      <AiFeed
        api={apiWith(async () => {
          throw new ApiError("server");
        })}
        running
        {...props}
      />,
    );
    expect(await screen.findByText(w.stalled)).toBeTruthy();
  });

  it("shows nothing at all on an API that has no feed, and 'Nothing asked yet' while empty", async () => {
    const { container } = render(<AiFeed api={{ ...mockApi("table"), aiFeed: undefined } as unknown as AccountApi} running {...props} />);
    expect(container.textContent).toBe("");
    cleanup();
    render(<AiFeed api={apiWith(async () => ({ lost: 0, entries: [] }))} running={false} {...props} />);
    expect((await screen.findAllByText(w.nonePrompts)).length).toBe(1);
  });

  it("offers to jump to the newest when the reader has scrolled up", async () => {
    let n = 1;
    const api = apiWith(async () => ({ lost: 0, entries: [entry(n++)] }));
    render(<AiFeed api={api} running {...props} />);
    const box = (await screen.findByRole("region", { name: w.windowLabel(w.promptsHeading) })) as HTMLElement;
    Object.defineProperty(box, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(box, "clientHeight", { value: 100, configurable: true });
    box.scrollTop = 0;
    fireEvent.scroll(box); // the reader is at the top, far from the newest
    expect((await screen.findAllByRole("button", { name: w.jump })).length).toBeGreaterThan(0);
  });
});

describe("the look waits while the tab is hidden (#3710)", () => {
  // A fake `document.hidden`: setting it fires the same event a browser fires when the tab is hidden or shown.
  let hiddenNow = false;
  const setHidden = (value: boolean): void => {
    hiddenNow = value;
    document.dispatchEvent(new Event("visibilitychange"));
  };
  const pause = (ms: number): Promise<void> => new Promise((ok) => setTimeout(ok, ms));
  beforeEach(() => {
    hiddenNow = false;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hiddenNow });
  });
  afterEach(() => {
    hiddenNow = false;
  });

  it("does not ask while the tab is hidden", async () => {
    const aiFeed = vi.fn(async () => ({ lost: 0, entries: [entry(1)] }));
    render(<AiFeed api={apiWith(aiFeed)} running {...props} />);
    await waitFor(() => expect(aiFeed.mock.calls.length).toBeGreaterThan(1));
    setHidden(true);
    await pause(20); // a look already on its way may finish; nothing new is asked
    const atHide = aiFeed.mock.calls.length;
    await pause(120); // several intervals go by
    expect(aiFeed.mock.calls.length).toBe(atHide);
  });

  it("asks at once when the tab is shown again, and carries on", async () => {
    const aiFeed = vi.fn(async () => ({ lost: 0, entries: [entry(1)] }));
    render(<AiFeed api={apiWith(aiFeed)} running {...props} />);
    await waitFor(() => expect(aiFeed.mock.calls.length).toBeGreaterThan(1));
    setHidden(true);
    await pause(60);
    const atHide = aiFeed.mock.calls.length;
    setHidden(false);
    await waitFor(() => expect(aiFeed.mock.calls.length).toBeGreaterThan(atHide));
    await waitFor(() => expect(aiFeed.mock.calls.length).toBeGreaterThan(atHide + 1), slow);
  });

  it("a tab hidden from the start asks nothing until it is shown", async () => {
    hiddenNow = true;
    const aiFeed = vi.fn(async () => ({ lost: 0, entries: [entry(1)] }));
    render(<AiFeed api={apiWith(aiFeed)} running {...props} />);
    await pause(60);
    expect(aiFeed.mock.calls.length).toBe(0);
    setHidden(false);
    await waitFor(() => expect(aiFeed.mock.calls.length).toBeGreaterThan(0));
  });

  it("keeps what it showed while hidden, and while shown again", async () => {
    const api = apiWith(async () => ({ lost: 0, entries: [entry(1), entry(2)] }));
    render(<AiFeed api={api} running {...props} />);
    const back = await screen.findByRole("region", { name: w.windowLabel(w.repliesHeading) });
    const before = back.textContent;
    expect(before).toContain("Found 2.");
    setHidden(true);
    await pause(40);
    expect(back.textContent).toBe(before);
    setHidden(false);
    await pause(40);
    expect(back.textContent).toBe(before);
  });
});
