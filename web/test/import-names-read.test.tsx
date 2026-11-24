/** @jsxImportSource preact */
// @vitest-environment happy-dom
// After a session is imported, DMbot reads its names (#5127, the web half of #5091). The import page shows that read in the shared
// wait shape ("Reading names", part n of m, time running, last move), then one plain line for parts that could not be read,
// or the stopped line; nothing shows when the reader is absent.
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

const names = (over: Partial<JobProgress> = {}): JobProgress => ({
  stage: "reading_names",
  done: 3,
  total: 8,
  unit: "parts",
  startedAt: now() - 90,
  updatedAt: now() - 10,
  serverNow: now(),
  state: "running",
  reason: null,
  ...over,
});
const answer = (n: JobProgress | null | undefined, unread?: number): ImportProgress => ({
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
      ...(n === undefined ? {} : { names: n }),
      ...(unread === undefined ? {} : { namesUnread: unread }),
    },
  ],
});

describe("the name reader of each session", () => {
  it("shows Reading names with the part, the time running and the last move", async () => {
    await importing([answer(names())]);
    expect(await screen.findByText(w.names.reading_names!)).toBeTruthy();
    expect(screen.getByText(ww.progress(3, 8, "pieces"), { exact: false })).toBeTruthy();
  });

  it("a read with 2 unread says so, with the count first", async () => {
    await importing([answer(names({ stage: "names_read", state: "done" }), 2)]);
    expect(await screen.findByText(w.namesUnread(2))).toBeTruthy();
    expect(w.namesUnread(2)).toBe("2 parts of this session couldn't be read for names.");
  });

  it("a read with 1 unread says part, not parts", async () => {
    await importing([answer(names({ stage: "names_read", state: "done" }), 1)]);
    expect(await screen.findByText(w.namesUnread(1))).toBeTruthy();
    expect(w.namesUnread(1)).toBe("1 part of this session couldn't be read for names.");
  });

  it("a read with 0 unread says nothing", async () => {
    await importing([answer(names({ stage: "names_read", state: "done" }), 0)]);
    expect(screen.queryByText(/couldn't be read for names/)).toBeNull();
  });

  it("a stopped reader says the names found are still suggested", async () => {
    await importing([answer(names({ stage: "names_stopped", state: "failed" }))]);
    expect(await screen.findByText(w.namesStopped)).toBeTruthy();
  });

  it("no names stage shows nothing", async () => {
    await importing([answer(undefined)]);
    expect(screen.queryByText(w.names.reading_names!)).toBeNull();
    expect(screen.queryByText(/couldn't be read for names/)).toBeNull();
  });

  it("none of these lines holds a name from the transcript", async () => {
    await importing([answer(names({ stage: "names_read", state: "done" }), 2)]);
    await screen.findByText(w.namesUnread(2));
    // The transcript fixture holds "Cerric" and "hello": neither may reach the page.
    expect(document.body.textContent).not.toContain("Cerric");
    expect(document.body.textContent).not.toContain("hello");
  });
});
