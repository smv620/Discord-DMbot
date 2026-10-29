/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The admin support page's "Needs the team" list (#1485): oldest first with how long each has
// waited; the other filters keep the API's order and show no waiting words.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Tickets, { waitingSince } from "../src/admin/Tickets";
import { type AdminApi, type SupportTicketRow, type TicketStatus } from "../src/admin/api";
import { waitingFor } from "../src/content/admin";

const NOW = 1_800_000_000;

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW * 1000);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const row = (id: number, ageS: number, status: TicketStatus = "escalated"): SupportTicketRow => ({
  id,
  at: NOW - ageS,
  discordId: "123456789012345678",
  dmbotId: "1P0Z4F6N2K9",
  name: "Alice",
  plan: "table",
  subject: `Ticket ${id}`,
  message: "Words",
  source: "website",
  status,
  email: "alice@example.com",
});

/** As the real API answers: newest first, only the filter's status. */
function api(rows: SupportTicketRow[]): AdminApi {
  const counts = { new: 0, answered: 0, closed: 0, escalated: 0 };
  for (const r of rows) counts[r.status] += 1;
  return {
    tickets: vi.fn(async (filter = "all") => ({
      tickets: rows
        .filter((r) => filter === "all" || r.status === filter)
        .sort((a, b) => b.at - a.at || b.id - a.id),
      counts,
    })),
  } as unknown as AdminApi;
}

const subjects = () => screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);

describe("the age words", () => {
  it.each([
    [0, "waiting 1 min"],
    [10 * 60, "waiting 10 min"],
    [59 * 60, "waiting 59 min"],
    [3599, "waiting 59 min"],
    [3 * 3600, "waiting 3 h"],
    [3 * 3600 + 3000, "waiting 3 h"],
    [86_399, "waiting 23 h"],
    [36 * 3600, "waiting 1 day"],
    [23 * 3600, "waiting 23 h"],
    [24 * 3600, "waiting 1 day"],
    [2 * 86_400 + 3600, "waiting 2 days"],
    [10 * 86_400, "waiting 10 days"],
    [-30, "waiting 1 min"],
  ])("%i seconds is %s", (seconds, words) => {
    expect(waitingFor(seconds)).toBe(words);
  });
});

describe("Needs the team", () => {
  const rows = [row(1, 2 * 86_400), row(2, 3 * 3600), row(3, 10 * 60)];

  it("lists the oldest first, each with how long it has waited", async () => {
    render(<Tickets api={api(rows)} csrf="c" onSignedOut={vi.fn()} onOff={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Needs the team/ }));
    await waitFor(() => expect(subjects()).toEqual(["Ticket 1", "Ticket 2", "Ticket 3"]));
    const items = screen.getAllByRole("listitem").filter((li) => li.getAttribute("data-ticket"));
    expect(items.map((li) => /waiting \d+ (?:min|h|days?)/.exec(li.textContent ?? "")?.[0])).toEqual([
      "waiting 2 days",
      "waiting 3 h",
      "waiting 10 min",
    ]);
  });

  it("breaks a tie by the ticket's number, oldest number first", async () => {
    render(
      <Tickets
        api={api([row(5, 3600), row(4, 3600)])}
        csrf="c"
        onSignedOut={vi.fn()}
        onOff={vi.fn()}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Needs the team/ }));
    await waitFor(() => expect(subjects()).toEqual(["Ticket 4", "Ticket 5"]));
  });
});

describe("the other filters", () => {
  const rows = [
    row(1, 2 * 86_400, "new"),
    row(2, 3 * 3600, "new"),
    row(3, 10 * 60, "new"),
    row(4, 5000, "answered"),
  ];

  it("keep the API's order (newest first) and say nothing about waiting", async () => {
    render(<Tickets api={api(rows)} csrf="c" onSignedOut={vi.fn()} onOff={vi.fn()} />);
    // The page opens on "Needs the team"; the newest-first filters are the others.
    fireEvent.click(await screen.findByRole("button", { name: /Needs a reply/ }));
    await waitFor(() => expect(subjects()).toEqual(["Ticket 3", "Ticket 2", "Ticket 1"]));
    expect(document.body.textContent).not.toMatch(/waiting \d/);
    fireEvent.click(screen.getByRole("button", { name: /All/ }));
    await waitFor(() => expect(subjects()).toEqual(["Ticket 3", "Ticket 4", "Ticket 2", "Ticket 1"]));
    expect(document.body.textContent).not.toMatch(/waiting \d/);
  });
});

describe("counting from the hand-over (#1494)", () => {
  const handed = (id: number, ageS: number, handedAgeS: number | null | undefined): SupportTicketRow => {
    const r = row(id, ageS);
    if (handedAgeS !== undefined) r.handedOverAt = handedAgeS === null ? null : NOW - handedAgeS;
    return r;
  };

  it("takes the hand-over time when it is there, else the ticket's own time", () => {
    expect(waitingSince(handed(1, 2 * 86_400, 3600))).toBe(NOW - 3600); // present
    expect(waitingSince(handed(2, 2 * 86_400, null))).toBe(NOW - 2 * 86_400); // never handed over
    expect(waitingSince(handed(3, 2 * 86_400, undefined))).toBe(NOW - 2 * 86_400); // an older API
  });

  it("says the team's own wait for each of the three cases", async () => {
    // Made two days ago and handed over three hours ago; never handed over; from an older API.
    const rows = [handed(1, 2 * 86_400, 3 * 3600), handed(2, 5 * 3600, null), handed(3, 40 * 60, undefined)];
    render(<Tickets api={api(rows)} csrf="c" onSignedOut={vi.fn()} onOff={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: /Needs the team/ }));
    await waitFor(() => expect(subjects()).toEqual(["Ticket 2", "Ticket 1", "Ticket 3"]));
    const items = screen.getAllByRole("listitem").filter((li) => li.getAttribute("data-ticket"));
    const said = Object.fromEntries(
      items.map((li) => [li.getAttribute("data-ticket"), /waiting \d+ (?:min|h|days?)/.exec(li.textContent ?? "")?.[0]]),
    );
    expect(said).toEqual({ "1": "waiting 3 h", "2": "waiting 5 h", "3": "waiting 40 min" });
  });
});
