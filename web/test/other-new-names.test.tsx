/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Other new names" on the Questions page (#5186, core #5203): the names no recap has linked come last in the names panel, under
// their own heading, and open the same card. The page never says nothing waits while a count is above 0.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import QuestionsNames from "../src/account/QuestionsNames";
import { questionsPage as w } from "../src/content/questions-words";

vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAP_NAME_CHECKS_LIVE() {
      return true;
    },
    get RECAPS_LIVE() {
      return true;
    },
  };
});

afterEach(() => cleanup());

function withList(recaps: { ref: string; at: number; count: number; other?: boolean }[]) {
  const api = mockApi("table");
  api.campaignNameChecks = async () => ({ recaps });
  return api;
}

describe("the names panel", () => {
  it("lists the sessions, then Other new names last, each under its own heading", async () => {
    const api = withList([
      { ref: "100.cmp1.100", at: 1_799_000_000, count: 3 },
      { ref: "100.cmp1.0", at: 0, count: 1, other: true },
    ]);
    render(<QuestionsNames api={api} campaignRef="100.cmp1" />);
    const headings = (await screen.findAllByRole("heading", { level: 3 })).map((h) => h.textContent);
    expect(headings[0]).toMatch(/^Session of /);
    expect(headings[1]).toBe(w.otherNames);
    expect(headings.filter((h) => h === w.otherNames)).toHaveLength(1);
  });

  it("an older API with no `other` key reads every group as a session", async () => {
    render(<QuestionsNames api={withList([{ ref: "100.cmp1.100", at: 1_799_000_000, count: 3 }])} campaignRef="100.cmp1" />);
    const headings = (await screen.findAllByRole("heading", { level: 3 })).map((h) => h.textContent);
    expect(headings.some((h) => h === w.otherNames)).toBe(false);
  });

  it("with only Other new names it still lists them, never 'nothing is waiting'", async () => {
    render(<QuestionsNames api={withList([{ ref: "100.cmp1.0", at: 0, count: 1, other: true }])} campaignRef="100.cmp1" waiting={1} />);
    expect(await screen.findByRole("heading", { level: 3, name: w.otherNames })).toBeTruthy();
    expect(screen.queryByText(w.noneHere)).toBeNull();
  });
});
