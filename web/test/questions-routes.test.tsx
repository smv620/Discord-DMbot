/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The Questions page's links for the three kinds answered on another page (#4841 A): world notes and story threads go to the
// Names page, maps to the Maps page, each only while its own switch is on. Off, the line says to answer in Discord, as before.
import { cleanup, render, screen, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import Questions from "../src/account/Questions";
import { kindWords, questionsPage as w } from "../src/content/questions-words";
import type { QuestionCounts } from "../src/account/api";

const sw = vi.hoisted(() => ({ worldNotes: false, threads: false, maps: false, heard: false, rules: false }));
// The page itself is on here; the three kinds' own switches are set per test.
vi.mock("../src/content/questions-switch", () => ({ QUESTIONS_LIVE: true }));
vi.mock("../src/content/world-notes-switch", () => ({
  get WORLD_NOTES_LIVE() {
    return sw.worldNotes;
  },
}));
vi.mock("../src/content/threads-switch", () => ({
  get THREADS_LIVE() {
    return sw.threads;
  },
}));
vi.mock("../src/content/heard-switch", () => ({
  get HEARD_LIVE() {
    return sw.heard;
  },
}));
vi.mock("../src/content/rule-updates-switch", () => ({
  get RULE_UPDATES_LIVE() {
    return sw.rules;
  },
}));
vi.mock("../src/content/maps-switch", () => ({
  get MAPS_LIVE() {
    return sw.maps;
  },
}));

afterEach(() => {
  cleanup();
  sw.worldNotes = false;
  sw.threads = false;
  sw.maps = false;
  sw.heard = false;
  sw.rules = false;
});

const now = Math.floor(Date.now() / 1000);
const REF = "100.cmp1";
const oneKind = (kind: string): QuestionCounts => ({
  serverNow: now,
  campaigns: [{ ref: REF, name: "The Ashen Road", total: 2, countedAt: now - 30, kinds: [{ kind, count: 2, oldest: now - 100 }] }],
});

/** The line for one kind on the page, once it has loaded. */
async function lineFor(kind: string): Promise<HTMLElement> {
  const api = mockApi("table");
  api.myQuestions = async () => oneKind(kind);
  render(<Questions api={api} />);
  const label = `${kindWords(kind).label} · 2`;
  return (await screen.findByText(label)).closest("li") as HTMLElement;
}

describe("the Questions page links the kinds answered on another page (#4841 A)", () => {
  it("world notes link to the Names page with its words while the switch is on", async () => {
    sw.worldNotes = true;
    const line = await lineFor("world_notes");
    const link = within(line).getByRole("link", { name: "Open the Names page" });
    expect(link.getAttribute("href")).toBe(`/account/names/${encodeURIComponent(REF)}`);
    expect(within(line).queryByText(w.discord)).toBeNull();
  });

  it("world notes say to answer in Discord, with no link, while the switch is off", async () => {
    const line = await lineFor("world_notes");
    expect(within(line).queryByRole("link")).toBeNull();
    expect(within(line).getByText(w.discord)).toBeTruthy();
  });

  it("story threads link to the Names page with its words while the switch is on", async () => {
    sw.threads = true;
    const line = await lineFor("threads");
    const link = within(line).getByRole("link", { name: "Open the Names page" });
    expect(link.getAttribute("href")).toBe(`/account/names/${encodeURIComponent(REF)}`);
  });

  it("story threads say to answer in Discord, with no link, while the switch is off", async () => {
    const line = await lineFor("threads");
    expect(within(line).queryByRole("link")).toBeNull();
    expect(within(line).getByText(w.discord)).toBeTruthy();
  });

  it("maps link to the Maps page with its words while the switch is on", async () => {
    sw.maps = true;
    const line = await lineFor("maps");
    const link = within(line).getByRole("link", { name: "Open the Maps page" });
    expect(link.getAttribute("href")).toBe(`/account/maps/${encodeURIComponent(REF)}`);
  });

  it("maps say to answer in Discord, with no link, while the switch is off", async () => {
    const line = await lineFor("maps");
    expect(within(line).queryByRole("link")).toBeNull();
    expect(within(line).getByText(w.discord)).toBeTruthy();
  });

  it("names that look alike say where in Discord, with no link to a page that cannot answer them (#5259)", async () => {
    const line = await lineFor("lookalikes");
    expect(within(line).queryByRole("link")).toBeNull();
    expect(within(line).getByText("Answer in Discord: ⚙️ Manage DMbot → 📇 Names → 🔗 Look-alikes.")).toBeTruthy();
  });

  it("Did they mean…? is answered on the page while its switch is on, else says how in Discord (#4857)", async () => {
    sw.heard = true;
    const on = await lineFor("heard");
    expect(within(on).getByRole("button", { name: "Answer them here: Did they mean…?" })).toBeTruthy();
    cleanup();
    sw.heard = false;
    const off = await lineFor("heard");
    expect(within(off).queryByRole("button", { name: /Answer them here/ })).toBeNull();
    expect(within(off).getByText(w.discord)).toBeTruthy();
  });

  it("house rule updates link to the House rules page while the switch is on, else say how in Discord", async () => {
    sw.rules = true;
    const on = await lineFor("rules");
    expect(within(on).getByRole("link", { name: "Open the House rules page" }).getAttribute("href")).toBe(`/account/house-rules/${encodeURIComponent(REF)}`);
    cleanup();
    sw.rules = false;
    const off = await lineFor("rules");
    expect(within(off).queryByRole("link")).toBeNull();
    expect(within(off).getByText(w.discord)).toBeTruthy();
  });

  it("the House rule updates line has both links while RULE_UPDATES_LIVE is on, and says to answer in Discord while it is off (#4875)", async () => {
    sw.rules = true;
    const on = await lineFor("rules");
    expect(within(on).getByRole("link", { name: "Open the House rules page" }).getAttribute("href")).toBe(`/account/house-rules/${encodeURIComponent(REF)}`);
    expect(within(on).getByRole("link", { name: "Open the Shared rules page" }).getAttribute("href")).toBe("/account/sharing");
    cleanup();
    sw.rules = false;
    const off = await lineFor("rules");
    expect(within(off).queryByRole("link")).toBeNull();
    expect(within(off).getByText(w.discord)).toBeTruthy();
  });
});
