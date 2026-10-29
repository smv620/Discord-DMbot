/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The admin page's Tickets list (#1375): filter buttons with counts (what needs a reply first),
// and a Copy address button next to each reply address. Team-only: no change to what a person sees.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type AdminApi,
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

const row = (id: number, subject: string, status: SupportTicketRow["status"], email: string | null = `p${id}@example.com`): SupportTicketRow => ({
  id,
  at: 1_800_000_000 - id,
  discordId: "123456789012345678",
  dmbotId: "1P0Z4F6N2K9",
  name: "Alice",
  plan: "table",
  subject,
  message: `Words of ${subject}`,
  source: "website",
  status,
  email,
});

const countsOf = (rows: SupportTicketRow[]): TicketCounts => ({
  new: rows.filter((r) => r.status === "new").length,
  answered: rows.filter((r) => r.status === "answered").length,
  closed: rows.filter((r) => r.status === "closed").length,
  escalated: rows.filter((r) => r.status === "escalated").length,
});

/** The API as the real one answers: only the filter's tickets, and the counts over all of them. */
function filtering(rows: SupportTicketRow[]) {
  const api = {
    tickets: vi.fn(async (filter: TicketFilter = "all") => ({
      tickets: structuredClone(rows.filter((r) => filter === "all" || r.status === filter)),
      counts: countsOf(rows),
    })),
    setTicketStatus: vi.fn(async (_csrf: string, id: number, status: SupportTicketRow["status"]) => {
      const ticket = rows.find((r) => r.id === id);
      if (ticket) ticket.status = status;
    }),
  };
  render(<Tickets api={api as unknown as AdminApi} csrf="c" onSignedOut={vi.fn()} onOff={vi.fn()} />);
  return api;
}

const ROWS = (): SupportTicketRow[] => [
  row(1, "New A", "new"),
  row(2, "New B", "new"),
  row(3, "Answered A", "answered"),
  row(4, "Closed A", "closed"),
];

const button = (name: string | RegExp) => screen.getByRole("button", { name });
const shown = () => screen.queryAllByRole("heading", { level: 3 }).map((h) => h.textContent);

describe("the filter", () => {
  it("starts on what needs a reply and shows a count on each button", async () => {
    const api = filtering(ROWS());
    expect(await screen.findByText("New A")).toBeTruthy();
    expect(api.tickets).toHaveBeenCalledWith("new", 1);
    expect(shown()).toEqual(["New A", "New B"]);
    const group = screen.getByRole("group", { name: text.ticketsFilterLabel });
    expect([...group.querySelectorAll("button")].map((b) => b.textContent)).toEqual([
      "Needs the team\u00a0(0)",
      "Needs a reply\u00a0(2)",
      "Answered\u00a0(1)",
      "Closed\u00a0(1)",
      "All\u00a0(4)",
    ]);
    // Buttons, not a dropdown, and the one in use says so to a screen reader.
    expect(group.querySelector("select")).toBeNull();
    expect(button(/Needs a reply/).getAttribute("aria-pressed")).toBe("true");
    expect(button(/^All/).getAttribute("aria-pressed")).toBe("false");
  });

  it("each button shows only its own tickets", async () => {
    filtering(ROWS());
    await screen.findByText("New A");
    fireEvent.click(button(/^Answered/));
    await waitFor(() => expect(shown()).toEqual(["Answered A"]));
    fireEvent.click(button(/^Closed/));
    await waitFor(() => expect(shown()).toEqual(["Closed A"]));
    fireEvent.click(button(/^All/));
    await waitFor(() => expect(shown()).toEqual(["New A", "New B", "Answered A", "Closed A"]));
    fireEvent.click(button(/^Needs a reply/));
    await waitFor(() => expect(shown()).toEqual(["New A", "New B"]));
    expect(button(/^Needs a reply/).getAttribute("aria-pressed")).toBe("true");
  });

  it("the counts cover every ticket whichever button is on", async () => {
    filtering(ROWS());
    await screen.findByText("New A");
    fireEvent.click(button(/^Closed/));
    await screen.findByText("Closed A");
    expect(button(/^Needs a reply/).textContent).toBe("Needs a reply\u00a0(2)");
    expect(button(/^All/).textContent).toBe("All\u00a0(4)");
  });

  it("a ticket answered from the list leaves it and the counts follow", async () => {
    filtering(ROWS());
    await screen.findByText("New A");
    fireEvent.click(within(screen.getByText("New A").closest("li") as HTMLElement).getByRole("button", { name: text.markAnswered }));
    await waitFor(() => expect(shown()).toEqual(["New B"]));
    expect(button(/^Needs a reply/).textContent).toBe("Needs a reply\u00a0(1)");
    expect(button(/^Answered/).textContent).toBe("Answered\u00a0(2)");
  });

  it("an older, slower answer never replaces a newer one", async () => {
    const rows = ROWS();
    let release: () => void = () => undefined;
    const slow = new Promise<void>((done) => {
      release = done;
    });
    const api = {
      tickets: vi.fn(async (filter: TicketFilter = "all") => {
        if (filter === "answered") await slow; // Answered is asked for first and answers last
        return {
          tickets: rows.filter((r) => filter === "all" || r.status === filter),
          counts: countsOf(rows),
        };
      }),
      setTicketStatus: vi.fn(),
    };
    render(<Tickets api={api as unknown as AdminApi} csrf="c" onSignedOut={vi.fn()} onOff={vi.fn()} />);
    await screen.findByText("New A");
    fireEvent.click(button(/^Answered/));
    fireEvent.click(button(/^Closed/));
    await waitFor(() => expect(shown()).toEqual(["Closed A"]));
    release(); // now the Answered answer arrives, too late
    await new Promise((done) => setTimeout(done, 0));
    expect(shown()).toEqual(["Closed A"]);
    expect(button(/^Closed/).getAttribute("aria-pressed")).toBe("true");
  });

  it("says what is empty, in the words of the view", async () => {
    filtering([row(1, "Closed only", "closed")]);
    expect(await screen.findByText(text.ticketsNoneFor["new"] ?? "")).toBeTruthy();
    fireEvent.click(button(/^Answered/));
    expect(await screen.findByText(text.ticketsNoneFor["answered"] ?? "")).toBeTruthy();
    fireEvent.click(button(/^Closed/));
    expect(await screen.findByText("Closed only")).toBeTruthy();
  });
});

