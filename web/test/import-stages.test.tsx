/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Where each imported session has got to (#2230): the stage in plain words, the link to answer
// names, the one "couldn't check every session" line, and when the page asks again. The page only
// shows what the API says; a missing stage (an older API) falls back to the plain state words.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ImportProgress } from "../src/account/api";
import { everyImportStage, mockApi } from "../src/account/mock";
import Import from "../src/account/Import";
import { importPage as w } from "../src/content/import-words";

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

/** Opens the page, runs the import, and shows `progress` (the same answer on every ask). */
async function importing(progress: ImportProgress) {
  const api = mockApi("table");
  const asks: number[] = [];
  api.importProgress = async () => {
    asks.push(1);
    return progress;
  };
  api.importStart = async () => ({
    importId: "x",
    sessions: [],
    estimate: { sessions: progress.sessions.length, fastCalls: 1 },
  });
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
  return { api, asks };
}

const one = (over: Partial<ImportProgress["sessions"][number]>): ImportProgress => ({
  sessions: [{ file: "a.txt", date: "2026-09-14", state: "working", reason: null, ...over }],
});

describe("the words for each stage", () => {
  it("says every stage in plain words, in the order of the sessions", async () => {
    await importing(everyImportStage);
    for (const text of [
      /2026-09-01 · Tidying the transcript/,
      /2026-09-02 · Looking for names/,
      /2026-09-03 · Looking for names/,
      /2026-09-04 · Waiting for your answers about 3 names/,
      /2026-09-05 · Writing the recap/,
      /2026-09-06 · Writing the recap/,
      /2026-09-07 · Recap ready/,
      /2026-09-08 · The recap couldn't be written\. You can try again from the recap list\./,
      /2026-09-09 · Imported \(no recap\)/,
      /2026-09-10 · Imported/,
      /2026-09-11 · Couldn't import this one/,
    ]) {
      expect(screen.getByText(text)).toBeTruthy();
    }
  });

  it("says 'name' for one and a general line for none", async () => {
    expect(w.waitingForNames(1)).toBe("Waiting for your answers about 1 name");
    expect(w.waitingForNames(0)).toBe("Waiting for your answers about names");
  });

  it("falls back to the old words when the API sends no stage", async () => {
    await importing(one({ state: "working" }));
    expect(screen.getByText(/2026-09-14 · Importing…/)).toBeTruthy();
    cleanup();
    await importing(one({ state: "done" }));
    expect(screen.getByText(/2026-09-14 · Done/)).toBeTruthy();
  });
});

describe("the links", () => {
  it("links 'waiting for names' and 'recap ready' to the recaps, and no other stage", async () => {
    await importing(everyImportStage);
    const links = screen.getAllByRole("link").map((a) => [a.textContent, a.getAttribute("href")]);
    expect(links).toContainEqual([w.namesLink, "/account/recaps"]);
    expect(links).toContainEqual([w.recapLink, "/account/recaps"]);
    expect(links.filter(([t]) => t === w.namesLink || t === w.recapLink)).toHaveLength(2);
  });
});

describe("the name check that was given up", () => {
  it("shows the one line once, however many sessions were not checked", async () => {
    await importing({
      sessions: [
        { file: "a.txt", date: "2026-09-14", state: "done", reason: null, stage: "done", namesUnchecked: true },
        { file: "b.txt", date: "2026-09-21", state: "done", reason: null, stage: "done", namesUnchecked: true },
      ],
    });
    expect(screen.getAllByText(w.namesUnchecked)).toHaveLength(1);
  });

  it("shows nothing when every session was checked", async () => {
    await importing(one({ state: "done", stage: "done", namesUnchecked: false }));
    expect(screen.queryByText(w.namesUnchecked)).toBeNull();
  });
});

describe("when the page asks again", () => {
  /** The delays the page asks to wait between asks; the real timers never get to fire them. */
  async function delays(progress: ImportProgress): Promise<number[]> {
    const spy = vi.spyOn(globalThis, "setTimeout");
    await importing(progress);
    return spy.mock.calls.map((c) => Number(c[1])).filter((n) => n === 3000 || n === 60000);
  }

  it("asks every few seconds while something is being worked on", async () => {
    expect(await delays(one({ state: "working", stage: "cleaning" }))).toContain(3000);
  });

  it("asks once a minute while every unfinished session only waits for names", async () => {
    const seen = await delays({
      sessions: [
        { file: "a.txt", date: "2026-09-14", state: "done", reason: null, stage: "waiting_for_names", namesToAnswer: 2 },
        { file: "b.txt", date: "2026-09-21", state: "done", reason: null, stage: "recap_ready" },
      ],
    });
    expect(seen).toContain(60000);
    expect(seen).not.toContain(3000);
  });

  it("keeps the quick pace when one session waits for names and another is working", async () => {
    const seen = await delays({
      sessions: [
        { file: "a.txt", date: "2026-09-14", state: "done", reason: null, stage: "waiting_for_names" },
        { file: "b.txt", date: "2026-09-21", state: "working", reason: null, stage: "writing_recap" },
      ],
    });
    expect(seen).toContain(3000);
    expect(seen).not.toContain(60000);
  });

  it("stops asking when every session is final, and says it is all done", async () => {
    const seen = await delays({
      sessions: [
        { file: "a.txt", date: "2026-09-14", state: "done", reason: null, stage: "recap_failed" },
        { file: "b.txt", date: "2026-09-21", state: "done", reason: null, stage: "recap_not_written" },
        { file: "c.txt", date: "2026-09-28", state: "failed", reason: null, stage: "failed" },
      ],
    });
    expect(seen).toEqual([]);
    expect(screen.getByText(w.someFailed, { exact: false })).toBeTruthy();
  });

  it("does not say all done while a session still waits for names", async () => {
    await importing(one({ state: "done", stage: "waiting_for_names", namesToAnswer: 1 }));
    expect(screen.queryByText(w.allDone, { exact: false })).toBeNull();
    expect(screen.getByText(w.doneCount(0, 1))).toBeTruthy();
  });

  it("falls back to the old rule without a stage: stops when done", async () => {
    expect(await delays(one({ state: "done" }))).toEqual([]);
  });
});
