/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The admin Tickets list as pages (#2211): twenty a page within the filter, the same page bar as
// Recent changes (one shared component), the page kept after a close so the team keeps its place,
// and the page before when the last ticket of the last page leaves it.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type AdminApi,
  AdminApiError,
  httpAdminApi,
  type SupportTicketRow,
  type TicketCounts,
  type TicketFilter,
} from "../src/admin/api";
import Tickets from "../src/admin/Tickets";
import { text } from "../src/content/admin";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const SIZE = 20;

const row = (id: number, status: SupportTicketRow["status"] = "new"): SupportTicketRow => ({
  id,
  at: 1_800_000_000 - id,
  discordId: "123456789012345678",
  dmbotId: "1P0Z4F6N2K9",
  name: "Alice",
  plan: "table",
  subject: `Ticket ${String(id).padStart(3, "0")}`,
  message: `Words of ${id}`,
  source: "website",
  status,
  email: `p${id}@example.com`,
});

const countsOf = (rows: SupportTicketRow[]): TicketCounts => ({
  new: rows.filter((r) => r.status === "new").length,
  answered: rows.filter((r) => r.status === "answered").length,
  closed: rows.filter((r) => r.status === "closed").length,
  escalated: rows.filter((r) => r.status === "escalated").length,
});

/** The API as the real one answers: twenty a page within the filter, newest (lowest id here) first,
 * `stale` for a page that isn't there. */
function paged(rows: SupportTicketRow[], over: Partial<AdminApi> = {}) {
  const api = {
    tickets: vi.fn(async (filter: TicketFilter = "all", page = 1) => {
      const list = rows.filter((r) => filter === "all" || r.status === filter);
      const pages = Math.max(1, Math.ceil(list.length / SIZE));
      if (!Number.isInteger(page) || page < 1 || page > pages) throw new AdminApiError("stale");
      return {
        tickets: structuredClone(list.slice((page - 1) * SIZE, page * SIZE)),
        counts: countsOf(rows),
        page,
        pages,
      };
    }),
    setTicketStatus: vi.fn(async (_csrf: string, id: number, status: SupportTicketRow["status"]) => {
      const ticket = rows.find((r) => r.id === id);
      if (ticket) ticket.status = status;
    }),
    ...over,
  };
  render(<Tickets api={api as unknown as AdminApi} csrf="c" onSignedOut={vi.fn()} onOff={vi.fn()} />);
  return api;
}

const many = (n: number, status: SupportTicketRow["status"] = "new"): SupportTicketRow[] =>
  Array.from({ length: n }, (_, i) => row(i + 1, status));

const nav = () => screen.findByRole("navigation", { name: text.ticketPagesNav });
/** A page-bar button, by its move: a screen reader hears "<move>, page <n>" (#2249); the Go button has no page. */
const button = (move: string) =>
  screen.getByRole("button", { name: new RegExp(`^${move}(, page \\d+)?$`) }) as HTMLButtonElement;
const shown = () => screen.queryAllByRole("heading", { level: 3 }).map((h) => h.textContent);
/** What a screen reader would hear last from the tickets' bar: a refusal if there is one, else the
 * page number. Found by role inside the bar's own notes, not by where they sit. */
const note = () => {
  const notes = within(document.getElementById("tickets-page-notes") as HTMLElement);
  return notes.getByRole("alert").textContent || notes.getByRole("status").textContent;
};

describe("the pages", () => {
  it("shows twenty tickets and the bar, with the page number for a screen reader", async () => {
    paged(many(45));
    await nav();
    expect(shown()).toHaveLength(20);
    expect(shown()[0]).toBe("Ticket 001");
    expect(note()).toBe(text.pageNow(1, 3));
    expect(button(text.ticketPageFirst).disabled).toBe(true);
    expect(button(text.ticketPageNext).disabled).toBe(false);
  });

  it("moves with Older, Oldest, Newer and Newest, and says what each page holds", async () => {
    const api = paged(many(45));
    await nav();
    fireEvent.click(button(text.ticketPageNext));
    await waitFor(() => expect(note()).toBe(text.pageNow(2, 3)));
    expect(shown()[0]).toBe("Ticket 021");
    fireEvent.click(button(text.ticketPageLast));
    await waitFor(() => expect(note()).toBe(text.pageNow(3, 3)));
    expect(shown()).toHaveLength(5);
    fireEvent.click(button(text.ticketPagePrevious));
    await waitFor(() => expect(note()).toBe(text.pageNow(2, 3)));
    fireEvent.click(button(text.ticketPageFirst));
    await waitFor(() => expect(note()).toBe(text.pageNow(1, 3)));
    expect((api.tickets as ReturnType<typeof vi.fn>).mock.calls.map((c: unknown[]) => c[1])).toEqual([1, 2, 3, 2, 1]);
  });

  it("jumps to a typed page, and refuses a number that isn't one", async () => {
    const api = paged(many(45));
    await nav();
    const box = screen.getByLabelText(text.pageBox) as HTMLInputElement;
    fireEvent.input(box, { target: { value: "0" } });
    fireEvent.click(button(text.pageGo));
    expect(note()).toBe(text.pagePick(3));
    expect(api.tickets).toHaveBeenCalledTimes(1); // only the first load
    fireEvent.input(box, { target: { value: "3" } });
    fireEvent.click(button(text.pageGo));
    await waitFor(() => expect(note()).toBe(text.pageNow(3, 3)));
  });

  it("hides the bar for one page, and for an API without pages", async () => {
    paged(many(7));
    await screen.findByText("Ticket 001");
    expect(screen.queryByRole("navigation", { name: text.ticketPagesNav })).toBeNull();
    cleanup();
    paged(many(45), {
      tickets: vi.fn(async () => ({ tickets: many(3), counts: countsOf(many(3)) })),
    } as unknown as Partial<AdminApi>);
    await screen.findByText("Ticket 001");
    expect(screen.queryByRole("navigation", { name: text.ticketPagesNav })).toBeNull();
  });
});

