/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The admin Tickets page's thread, reply box and Escalated filter (#1432): who wrote each message
// after the first, why a person has the ticket, one reply per press that keeps the words if it
// fails, no reply box without an address, and the "Needs the team" button and count.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type AdminApi,
  AdminApiError,
  httpAdminApi,
  type SupportTicketRow,
} from "../src/admin/api";
import Tickets from "../src/admin/Tickets";
import { text } from "../src/content/admin";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ticket = (over: Partial<SupportTicketRow> = {}): SupportTicketRow => ({
  id: 7,
  at: 1_800_000_000,
  discordId: "123456789012345678",
  dmbotId: "1P0Z4F6N2K9",
  name: "Alice",
  plan: "try-it",
  subject: "Invoice looks wrong",
  message: "First message words",
  source: "website",
  status: "escalated",
  email: "alice@example.com",
  reason: "unhappy",
  replies: [
    { id: 1, who: "ai", text: "Try the Billing page.", at: 1_800_000_100 },
    { id: 2, who: "customer", text: "That did not help.", at: 1_800_000_200 },
  ],
  ...over,
});

function show(rows: SupportTicketRow[], over: Partial<AdminApi> = {}) {
  const api = {
    tickets: vi.fn(async () => ({
      tickets: structuredClone(rows),
      counts: { new: 0, answered: 0, closed: 0, escalated: rows.length },
    })),
    setTicketStatus: vi.fn(async () => undefined),
    replyToTicket: vi.fn(async () => undefined),
    ...over,
  } as unknown as AdminApi;
  render(<Tickets api={api} csrf="c" onSignedOut={vi.fn()} onOff={vi.fn()} />);
  return api;
}

const box = () => screen.getByLabelText(text.replyLabel("Invoice looks wrong")) as HTMLTextAreaElement;

describe("the thread", () => {
  it("shows who wrote each message after the first, in order, and why a person has it", async () => {
    show([ticket()]);
    await screen.findByText("Invoice looks wrong");
    const thread = screen.getByRole("heading", { name: text.threadHeading }).parentElement!;
    const items = within(thread).getAllByRole("listitem");
    expect(items.map((i) => i.getAttribute("data-who"))).toEqual(["ai", "customer"]);
    expect(items[0]!.textContent).toContain("DMbot's AI helper");
    expect(items[0]!.textContent).toContain("Try the Billing page.");
    expect(items[1]!.textContent).toContain("The customer");
    expect(screen.getByText(text.escalationReasons.unhappy!)).toBeTruthy();
    expect(screen.getByText(text.ticketRowStatuses.escalated!)).toBeTruthy();
  });

  it("shows nothing about a thread or a reason that is not there", async () => {
    const bare = ticket({ status: "new" });
    delete bare.replies;
    delete bare.reason;
    show([bare]);
    await screen.findByText("Invoice looks wrong");
    expect(screen.queryByText(text.threadHeading)).toBeNull();
    expect(screen.queryByText(text.escalationReasonLabel, { exact: false })).toBeNull();
    // A reason code the page does not know says nothing rather than a code.
    cleanup();
    show([ticket({ reason: "something-new" })]);
    await screen.findByText("Invoice looks wrong");
    expect(screen.queryByText(text.escalationReasonLabel, { exact: false })).toBeNull();
  });

  it("the team's reply shows as 'The team'", async () => {
    show([ticket({ replies: [{ id: 3, who: "person", text: "I fixed it.", at: 1_800_000_300 }] })]);
    expect(await screen.findByText("The team")).toBeTruthy();
  });
});

