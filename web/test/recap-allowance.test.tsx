/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "N of M recaps this month" under the recap list (#1444): quiet while some are used, a plain
// "used its recaps" line with the next day when all are, nothing from an older API or while
// recaps are off. Narration is mentioned only while its own switch is on.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockApi } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import type { RecapMonth } from "../src/account/api";
import { allowanceLines, nextMonthStart } from "../src/content/recaps";

const live = vi.hoisted(() => ({ recaps: true, audio: false }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAPS_LIVE() {
      return live.recaps;
    },
    get RECAP_AUDIO_LIVE() {
      return live.audio;
    },
  };
});

afterEach(() => {
  cleanup();
  live.recaps = true;
  live.audio = false;
});

const month = (over: Partial<RecapMonth> = {}): RecapMonth => ({
  serverName: "Thursday Table",
  campaign: "Brynwater Crossing",
  used: 3,
  allowed: 10,
  audioUsed: 2,
  audioAllowed: 8,
  ...over,
});

const oct = new Date("2026-10-16T05:00:00Z");

describe("the lines", () => {
  it("names the next month's first day in UTC, across the year end", () => {
    expect(nextMonthStart(oct)).toBe("1 November");
    expect(nextMonthStart(new Date("2026-12-31T23:59:00Z"))).toBe("1 January");
  });

  it("is quiet while some are used, with narration only when it is on", () => {
    expect(allowanceLines([month()], false, oct)).toEqual([
      "Brynwater Crossing: 3 of 10 recaps used this month",
    ]);
    expect(allowanceLines([month()], true, oct)).toEqual([
      "Brynwater Crossing: 3 of 10 recaps used this month, 2 of 8 narrated",
    ]);
  });

  it("says plainly when all are used, and when the next is", () => {
    expect(allowanceLines([month({ used: 10 })], false, oct)).toEqual([
      "Brynwater Crossing has used all 10 recaps for this month. More come on 1 November.",
    ]);
    expect(allowanceLines([month({ used: 10, audioUsed: 8 })], true, oct)).toEqual([
      "Brynwater Crossing has used all 10 recaps for this month. More come on 1 November.",
      "Brynwater Crossing has used all 8 narrated recaps for this month. More come on 1 November.",
    ]);
  });

  it("says nothing for a campaign with none this month, or from an older API", () => {
    expect(allowanceLines([month({ used: 0, audioUsed: 0 })], true, oct)).toEqual([]);
    expect(allowanceLines(undefined, true, oct)).toEqual([]);
  });
});

describe("on the recap list", () => {
  function withMonths(thisMonth: RecapMonth[]) {
    const base = mockApi("table");
    const recaps = base.recaps!;
    return { ...base, recapList: async () => ({ recaps: await recaps(), thisMonth }) };
  }

  it("shows the line when recaps are live", async () => {
    render(<Recaps api={withMonths([month()])} path="/account/recaps" />);
    expect(await screen.findByText("Brynwater Crossing: 3 of 10 recaps used this month")).toBeTruthy();
  });

  it("shows nothing while recaps are off", async () => {
    live.recaps = false;
    render(<Recaps api={withMonths([month()])} path="/account/recaps" />);
    await screen.findByText("The ferry at dusk");
    expect(screen.queryByText(/recaps used this month/)).toBeNull();
  });

  it("shows nothing from an API without the field", async () => {
    render(<Recaps api={mockApi("table")} path="/account/recaps" />);
    await screen.findByText("The ferry at dusk");
    expect(screen.queryByText(/this month/)).toBeNull();
  });
});
