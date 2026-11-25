/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Add all" under a finished import on the Import page (#5276, the import side of #3207; core #5277): the names the scan found, by kind,
// added at once through the same press as Upload lore's, with the import's id under its own key. Nothing shows to a player (no counts)
// or when the import found none.
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ImportProgress } from "../src/account/api";
import Import from "../src/account/Import";
import { mockApi } from "../src/account/mock";
import { importPage as w } from "../src/content/import-words";
import namesArea from "../src/account/api-names";
import { addAllWords as aw } from "../src/content/add-all-words";

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

const answer = (waiting?: Record<string, number>): ImportProgress => ({
  import: { stage: "done", state: "done", serverNow: now(), startedAt: now() - 300, updatedAt: now() - 100 },
  ...(waiting ? { waiting } : {}),
  sessions: [{ file: "a.txt", date: "2026-09-14", state: "done", reason: null, stage: "done" }],
});

describe("Add all under an import", () => {
  it("shows the total and the biggest kinds, and the press sends the import's id", async () => {
    const { api } = await importing([answer({ place: 5, character: 7 })]);
    fireEvent.click(await screen.findByRole("button", { name: aw.addAll(12) }));
    expect(api.calls.some((c) => c.startsWith("confirmKinds:100.cmp1:x:all") && c.endsWith(":import"))).toBe(true);
  });

  it("one kind sends just that kind", async () => {
    const { api } = await importing([answer({ place: 5, character: 7 })]);
    fireEvent.click(await screen.findByRole("button", { name: aw.addKind(5, "places") }));
    expect(api.calls.some((c) => c.startsWith("confirmKinds:100.cmp1:x:place") && c.endsWith(":import"))).toBe(true);
  });

  it("shows nothing for an import that found no names, or when the API sends no counts", async () => {
    await importing([answer({})]);
    await screen.findByText(w.progressHeading);
    expect(screen.queryByText(aw.nothingLeft)).toBeNull();
    cleanup();
    await importing([answer()]);
    await screen.findByText(w.progressHeading);
    expect(screen.queryByRole("button", { name: /^Add all/ })).toBeNull();
  });

  it("keeps its result and Undo after the counts go to zero", async () => {
    let waiting: Record<string, number> = { place: 5, character: 7 };
    const { api } = await importing([answer(waiting)]);
    api.importProgress = async () => answer(waiting);
    api.confirmAnswer = async () => ({ state: "done", action: "add", counts: { place: 5, character: 7 }, undo: true, progress: null }) as never;
    fireEvent.click(await screen.findByRole("button", { name: aw.addAll(12) }));
    expect(await screen.findByRole("button", { name: aw.undo }, { timeout: 8000 })).toBeTruthy();
    waiting = {};
    expect(screen.getByRole("button", { name: aw.undo })).toBeTruthy();
  }, 20000);

  it("shows the total and the nothing-left line only when there is something to add", async () => {
    await importing([answer({ place: 2 })]);
    expect(await screen.findByText(aw.found(2))).toBeTruthy();
    expect(screen.queryByText(aw.nothingLeft)).toBeNull();
  });
});

describe("the real press", () => {
  it("sends the import's id under its own key, and an upload's under upload", async () => {
    const call = vi.fn(async () => ({ answer: "a" }));
    const area = namesArea({ call, waitForAnswer: vi.fn(), root: "" } as never);
    await area.confirmKinds!("100.cmp1", "x", "all", "import");
    await area.confirmKinds!("100.cmp1", "up-1", ["place"]);
    expect(call).toHaveBeenNthCalledWith(1, "POST", "/me/campaigns/100.cmp1/all-names-confirm", { action: "confirm-kinds", import: "x", kinds: "all" });
    expect(call).toHaveBeenNthCalledWith(2, "POST", "/me/campaigns/100.cmp1/all-names-confirm", { action: "confirm-kinds", upload: "up-1", kinds: ["place"] });
  });
});