describe("the reply box", () => {
  it("sends the words once, with the token, then clears the box and says so", async () => {
    const api = show([ticket()]);
    await screen.findByText("Invoice looks wrong");
    fireEvent.input(box(), { target: { value: "  Hello, I fixed it.  " } });
    fireEvent.click(screen.getByRole("button", { name: text.replySend }));
    expect(await screen.findByText(text.replySent)).toBeTruthy();
    expect(api.replyToTicket).toHaveBeenCalledTimes(1);
    expect(api.replyToTicket).toHaveBeenCalledWith("c", 7, "Hello, I fixed it.");
    expect(box().value).toBe("");
    await waitFor(() => expect(api.tickets).toHaveBeenCalledTimes(2)); // the list is read again
  });

  it("sends one request for two quick presses", async () => {
    let release: () => void = () => {};
    const replyToTicket = vi.fn(() => new Promise<void>((done) => (release = done)));
    show([ticket()], { replyToTicket });
    await screen.findByText("Invoice looks wrong");
    fireEvent.input(box(), { target: { value: "One" } });
    const send = screen.getByRole("button", { name: text.replySend });
    fireEvent.click(send);
    fireEvent.click(send);
    expect(replyToTicket).toHaveBeenCalledTimes(1);
    release();
    await screen.findByText(text.replySent);
  });

  it("refuses an empty or too long reply before asking, and keeps the words", async () => {
    const api = show([ticket()]);
    await screen.findByText("Invoice looks wrong");
    fireEvent.click(screen.getByRole("button", { name: text.replySend }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.replyEmpty);
    expect(api.replyToTicket).not.toHaveBeenCalled();
    // Past the limit (the box's own limit does not stop a paste in every browser).
    fireEvent.input(box(), { target: { value: "x".repeat(4001) } });
    fireEvent.click(screen.getByRole("button", { name: text.replySend }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.replyTooLong);
    expect(api.replyToTicket).not.toHaveBeenCalled();
  });

  it.each([
    ["bad-reply", text.replyTooLong],
    ["reply-off", text.replyOff],
    ["no-ticket", text.ticketGone],
    ["down", text.replyFailed],
    ["stale", text.replyFailed],
  ] as const)("says a %s refusal in plain words and keeps what was typed", async (kind, words) => {
    show([ticket()], {
      replyToTicket: vi.fn(async () => {
        throw new AdminApiError(kind);
      }),
    });
    await screen.findByText("Invoice looks wrong");
    fireEvent.input(box(), { target: { value: "My words" } });
    fireEvent.click(screen.getByRole("button", { name: text.replySend }));
    expect((await screen.findByRole("alert")).textContent).toBe(words);
    expect(box().value).toBe("My words");
  });

  it("goes back to the sign-in screen when the session ended", async () => {
    const onSignedOut = vi.fn();
    const api = {
      tickets: async () => ({
        tickets: [ticket()],
        counts: { new: 0, answered: 0, closed: 0, escalated: 1 },
      }),
      setTicketStatus: vi.fn(),
      replyToTicket: vi.fn(async () => {
        throw new AdminApiError("signed-out");
      }),
    } as unknown as AdminApi;
    render(<Tickets api={api} csrf="c" onSignedOut={onSignedOut} onOff={vi.fn()} />);
    await screen.findByText("Invoice looks wrong");
    fireEvent.input(box(), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: text.replySend }));
    await waitFor(() => expect(onSignedOut).toHaveBeenCalled());
  });

  it("has no box without an address, and none on a closed ticket", async () => {
    show([ticket({ email: null, id: 1 }), ticket({ id: 2, status: "closed", subject: "Done one" })]);
    await screen.findByText("Done one");
    expect(screen.getByText(text.replyNoEmail)).toBeTruthy();
    expect(screen.queryAllByRole("button", { name: text.replySend })).toHaveLength(0);
  });
});

describe("the filter and the API call", () => {
  it("has a 'Needs the team' button with its count, and All counts it too", async () => {
    const api = {
      tickets: vi.fn(async () => ({
        tickets: [],
        counts: { new: 2, answered: 1, closed: 1, escalated: 3 },
      })),
      setTicketStatus: vi.fn(),
      replyToTicket: vi.fn(),
    } as unknown as AdminApi;
    render(<Tickets api={api} csrf="c" onSignedOut={vi.fn()} onOff={vi.fn()} />);
    expect(await screen.findByRole("button", { name: /^Needs the team/ })).toBeTruthy();
    const group = screen.getByRole("group", { name: text.ticketsFilterLabel });
    expect([...group.querySelectorAll("button")].map((b) => b.textContent)).toEqual([
      "Needs the team (3)",
      "Needs a reply (2)",
      "Answered (1)",
      "Closed (1)",
      "All (7)",
    ]);
    fireEvent.click(screen.getByRole("button", { name: /^Needs the team/ }));
    await waitFor(() => expect(api.tickets).toHaveBeenCalledWith("escalated", 1));
    expect(await screen.findByText(text.ticketsNoneFor.escalated!)).toBeTruthy();
  });

  it("posts the reply to the admin route with the token, and words the refusals", async () => {
    const calls: [string, RequestInit][] = [];
    const answer = (status: number, error?: string) =>
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push([url, init]);
        return new Response(error ? JSON.stringify({ error }) : null, { status });
      });
    const ok = httpAdminApi("https://api.example", answer(204) as unknown as typeof fetch);
    await ok.replyToTicket("tok", 7, "Hi");
    const [url, init] = calls[0]!;
    expect(url).toBe("https://api.example/admin/support/7/reply");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-Admin-CSRF"]).toBe("tok");
    expect(JSON.parse(String(init.body))).toEqual({ text: "Hi" });
    for (const [status, error, kind] of [
      [400, "bad_text", "bad-reply"],
      [503, "reply_off", "reply-off"],
      [404, "no_ticket", "no-ticket"],
      [401, undefined, "signed-out"],
    ] as const) {
      const api = httpAdminApi("https://api.example", answer(status, error) as unknown as typeof fetch);
      await expect(api.replyToTicket("tok", 7, "Hi")).rejects.toMatchObject({ kind });
    }
  });

  it("counts an API without the escalated number as none", async () => {
    const fetcher = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ tickets: [], counts: { new: 1, answered: 0, closed: 0 } }),
          { status: 200 },
        ),
    );
    const api = httpAdminApi("https://api.example", fetcher as unknown as typeof fetch);
    expect((await api.tickets("all")).counts).toEqual({
      new: 1,
      answered: 0,
      closed: 0,
      escalated: 0,
    });
  });
});
