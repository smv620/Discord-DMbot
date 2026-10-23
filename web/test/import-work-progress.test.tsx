/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The import page shows the whole import's progress the way every wait does (#2565; job shape from #2564): the
// stage, "n of m sessions", how long it has run, the last change, a stall, and a failure with what to do. A
// late older answer never moves it backwards, and a missing import object (an older API) keeps the old line.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ImportProgress, JobProgress } from "../src/account/api";
import { ApiError } from "../src/account/api";
import Import, { WATCH_LIMIT_MS } from "../src/account/Import";
import { mockApi } from "../src/account/mock";
import { importPage as w } from "../src/content/import-words";
import { workWords as ww } from "../src/content/work-progress";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/import-words", async (original) => {
  const real = await original<typeof import("../src/content/import-words")>();
  return {
    ...real,
    get IMPORT_LIVE() {
      return live.on;
    },
  };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  live.on = false;
});

const s1 = new File(["Cerric: hello\nAsh: hi"], "session-1-2026-09-14.txt", { type: "text/plain" });
const now = () => Math.floor(Date.now() / 1000);

/** Opens the page, runs the import, and answers each ask with the next of `answers` (the last one stays). */
async function importing(answers: (ImportProgress | Error)[]) {
  const api = mockApi("table");
  let asks = 0;
  api.importProgress = async () => {
    const next = answers[Math.min(asks++, answers.length - 1)]!;
    if (next instanceof Error) throw next;
    return next;
  };
  api.importStart = async () => ({ importId: "x", sessions: [], estimate: { sessions: 1, fastCalls: 1 } });
  live.on = true;
  render(<Import api={api} path="/account/import/100.cmp1" />);
  const input = screen.getByLabelText(w.pickLabel, { selector: "input" }) as HTMLInputElement;
  Object.defineProperty(input, "files", { value: [s1], configurable: true });
  fireEvent.change(input);
  fireEvent.click(screen.getByRole("button", { name: w.preview }));
  fireEvent.click(await screen.findByRole("button", { name: w.checkButton }));
  await screen.findByText(/^Checked:/);
  fireEvent.click(screen.getByRole("button", { name: w.importButton }));
  await screen.findByText(w.progressHeading);
  return { api, asks: () => asks };
}

const job = (over: Partial<JobProgress> = {}): JobProgress => ({
  stage: "cleaning",
  done: 3,
  total: 9,
  unit: "sessions",
  startedAt: now() - 125,
  updatedAt: now() - 8,
  serverNow: now(),
  state: "running",
  reason: null,
  ...over,
});
const answer = (imp: JobProgress | undefined, over: Partial<ImportProgress["sessions"][number]> = {}): ImportProgress => ({
  ...(imp ? { import: imp } : {}),
  sessions: [{ file: "a.txt", date: "2026-09-14", state: "working", reason: null, stage: "cleaning", ...over }],
});