describe("Copy address", () => {
  const clipboard = (write: (value: string) => Promise<void>) =>
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: write } });

  it("puts exactly the address on the clipboard and says so", async () => {
    const write = vi.fn(async () => undefined);
    clipboard(write);
    filtering([row(1, "New A", "new", "alice+tag@example.com")]);
    const item = (await screen.findByText("New A")).closest("li") as HTMLElement;
    fireEvent.click(within(item).getByRole("button", { name: /^Copy address/ }));
    await waitFor(() => expect(write).toHaveBeenCalledWith("alice+tag@example.com"));
    expect((await within(item).findByRole("status")).textContent).toContain(text.addressCopied);
  });

  it("says so when it could not copy, and the address is still there to select", async () => {
    clipboard(async () => {
      throw new Error("not allowed");
    });
    filtering([row(1, "New A", "new", "alice@example.com")]);
    const item = (await screen.findByText("New A")).closest("li") as HTMLElement;
    fireEvent.click(within(item).getByRole("button", { name: /^Copy address/ }));
    expect((await within(item).findByRole("status")).textContent).toContain(text.copyFailed);
    expect(within(item).getByRole("link", { name: "alice@example.com" })).toBeTruthy();
  });

  it("says 'copied' only for a few seconds, and not after another view is chosen", async () => {
    clipboard(vi.fn(async () => undefined));
    filtering([row(1, "New A", "new"), row(2, "Answered A", "answered")]);
    const item = (await screen.findByText("New A")).closest("li") as HTMLElement;
    fireEvent.click(within(item).getByRole("button", { name: /^Copy address/ }));
    expect(await within(item).findByText(text.addressCopied, { exact: false })).toBeTruthy();
    fireEvent.click(button(/^All/));
    await screen.findByText("Answered A");
    expect(screen.queryByText(text.addressCopied, { exact: false })).toBeNull();
  });

  it("names each button for its ticket, so a screen reader can tell them apart", async () => {
    filtering([row(1, "First", "new"), row(2, "Second", "new")]);
    await screen.findByText("First");
    expect(screen.getAllByRole("button", { name: /^Copy address for / }).map((b) => b.getAttribute("aria-label"))).toEqual([
      "Copy address for First",
      "Copy address for Second",
    ]);
  });

  it("is only on a ticket that has an address", async () => {
    filtering([row(1, "With", "new"), row(2, "Without", "new", null)]);
    await screen.findByText("With");
    const without = screen.getByText("Without").closest("li") as HTMLElement;
    expect(within(without).queryByRole("button", { name: /^Copy address/ })).toBeNull();
    expect(without.textContent).toContain(text.noEmail);
    expect(screen.getAllByRole("button", { name: /^Copy address/ })).toHaveLength(1);
  });
});

describe("the API call", () => {
  const reply = (body: unknown) =>
    vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));

  it("sends the filter as ?status= and returns the counts", async () => {
    const counts = { new: 2, answered: 1, closed: 1, escalated: 0 };
    const fetcher = reply({ tickets: [row(1, "A", "new")], counts });
    const found = await httpAdminApi("https://api.example", fetcher).tickets("new");
    expect(found.counts).toEqual(counts);
    expect((fetcher.mock.calls[0] as unknown as [string])[0]).toBe("https://api.example/admin/support?status=new&page=1");
  });

  it("does not trust counts that are not numbers", async () => {
    const fetcher = reply({ tickets: ROWS(), counts: {} });
    const found = await httpAdminApi("https://api.example", fetcher).tickets("all");
    expect(found.counts).toEqual({ new: 2, answered: 1, closed: 1, escalated: 0 });
  });

  it("filters and counts here when the API is older and sends everything", async () => {
    const all = ROWS();
    const fetcher = reply({ tickets: all });
    const api = httpAdminApi("https://api.example", fetcher);
    const answered = await api.tickets("answered");
    expect(answered.tickets.map((t) => t.subject)).toEqual(["Answered A"]);
    expect(answered.counts).toEqual({ new: 2, answered: 1, closed: 1, escalated: 0 });
    expect((await api.tickets("all")).tickets).toHaveLength(4);
  });
});
