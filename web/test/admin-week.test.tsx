/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The admin support page's "This week" line (#1480): the sentence for each shape of the numbers,
// the empty week, an older API that sends none, and that no ticket words are needed to read it.
import { cleanup, render, screen } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Tickets from "../src/admin/Tickets";
import { type AdminApi, httpAdminApi, type TicketWeek } from "../src/admin/api";
import { weekLine } from "../src/content/admin";

afterEach(cleanup);

const COUNTS = { new: 0, answered: 0, closed: 0, escalated: 0 };
const none = {
  unhappy: 0,
  asked: 0,
  limit: 0,
  "no-email": 0,
  "helper-down": 0,
};

const WEEK: TicketWeek = {
  questions: 12,
  helper: 9,
  team: 2,
  waiting: 1,
  reasons: { ...none, unhappy: 1, asked: 1 },
};

function page(week: TicketWeek | undefined) {
  const api = {
    tickets: vi.fn(async () => ({ tickets: [], counts: COUNTS, week })),
  } as unknown as AdminApi;
  render(<Tickets api={api} csrf="c" onSignedOut={vi.fn()} onOff={vi.fn()} />);
}

describe("the sentence", () => {
  it("says the week in one line", () => {
    expect(weekLine(WEEK)).toBe(
      "This week: 12 questions, 9 answered by DMbot's helper, 2 by the team, 1 waiting. Helper handed over 2 (unhappy 1, asked 1).",
    );
  });

  it("says one question as one, and leaves out the hand-over part when there were none", () => {
    expect(
      weekLine({ questions: 1, helper: 0, team: 0, waiting: 1, reasons: none }),
    ).toBe(
      "This week: 1 question, 0 answered by DMbot's helper, 0 by the team, 1 waiting.",
    );
  });

  it("says no questions plainly", () => {
    expect(
      weekLine({ ...WEEK, questions: 0, helper: 0, team: 0, waiting: 0 }),
    ).toBe("No questions this week.");
  });

  it("names the other reasons in plain words, and counts a code it does not know", () => {
    const line = weekLine({
      ...WEEK,
      reasons: { ...none, limit: 1, "no-email": 2, "helper-down": 1, odd: 3 },
    });
    expect(line).toContain(
      "Helper handed over 7 (five answers used 1, no email 2, helper down 1).",
    );
  });
});

describe("on the page", () => {
  it("shows the line above the filters", async () => {
    page(WEEK);
    expect(await screen.findByText(weekLine(WEEK))).toBeTruthy();
  });

  it("shows the empty week", async () => {
    page({ questions: 0, helper: 0, team: 0, waiting: 0, reasons: none });
    expect(await screen.findByText("No questions this week.")).toBeTruthy();
  });

  it("shows nothing from an older API that sends no week", async () => {
    page(undefined);
    await screen.findByText(/Tickets|tickets/);
    expect(screen.queryByText(/This week|No questions this week/)).toBeNull();
  });
});

describe("the API call", () => {
  const reply = (body: unknown) =>
    vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));

  it("passes the week through when it is all numbers", async () => {
    const found = await httpAdminApi(
      "https://api.example",
      reply({ tickets: [], counts: COUNTS, week: WEEK }),
    ).tickets();
    expect(found.week).toEqual(WEEK);
  });

  it("drops a week that is not numbers, and a reason that is not a number", async () => {
    const bad = { ...WEEK, questions: "12" };
    expect(
      (
        await httpAdminApi(
          "https://api.example",
          reply({ tickets: [], counts: COUNTS, week: bad }),
        ).tickets()
      ).week,
    ).toBeUndefined();
    const odd = { ...WEEK, reasons: { unhappy: 1, asked: "x" } };
    expect(
      (
        await httpAdminApi(
          "https://api.example",
          reply({ tickets: [], counts: COUNTS, week: odd }),
        ).tickets()
      ).week?.reasons,
    ).toEqual({ unhappy: 1 });
  });
});
