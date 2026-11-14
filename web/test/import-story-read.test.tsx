/** @jsxImportSource preact */
// @vitest-environment happy-dom
// After a session is imported, DMbot reads its connections (#3307). The import page shows that read in the shared
// wait shape: "Reading connections" (piece n of m, time running, last move), the daily-limit wait that carries on
// by itself, and the finished line; the page keeps looking while it reads, and shows nothing before it starts.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ImportProgress, JobProgress } from "../src/account/api";
import Import from "../src/account/Import";
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
    // These tests drive the older choices (a member or Leave out); the consent step has its own tests (import-confirm).
    get IMPORT_CONFIRM_LIVE() {
      return false;
    },
  };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  live.on = false;
});

const s1 = new File(["Cerric: hello\nAsh: hi"], "session-1-2026-09-14.txt", {
  type: "text/plain",
});
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
  api.importStart = async () => ({
    importId: "x",
    sessions: [],
    estimate: { sessions: 1, fastCalls: 1 },
  });
  live.on = true;
  render(<Import api={api} path="/account/import/100.cmp1" />);
  const input = screen.getByLabelText(w.pickLabel, {
    selector: "input",
  }) as HTMLInputElement;
  Object.defineProperty(input, "files", { value: [s1], configurable: true });
  fireEvent.change(input);
  fireEvent.click(screen.getByRole("button", { name: w.preview }));
  fireEvent.click(await screen.findByRole("button", { name: w.checkButton }));
  await screen.findByText(/^Checked:/);
  fireEvent.click(screen.getByRole("button", { name: w.importButton }));
  await screen.findByText(w.progressHeading);
  return { api, asks: () => asks };
}

const story = (over: Partial<JobProgress> = {}): JobProgress => ({
  stage: "reading_connections",
  done: 2,
  total: 5,
  unit: "parts",
  startedAt: now() - 90,
  updatedAt: now() - 10,
  serverNow: now(),
  state: "running",
  reason: null,
  ...over,
});
const answer = (s: JobProgress | null | undefined): ImportProgress => ({
  import: {
    stage: "done",
    state: "done",
    serverNow: now(),
    startedAt: now() - 300,
    updatedAt: now() - 100,
  },
  sessions: [
    {
      file: "a.txt",
      date: "2026-09-14",
      state: "done",
      reason: null,
      stage: "done",
      ...(s === undefined ? {} : { story: s }),
    },
  ],
});

describe("the read of each session's connections", () => {
  it("shows Reading connections with the piece, the time running and the last move", async () => {
    await importing([answer(story())]);
    expect(await screen.findByText(w.story.reading_connections!)).toBeTruthy();
    expect(
      screen.getByText(ww.progress(2, 5, "pieces"), { exact: false }),
    ).toBeTruthy();
  });

  it("says it is waiting for the daily limit and carries on by itself, with no clock", async () => {
    await importing([
      answer(
        story({
          stage: "waiting_for_limit",
          done: null,
          total: null,
          unit: null,
        }),
      ),
    ]);
    expect(await screen.findByText(w.story.waiting_for_limit!)).toBeTruthy();
    expect(w.story.waiting_for_limit).toBe(
      "Waiting for DMbot's daily limit, will carry on by itself",
    );
  });

  it("says the connections are read when it is done", async () => {
    await importing([
      answer(
        story({
          stage: "connections_read",
          state: "done",
          done: null,
          total: null,
        }),
      ),
    ]);
    expect(await screen.findByText(w.story.connections_read!)).toBeTruthy();
  });

  it("shows nothing before the read starts, or from an older API", async () => {
    await importing([answer(null)]);
    expect(screen.queryByText(w.story.reading_connections!)).toBeNull();
    cleanup();
    await importing([answer(undefined)]);
    expect(screen.queryByText(w.story.connections_read!)).toBeNull();
  });

  it("keeps looking while the read goes on after the import is done, then stops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { asks } = await importing([
        answer(story()),
        answer(story({ done: 4 })),
        answer(
          story({
            stage: "connections_read",
            state: "done",
            done: null,
            total: null,
          }),
        ),
      ]);
      await vi.advanceTimersByTimeAsync(60_000);
      expect(await screen.findByText(w.story.connections_read!)).toBeTruthy();
      const after = asks();
      await vi.advanceTimersByTimeAsync(120_000);
      expect(asks()).toBe(after);
      expect(after).toBe(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not look for a read that has failed, and says it failed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { asks } = await importing([answer(story({ state: "failed" }))]);
      expect(await screen.findByText(w.storyFailed)).toBeTruthy();
      const after = asks();
      await vi.advanceTimersByTimeAsync(120_000);
      expect(asks()).toBe(after);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows nothing for a stage it does not know", async () => {
    await importing([answer(story({ stage: "constructor" }))]);
    expect(screen.queryByText(w.story.reading_connections!)).toBeNull();
    expect(document.querySelector("progress")).toBeNull();
  });

  it("picks up a read that starts after the session was already imported", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      // (the session is imported, but its first look says nothing of a read; the import itself is still running)
      const first = answer(null);
      first.import = { ...first.import!, stage: "cleaning", state: "running" };
      first.sessions[0]!.stage = "cleaning";
      await importing([first, answer(story())]);
      await vi.advanceTimersByTimeAsync(60_000);
      expect(await screen.findByText(w.story.reading_connections!)).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});