describe("the whole import's progress", () => {
  it("shows the stage, how far along it is, how long it has run and the last change", async () => {
    await importing([answer(job())]);
    expect(await screen.findByText(w.stage.cleaning!)).toBeTruthy();
    expect(screen.getByText(ww.progress(3, 9, "sessions"), { exact: false })).toBeTruthy();
    expect(screen.getByText(ww.running(ww.duration(125)), { exact: false })).toBeTruthy();
    expect(screen.getByText(ww.lastChange(ww.duration(8)), { exact: false })).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("max")).toBe("9");
  });

  it("falls back to plain words for a stage code it doesn't know", async () => {
    await importing([answer(job({ stage: "brand_new_stage" }))]);
    expect(await screen.findByText(ww.unknownStage)).toBeTruthy();
  });

  it("says a stalled import has had no change, and what to do", async () => {
    await importing([answer(job({ state: "stalled", updatedAt: now() - 190 }), { progress: job({ stage: "cleaning", state: "stalled" }) })]);
    expect(await screen.findByText(ww.stalled)).toBeTruthy();
    expect(screen.getByText(ww.quietFor(ww.duration(190)), { exact: false })).toBeTruthy();
    expect(screen.getByText(w.stalledDo)).toBeTruthy();
    expect(screen.getByText(new RegExp(w.sessionStalled))).toBeTruthy();
  });

  it("does not call the whole import failed while other sessions go on", async () => {
    const two: ImportProgress = {
      import: job({ state: "failed", reason: "couldn't check names for one session" }),
      sessions: [
        { file: "a.txt", date: "2026-09-14", state: "failed", reason: "x", stage: "failed" },
        { file: "b.txt", date: "2026-09-15", state: "working", reason: null, stage: "cleaning" },
      ],
    };
    await importing([two]);
    expect((await screen.findAllByText(w.stage.cleaning!, { exact: false })).length).toBeGreaterThan(0);
    expect(screen.queryByText(ww.failed)).toBeNull();
  });

  it("waiting for the DM's answers about names is not a stall: no clock, no stuck words", async () => {
    const waiting: ImportProgress = {
      import: job({ stage: "waiting_for_names", updatedAt: now() - 4000 }),
      sessions: [{ file: "a.txt", date: "2026-09-14", state: "working", reason: null, stage: "waiting_for_names", namesToAnswer: 2 }],
    };
    await importing([waiting]);
    expect((await screen.findAllByText(w.waitingForNames(2))).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Last progress|Running for|Nothing new for/)).toBeNull();
  });

  it("says what happened and what to do when every session failed", async () => {
    const failed: ImportProgress = {
      import: job({ state: "failed", stage: "failed", reason: "error" }),
      sessions: [{ file: "a.txt", date: "2026-09-14", state: "failed", reason: "x", stage: "failed" }],
    };
    await importing([failed]);
    expect(await screen.findByText(ww.failed)).toBeTruthy();
    expect(screen.getAllByText(w.failedReason.error!, { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getByText(w.failedDo)).toBeTruthy();
  });

  it("an older answer never moves the page backwards", async () => {
    const newer = answer(job({ done: 5, updatedAt: now() - 3 }));
    const older = answer(job({ done: 2, updatedAt: now() - 60 }));
    const { asks } = await importing([newer, older, older]);
    await screen.findByText(ww.progress(5, 9, "sessions"), { exact: false });
    await vi.waitFor(() => expect(asks()).toBeGreaterThan(1), { timeout: 8000 });
    expect(screen.queryByText(ww.progress(2, 9, "sessions"), { exact: false })).toBeNull();
    expect(screen.getByText(ww.progress(5, 9, "sessions"), { exact: false })).toBeTruthy();
  }, 15_000);

  it("keeps the last answer and says so when the import is gone", async () => {
    await importing([answer(job()), new ApiError("not-found")]);
    expect(await screen.findByText(w.gone, undefined, { timeout: 8000 })).toBeTruthy();
    expect(screen.getByText(w.stage.cleaning!)).toBeTruthy();
    // the last answer stays, but no clock keeps running beside "no longer listed"
    expect(screen.queryByText(/Running for|Last progress/)).toBeNull();
    expect(screen.getByText(ww.stoppedLooking)).toBeTruthy();
  }, 15_000);

  it("turns a failure code into plain words and never prints the code", async () => {
    const one: ImportProgress = {
      import: job({ state: "failed", stage: "failed", reason: "names_failed" }),
      sessions: [
        { file: "a.txt", date: "2026-09-14", state: "failed", reason: "names_failed", stage: "failed" },
      ],
    };
    await importing([one]);
    expect((await screen.findAllByText(w.failedReason.names_failed!, { exact: false })).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toContain("names_failed"); // the code itself is never printed
  });

  it.each(["recap_failed", "error"])("shows plain words for the failure code %s, never the code", async (code) => {
    const one: ImportProgress = {
      import: job({ state: "failed", stage: "failed", reason: code }),
      sessions: [{ file: "a.txt", date: "2026-09-14", state: "failed", reason: null, stage: "failed" }],
    };
    await importing([one]);
    expect((await screen.findAllByText(w.failedReason[code]!, { exact: false })).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toContain(code);
  });

  it("a stall does not send anyone to import again", async () => {
    await importing([answer(job({ state: "stalled", updatedAt: now() - 190 }), { progress: job({ state: "stalled" }) })]);
    expect(await screen.findByText(w.stalledDo)).toBeTruthy();
    expect(w.stalledDo).not.toMatch(/import .*again/i);
    expect(w.sessionStalled).not.toMatch(/again/i);
  });

  it("says the fallback words for a failure code it doesn't know", async () => {
    const one: ImportProgress = {
      import: job({ state: "failed", stage: "failed", reason: "something_new" }),
      sessions: [{ file: "a.txt", date: "2026-09-14", state: "failed", reason: null, stage: "failed" }],
    };
    await importing([one]);
    expect((await screen.findAllByText(w.failedFallback)).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toContain("something_new");
  });

  it("shows how many lines of a session are done while it is being cleaned", async () => {
    const cleaning: ImportProgress = {
      import: job(),
      sessions: [
        {
          file: "a.txt",
          date: "2026-09-14",
          state: "working",
          reason: null,
          stage: "cleaning",
          progress: job({ stage: "cleaning", done: 120, total: 400, unit: "lines" }),
        },
      ],
    };
    await importing([cleaning]);
    expect(await screen.findByText(w.lineCount(120, 400), { exact: false })).toBeTruthy();
  });

  it("keeps the old count line when the API sends no progress object", async () => {
    await importing([answer(undefined)]);
    expect(await screen.findByText(w.doneCount(0, 1))).toBeTruthy();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("shows only counts and codes: no file text in the progress", async () => {
    await importing([answer(job())]);
    await screen.findByText(w.stage.cleaning!);
    expect(document.body.textContent).not.toContain("Cerric: hello"); // the file's words are never on the page
  });
});

describe("how often the page asks (fake timers)", () => {
  /** Opens the page, then runs the clock; `answers` is what the API says on each ask (the last one stays). */
  async function watching(answers: ImportProgress[]) {
    const api = mockApi("table");
    let asks = 0;
    api.importProgress = async () => answers[Math.min(asks++, answers.length - 1)]!;
    api.importStart = async () => ({ importId: "x", sessions: [], estimate: { sessions: 1, fastCalls: 1 } });
    live.on = true;
    vi.useFakeTimers({ shouldAdvanceTime: true }); // (real time still moves the clock, so waiting for the page works)
    render(<Import api={api} path="/account/import/100.cmp1" />);
    const input = screen.getByLabelText(w.pickLabel, { selector: "input" }) as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [s1], configurable: true });
    fireEvent.change(input);
    fireEvent.click(screen.getByRole("button", { name: w.preview }));
    fireEvent.click(await screen.findByRole("button", { name: w.checkButton }));
    await screen.findByText(/^Checked:/);
    fireEvent.click(screen.getByRole("button", { name: w.importButton }));
    await screen.findByText(w.progressHeading);
    return { asks: () => asks };
  }

  afterEach(() => vi.useRealTimers());

  it("asks less and less often while nothing moves, up to every 30 seconds", async () => {
    const stuck = answer(job({ state: "stalled", updatedAt: 1000 }), { progress: job({ state: "stalled", updatedAt: 1000 }) });
    const { asks } = await watching([stuck]);
    const before = asks();
    await vi.advanceTimersByTimeAsync(10 * 60_000); // ten minutes of a stall
    const calls = asks() - before;
    expect(calls).toBeLessThan(30); // a steady 3 s would make 200
    expect(calls).toBeGreaterThan(10);
  });

  it("asks quickly again as soon as something moves", async () => {
    const stuck = answer(job({ updatedAt: 1000 }));
    const moving = (n: number) => answer(job({ done: n, updatedAt: 1000 + n }));
    const run = Array.from({ length: 40 }, (_, i) => moving(i + 1));
    const { asks } = await watching([stuck, stuck, ...run]);
    const before = asks();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(asks() - before).toBeGreaterThanOrEqual(12); // about every 3 s once it moves (the slow pace would be ~4)
  });

  it("stops looking after the watch limit and offers Check again", async () => {
    const { asks } = await watching([answer(job({ state: "stalled", updatedAt: 1000 }))]);
    await vi.advanceTimersByTimeAsync(WATCH_LIMIT_MS + 120_000);
    expect(screen.getByText(w.stoppedWatching)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.checkAgain })).toBeTruthy();
    expect(screen.queryByText(/Running for/)).toBeNull(); // the clock stopped with it
    const stopped = asks();
    await vi.advanceTimersByTimeAsync(5 * 60_000);
    expect(asks()).toBe(stopped);
  });
});
