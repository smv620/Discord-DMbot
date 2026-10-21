/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The links on the Import page's progress view go to each session's own recap when the API names
// it (#2240, #2241), and to the recap list when it does not (an older API, or no recap yet).
import { cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ImportProgress } from "../src/account/api";
import { mockApi } from "../src/account/mock";
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
  live.on = false;
});

const s1 = new File(["Cerric: hello\nAsh: hi"], "session-1-2026-09-14.txt", { type: "text/plain" });

async function showing(progress: ImportProgress) {
  const api = mockApi("table");
  api.importProgress = async () => progress;
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
}

const row = (file: string, stage: NonNullable<ImportProgress["sessions"][number]["stage"]>, recap?: string | null) => ({
  file,
  date: "2026-09-14",
  state: "done" as const,
  reason: null,
  stage,
  namesToAnswer: 2,
  ...(recap === undefined ? {} : { recap }),
});

const hrefOf = (name: string): string | null =>
  screen.getByRole("link", { name }).getAttribute("href");

describe("each link goes to the session's own recap", () => {
  it("links 'Recap ready' to that recap when the API names it", async () => {
    await showing({ sessions: [row("a.txt", "recap_ready", "ref-a_1")] });
    expect(hrefOf(w.linkFor(w.recapLink, "a.txt"))).toBe("/account/recaps/ref-a_1");
  });

  it("links 'Waiting for your answers' to the same page", async () => {
    await showing({ sessions: [row("a.txt", "waiting_for_names", "ref-a_1")] });
    expect(hrefOf(w.linkFor(w.namesLink, "a.txt"))).toBe("/account/recaps/ref-a_1");
  });

  it("links a failed recap to its page too", async () => {
    await showing({ sessions: [row("a.txt", "recap_failed", "ref-a_1")] });
    expect(hrefOf(w.linkFor(w.recapLink, "a.txt"))).toBe("/account/recaps/ref-a_1");
  });

  it("gives each session its own link", async () => {
    await showing({
      sessions: [row("a.txt", "recap_ready", "ref-a"), row("b.txt", "waiting_for_names", "ref-b")],
    });
    expect(hrefOf(w.linkFor(w.recapLink, "a.txt"))).toBe("/account/recaps/ref-a");
    expect(hrefOf(w.linkFor(w.namesLink, "b.txt"))).toBe("/account/recaps/ref-b");
  });

  it("encodes the reference so it can't change the address", async () => {
    await showing({ sessions: [row("a.txt", "recap_ready", "a/b?c#d")] });
    expect(hrefOf(w.linkFor(w.recapLink, "a.txt"))).toBe("/account/recaps/a%2Fb%3Fc%23d");
  });
});

describe("without a reference the links go to the list", () => {
  it("keeps the list link when the API sends no recap field", async () => {
    await showing({
      sessions: [row("a.txt", "recap_ready"), row("b.txt", "waiting_for_names")],
    });
    expect(hrefOf(w.linkFor(w.recapLink, "a.txt"))).toBe("/account/recaps");
    expect(hrefOf(w.linkFor(w.namesLink, "b.txt"))).toBe("/account/recaps");
  });

  it("keeps the list link when the recap is null", async () => {
    await showing({ sessions: [row("a.txt", "recap_ready", null)] });
    expect(hrefOf(w.linkFor(w.recapLink, "a.txt"))).toBe("/account/recaps");
  });
});
