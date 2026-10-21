/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Write a recap of this session" on the Import sessions page (#2221, the web half of #2106 step
// 2): a tick for each session and one for all, offered only while recaps are on for the site, and
// sent as `writeRecap: true` for the ticked sessions only. Whether a recap is really written is
// the bot's to decide (the DM's choice, the plan); the page only sends what was ticked.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ImportPlan } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Import from "../src/account/Import";
import { importPage as w } from "../src/content/import-words";

const live = vi.hoisted(() => ({ import: false, recaps: false }));
vi.mock("../src/content/import-words", async (original) => {
  const real = await original<typeof import("../src/content/import-words")>();
  return {
    ...real,
    get IMPORT_LIVE() {
      return live.import;
    },
  };
});
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAPS_LIVE() {
      return live.recaps;
    },
  };
});

afterEach(() => {
  cleanup();
  live.import = false;
  live.recaps = false;
});

const path = "/account/import/100.cmp1";
const file = (name: string, text: string): File => new File([text], name, { type: "text/plain" });
const s1 = file("session-1-2026-09-14.txt", "Cerric: hello\nAsh: hi\nMira: ok\nCerric: go");
const s2 = file("session-2.txt", "Cerric: again");

/** Opens the page, previews two files, fills the date, and runs Check: the confirm step. */
async function toConfirm(recapsOn: boolean) {
  live.import = true;
  live.recaps = recapsOn;
  const api = mockApi("table");
  const plans: ImportPlan[] = [];
  const start = api.importStart!.bind(api);
  api.importStart = async (ref, files, plan, links) => {
    plans.push(structuredClone(plan));
    return start(ref, files, plan, links);
  };
  render(<Import api={api} path={path} />);
  const input = screen.getByLabelText(w.pickLabel, { selector: "input" }) as HTMLInputElement;
  Object.defineProperty(input, "files", { value: [s1, s2], configurable: true });
  fireEvent.change(input);
  fireEvent.click(screen.getByRole("button", { name: w.preview }));
  await screen.findByRole("heading", { name: w.datesHeading });
  fireEvent.input(screen.getByLabelText(w.dateLabel("session-2.txt")), {
    target: { value: "2026-09-21" },
  });
  fireEvent.click(screen.getByRole("button", { name: w.checkButton }));
  await screen.findByText(/^Checked:/);
  return { api, plans };
}

const box = (label: string) => screen.getByRole("checkbox", { name: label }) as HTMLInputElement;
const one = (name: string) => box(w.recapsOne(name));
const importNow = () => fireEvent.click(screen.getByRole("button", { name: w.importButton }));

describe("the choice", () => {
  it("is not there while recaps are off for the site", async () => {
    await toConfirm(false);
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByText(w.recapsNote)).toBeNull();
  });

  it("has a tick for each session, none ticked, a tick for all, and the line under them", async () => {
    await toConfirm(true);
    expect(one("session-1-2026-09-14.txt").checked).toBe(false);
    expect(one("session-2.txt").checked).toBe(false);
    expect(box(w.recapsAll).checked).toBe(false);
    expect(screen.getByText(w.recapsNote)).toBeTruthy();
    expect(w.recapsNote).toBe(
      "Recaps wait until you've answered DMbot's questions about names, like after a live session. You'll get one message in Discord when they start.",
    );
  });

  it("'Write recaps for all' ticks every session, and unticks them again", async () => {
    await toConfirm(true);
    fireEvent.click(box(w.recapsAll));
    expect(one("session-1-2026-09-14.txt").checked).toBe(true);
    expect(one("session-2.txt").checked).toBe(true);
    fireEvent.click(box(w.recapsAll));
    expect(one("session-2.txt").checked).toBe(false);
  });

  it("shows 'all' as partly ticked when only some are", async () => {
    await toConfirm(true);
    fireEvent.click(one("session-2.txt"));
    expect(box(w.recapsAll).indeterminate).toBe(true);
    expect(box(w.recapsAll).checked).toBe(false);
    fireEvent.click(one("session-1-2026-09-14.txt"));
    expect(box(w.recapsAll).checked).toBe(true);
    expect(box(w.recapsAll).indeterminate).toBe(false);
  });
});

describe("what is sent", () => {
  it("sends writeRecap: true for the ticked sessions only", async () => {
    const { plans } = await toConfirm(true);
    fireEvent.click(one("session-2.txt"));
    importNow();
    await waitFor(() => expect(plans).toHaveLength(1));
    const [first, second] = plans[0]!.sessions;
    expect(first).toEqual({ file: "session-1-2026-09-14.txt", date: "2026-09-14", order: 1 });
    expect(second).toEqual({ file: "session-2.txt", date: "2026-09-21", order: 2, writeRecap: true });
    expect("writeRecap" in first!).toBe(false); // nothing at all for an unticked one
  });

  it("sends nothing about recaps when nothing is ticked", async () => {
    const { plans } = await toConfirm(true);
    importNow();
    await waitFor(() => expect(plans).toHaveLength(1));
    expect(JSON.stringify(plans[0])).not.toContain("writeRecap");
  });

  it("sends every session as true after 'all'", async () => {
    const { plans } = await toConfirm(true);
    fireEvent.click(box(w.recapsAll));
    importNow();
    await waitFor(() => expect(plans).toHaveLength(1));
    expect(plans[0]!.sessions.map((s) => s.writeRecap)).toEqual([true, true]);
  });

  it("sends nothing about recaps when they are off, even if the page once had ticks", async () => {
    const { plans } = await toConfirm(false);
    importNow();
    await waitFor(() => expect(plans).toHaveLength(1));
    expect(JSON.stringify(plans[0])).not.toContain("writeRecap");
  });

  it("follows the order the DM chose, not the order of the files", async () => {
    const { plans } = await toConfirm(true);
    fireEvent.click(one("session-1-2026-09-14.txt"));
    fireEvent.click(screen.getByRole("button", { name: w.moveUp("session-2.txt") }));
    importNow();
    await waitFor(() => expect(plans).toHaveLength(1));
    expect(plans[0]!.sessions.map((s) => [s.file, s.writeRecap ?? false])).toEqual([
      ["session-2.txt", false],
      ["session-1-2026-09-14.txt", true],
    ]);
  });
});