describe("with a filter", () => {
  it("pages within the filtered list, and a new filter starts at page 1", async () => {
    const rows = [...many(30, "new"), ...Array.from({ length: 25 }, (_, i) => row(100 + i, "answered"))];
    const api = paged(rows);
    await nav(); // the page starts on "new": 30 tickets, two pages
    expect(note()).toBe(text.pageNow(1, 2));
    fireEvent.click(button(text.ticketPageNext));
    await waitFor(() => expect(note()).toBe(text.pageNow(2, 2)));
    expect(shown()).toHaveLength(10);
    fireEvent.click(screen.getByRole("button", { name: /Answered/ }));
    await waitFor(() => expect(api.tickets).toHaveBeenLastCalledWith("answered", 1));
    await waitFor(() => expect(note()).toBe(text.pageNow(1, 2)));
    expect(shown()).toHaveLength(20);
    expect(shown()[0]).toBe("Ticket 100");
  });
});

describe("keeping its place", () => {
  it("stays on the same page after a ticket is closed", async () => {
    const rows = many(45);
    const api = paged(rows);
    await nav();
    fireEvent.click(button(text.ticketPageNext));
    await waitFor(() => expect(note()).toBe(text.pageNow(2, 3)));
    const ticket = document.querySelector('[data-ticket="25"]') as HTMLElement;
    fireEvent.click(within(ticket).getByRole("button", { name: text.closeTicket }));
    await waitFor(() => expect(api.setTicketStatus).toHaveBeenCalledWith("c", 25, "closed"));
    await waitFor(() => expect(api.tickets).toHaveBeenLastCalledWith("new", 2));
    await waitFor(() => expect(note()).toBe(text.pageNow(2, 3)));
    expect(shown()).toHaveLength(20);
    expect(shown()).not.toContain("Ticket 025");
  });

  it("stays on the same page after a reply is sent", async () => {
    const rows = many(45);
    const replyToTicket = vi.fn(async () => undefined);
    const api = paged(rows, { replyToTicket } as unknown as Partial<AdminApi>);
    await nav();
    fireEvent.click(button(text.ticketPageNext));
    await waitFor(() => expect(note()).toBe(text.pageNow(2, 3)));
    const ticket = document.querySelector('[data-ticket="25"]') as HTMLElement;
    fireEvent.input(within(ticket).getByLabelText(text.replyLabel("Ticket 025")), {
      target: { value: "Thanks, sorted." },
    });
    fireEvent.click(within(ticket).getByRole("button", { name: text.replySend }));
    await waitFor(() => expect(replyToTicket).toHaveBeenCalled());
    // The reload after the reply asks for the page the team is on, not page 1.
    await waitFor(() => expect(api.tickets).toHaveBeenLastCalledWith("new", 2));
    expect(note()).toBe(text.pageNow(2, 3));
  });

  it("goes to the page before when the last ticket of the last page leaves the list", async () => {
    const rows = many(21);
    const api = paged(rows);
    await nav();
    fireEvent.click(button(text.ticketPageLast));
    await waitFor(() => expect(note()).toBe(text.pageNow(2, 2)));
    expect(shown()).toEqual(["Ticket 021"]);
    fireEvent.click(button(text.closeTicket));
    await waitFor(() => expect(note()).toBe(text.ticketPageGone));
    // Page 2 is gone (stale), so page 1 is read; it is the only page now.
    expect((api.tickets as ReturnType<typeof vi.fn>).mock.calls.map((c: unknown[]) => c[1]).slice(-2)).toEqual([2, 1]);
    expect(shown()).toHaveLength(20);
    expect(screen.queryByRole("navigation", { name: text.ticketPagesNav })).toBeNull();
  });

  it("reloads with the filter that is showing, even if it changed while a close was in flight", async () => {
    const rows = [...many(5, "new"), ...Array.from({ length: 3 }, (_, i) => row(50 + i, "answered"))];
    let release!: () => void;
    const api = paged(rows, {
      setTicketStatus: vi.fn(
        () =>
          new Promise<void>((done) => {
            release = () => {
              const t = rows.find((r) => r.id === 1);
              if (t) t.status = "closed";
              done();
            };
          }),
      ),
    } as unknown as Partial<AdminApi>);
    await nav().catch(() => undefined);
    await screen.findByText("Ticket 001");
    const ticket = document.querySelector('[data-ticket="1"]') as HTMLElement;
    fireEvent.click(within(ticket).getByRole("button", { name: text.closeTicket }));
    // The admin switches to Answered while the close is still being saved.
    fireEvent.click(screen.getByRole("button", { name: /Answered/ }));
    await screen.findByText("Ticket 050");
    release();
    await waitFor(() => expect((api.tickets as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(2));
    // The reload after the close asked for the filter on screen, so Answered is still showing.
    const last = (api.tickets as ReturnType<typeof vi.fn>).mock.calls.at(-1) as [string, number];
    expect(last[0]).toBe("answered");
    expect(shown()).toEqual(["Ticket 050", "Ticket 051", "Ticket 052"]);
  });

  it("puts the keyboard on the page box when the pressed button disables itself", async () => {
    paged(many(45));
    await nav();
    fireEvent.click(button(text.ticketPageNext));
    await waitFor(() => expect(note()).toBe(text.pageNow(2, 3)));
    const newer = button(text.ticketPagePrevious);
    newer.focus();
    fireEvent.click(newer);
    await waitFor(() => expect(note()).toBe(text.pageNow(1, 3)));
    // (happy-dom drops focus from a disabled button to the body; the box takes it.)
    await waitFor(() => expect(document.activeElement).toBe(screen.getByLabelText(text.pageBox)));
  });

  it("says so, and keeps the page, when a page can't be read", async () => {
    let fail = false;
    const rows = many(45);
    const base = paged(rows);
    cleanup();
    const api = paged(rows, {
      tickets: vi.fn(async (filter: TicketFilter = "all", page = 1) => {
        if (fail) throw new AdminApiError("down");
        return base.tickets(filter, page);
      }),
    } as unknown as Partial<AdminApi>);
    await nav();
    fail = true;
    fireEvent.click(button(text.ticketPageNext));
    await waitFor(() => expect(api.tickets).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(text.ticketsDown)).toBeTruthy();
    expect(shown()[0]).toBe("Ticket 001"); // still page 1
    expect((screen.getByLabelText(text.pageBox) as HTMLInputElement).value).toBe("1");
  });
});

describe("the API call", () => {
  it("asks for the page and reads which page and how many came back", async () => {
    const fetcher = vi.fn<typeof fetch>(
      async () =>
        new Response(
          JSON.stringify({
            tickets: [],
            counts: { new: 0, answered: 0, closed: 0, escalated: 0 },
            page: 2,
            pages: 5,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    );
    const api = httpAdminApi("https://api.example", fetcher);
    const list = await api.tickets("new", 2);
    expect((fetcher.mock.calls[0] as unknown as [string])[0]).toBe(
      "https://api.example/admin/support?status=new&page=2",
    );
    expect([list.page, list.pages]).toEqual([2, 5]);
  });

  it("is a stale refusal for a bad page (400 bad_page), and no paging from an API without it", async () => {
    const bad = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ error: "bad_page" }), { status: 400 }),
    );
    await expect(httpAdminApi("https://api.example", bad).tickets("all", 9)).rejects.toMatchObject({
      kind: "stale",
    });
    const old = vi.fn<typeof fetch>(
      async () =>
        new Response(
          JSON.stringify({ tickets: [], counts: { new: 0, answered: 0, closed: 0, escalated: 0 } }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    );
    const list = await httpAdminApi("https://api.example", old).tickets("all");
    expect(list.page).toBeUndefined();
    expect(list.pages).toBeUndefined();
  });
});

describe("one bar for both lists", () => {
  it("is the same component in Recent changes and in the tickets (not a copy)", async () => {
    const { readFileSync } = await import("node:fs");
    const { join } = await import("node:path");
    const read = (f: string) => readFileSync(join(process.cwd(), "src", "admin", f), "utf8");
    expect(read("RecentChanges.tsx")).toContain('import PageBar from "./PageBar"');
    expect(read("Tickets.tsx")).toContain('import PageBar from "./PageBar"');
    // And neither carries its own jump box.
    expect(read("RecentChanges.tsx")).not.toContain("page-jump");
    expect(read("Tickets.tsx")).not.toContain("page-jump");
  });
});
