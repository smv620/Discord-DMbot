/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Support tickets on the website (#1340): the Account page's "Write to the DMbot team" form for
// everyone with an account (Try It too), the public Q&A for everyone else, the API calls, and the admin page's list.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Account from "../src/account/Account";
import {
  type AccountApi,
  ApiError,
  httpApi,
  type SupportReceipt,
  type SupportTicket,
} from "../src/account/api";
import { mockApi, type Scenario } from "../src/account/mock";
import { MAX_SUBJECT, MAX_TICKET, Support } from "../src/account/Support";
import { QA_URL, text } from "../src/content/account";
import { text as adminText } from "../src/content/admin";
import {
  type AdminApi,
  AdminApiError,
  httpAdminApi,
  type SupportTicketRow,
  type TicketCounts,
  type TicketStatus,
} from "../src/admin/api";
import Tickets from "../src/admin/Tickets";

// happy-dom can't run Cloudflare's script, so the person check is a stand-in with a button that
// gives a token (as a solved check does) and one that fails (as a blocked one does).
vi.mock("../src/hello/Turnstile", async () => {
  const { h } = await import("preact");
  return {
    default: ({ onToken, onFail }: { onToken: (t: string) => void; onFail: () => void }) =>
      h("div", null, [
        h("button", { type: "button", onClick: () => onToken("tok") }, "I am a person"),
        h("button", { type: "button", onClick: () => onFail() }, "Check blocked"),
      ]),
  };
});

afterEach(cleanup);

function show(scenario: Scenario) {
  const api = mockApi(scenario);
  render(<Account api={api} go={vi.fn()} search="" />);
  return api;
}

function type(label: string, value: string) {
  fireEvent.input(screen.getByLabelText(label), { target: { value } });
}

describe("who sees the form", () => {
  it("a paid account sees the form, with the privacy words before Send", async () => {
    show("table");
    expect(await screen.findByRole("heading", { name: text.supportHeading })).toBeTruthy();
    expect(screen.getByLabelText(text.supportSubjectLabel)).toBeTruthy();
    expect(screen.getByLabelText(text.supportMessageLabel)).toBeTruthy();
    const note = screen.getByText(text.supportPrivacy, { exact: false });
    // The three things the issue asks the page to say before Send.
    expect(note.textContent).toContain("privately to the DMbot team");
    expect(note.textContent).toContain("12 months, then delete it");
    expect(note.textContent).toContain("not posted anywhere public");
    expect(within(note).getByRole("link", { name: text.qaLink }).getAttribute("href")).toBe(
      QA_URL,
    );
    expect(screen.queryByRole("heading", { name: text.qaHeading })).toBeNull();
  });

  it("a Try It account sees the form too, with the line about buying a plan (#1435)", async () => {
    show("try-it");
    expect(await screen.findByLabelText(text.supportMessageLabel)).toBeTruthy();
    expect(screen.getByText(text.supportIntroTryIt)).toBeTruthy();
    expect(screen.queryByRole("heading", { name: text.qaHeading })).toBeNull();
  });

  it("a paid account does not get the Try It line", async () => {
    show("table");
    await screen.findByLabelText(text.supportMessageLabel);
    expect(screen.queryByText(text.supportIntroTryIt)).toBeNull();
  });

  it.each(["no-plan"] as const)(
    "%s sees the public Q&A, not the form",
    async (scenario) => {
      show(scenario);
      expect(await screen.findByRole("heading", { name: text.qaHeading })).toBeTruthy();
      expect(screen.getByRole("link", { name: text.qaLink }).getAttribute("href")).toBe(QA_URL);
      expect(screen.getByRole("link", { name: text.helloLink }).getAttribute("href")).toBe(
        "/hello",
      );
      expect(screen.queryByLabelText(text.supportMessageLabel)).toBeNull();
    },
  );

  it("an API that doesn't have support yet shows the public Q&A to everyone", async () => {
    const api = mockApi("table");
    delete api.sendSupport;
    render(<Account api={api} go={vi.fn()} search="" />);
    expect(await screen.findByRole("heading", { name: text.qaHeading })).toBeTruthy();
    expect(screen.queryByLabelText(text.supportMessageLabel)).toBeNull();
  });
});

describe("writing to the team", () => {
  function form(over: Partial<AccountApi> = {}) {
    const sendSupport = vi.fn<(t: SupportTicket) => Promise<SupportReceipt>>(async () => ({ reply: true }));
    const api = { ...mockApi("table"), sendSupport, ...over } as AccountApi;
    render(<Support api={api} siteKey="" />);
    return { sendSupport };
  }
  const send = () => fireEvent.click(screen.getByRole("button", { name: text.supportSend }));

  it("sends the trimmed subject and message, then thanks and offers another", async () => {
    const { sendSupport } = form();
    type(text.supportSubjectLabel, "  My invoice  ");
    type(text.supportMessageLabel, "\n The plan is wrong. ");
    send();
    expect((await screen.findByText(text.supportSent)).getAttribute("role")).toBe("status");
    expect(sendSupport).toHaveBeenCalledWith({
      subject: "My invoice",
      message: "The plan is wrong.",
      turnstile: "",
    });
    // The words are gone from the page once sent: nothing shows them back.
    expect(screen.queryByDisplayValue("The plan is wrong.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: text.supportAnother }));
    expect((screen.getByLabelText(text.supportMessageLabel) as HTMLTextAreaElement).value).toBe(
      "",
    );
  });

  it("the thank-you says the team replies by email when the account has an address", async () => {
    form();
    type(text.supportSubjectLabel, "Subject");
    type(text.supportMessageLabel, "Words");
    send();
    const thanks = await screen.findByText(text.supportSent);
    expect(thanks.textContent).toContain("We'll reply by email to the address on your account.");
  });

  it("the thank-you says what to do when the account has no email", async () => {
    form({ sendSupport: async () => ({ reply: false }) });
    type(text.supportSubjectLabel, "Subject");
    type(text.supportMessageLabel, "Words");
    send();
    const thanks = await screen.findByText(text.supportSentNoEmail);
    // It doesn't promise an email, and it names a way that exists: Discord sign-in is where
    // the address comes from (nothing on this page adds one).
    expect(thanks.textContent).toContain("we can't reply");
    expect(thanks.textContent).toContain("Discord settings");
    expect(thanks.textContent).not.toContain("We'll reply by email");
    expect(screen.queryByText(text.supportSent)).toBeNull();
  });

  it("an empty subject or message is said at once and nothing is sent", async () => {
    const { sendSupport } = form();
    type(text.supportMessageLabel, "Hello");
    send();
    expect((await screen.findByRole("alert")).textContent).toBe(
      text.supportErrors["ticket-empty"],
    );
    expect(sendSupport).not.toHaveBeenCalled();
    // The words stay.
    expect((screen.getByLabelText(text.supportMessageLabel) as HTMLTextAreaElement).value).toBe(
      "Hello",
    );
  });

  it("counts letters as the API does: 120 and 4,000 pass, one more does not", async () => {
    const { sendSupport } = form();
    type(text.supportSubjectLabel, "😀".repeat(MAX_SUBJECT)); // an emoji is one letter
    type(text.supportMessageLabel, "m".repeat(MAX_TICKET));
    send();
    await waitFor(() => expect(sendSupport).toHaveBeenCalledTimes(1));
    fireEvent.click(await screen.findByRole("button", { name: text.supportAnother }));
    type(text.supportSubjectLabel, "s".repeat(MAX_SUBJECT + 1));
    type(text.supportMessageLabel, "Hello");
    send();
    expect((await screen.findByRole("alert")).textContent).toBe(
      text.supportErrors["subject-too-long"],
    );
    type(text.supportSubjectLabel, "Subject");
    type(text.supportMessageLabel, "m".repeat(MAX_TICKET + 1));
    send();
    expect((await screen.findByRole("alert")).textContent).toBe(
      text.supportErrors["ticket-too-long"],
    );
    expect(sendSupport).toHaveBeenCalledTimes(1);
  });

  it.each([
    "slow-down",
    "not-paying",
    "one-waiting",
    "daily-limit",
    "signed-out",
    "network",
    "server",
    "ticket-too-long",
  ] as const)("a refusal for %s is said in plain words and keeps the words", async (kind) => {
    form({
      sendSupport: async () => {
        throw new ApiError(kind);
      },
    });
    type(text.supportSubjectLabel, "Subject");
    type(text.supportMessageLabel, "My words");
    send();
    expect((await screen.findByRole("alert")).textContent).toBe(text.supportErrors[kind]);
    expect((screen.getByLabelText(text.supportMessageLabel) as HTMLTextAreaElement).value).toBe(
      "My words",
    );
    expect((screen.getByRole("button", { name: text.supportSend }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("every refusal the API can give has words, and none is a technical term", () => {
    for (const [kind, words] of Object.entries(text.supportErrors)) {
      expect(words.length, kind).toBeGreaterThan(10);
      expect(words, kind).not.toMatch(/api|database|error|status|token|http/i);
      // Each says what to do next, or that the words are still there.
      expect(words, kind).toMatch(/still here|try again|instead|Sign in|Wait|Please|copy it/i);
    }
  });

  it("without a person check on the site, a not-human answer isn't told to tick a box", async () => {
    form({
      sendSupport: async () => {
        throw new ApiError("not-human");
      },
    });
    type(text.supportSubjectLabel, "Subject");
    type(text.supportMessageLabel, "Words");
    send();
    expect((await screen.findByRole("alert")).textContent).toBe(text.supportErrors["server"]);
  });

  it("two sends in the same moment send one ticket", async () => {
    let release: () => void = () => undefined;
    const sendSupport = vi.fn<(t: SupportTicket) => Promise<SupportReceipt>>(
      () => new Promise<SupportReceipt>((done) => (release = () => done({ reply: true }))),
    );
    render(<Support api={{ ...mockApi("table"), sendSupport } as AccountApi} siteKey="" />);
    type(text.supportSubjectLabel, "Subject");
    type(text.supportMessageLabel, "Words");
    const formElement = screen.getByLabelText(text.supportSubjectLabel).closest("form") as HTMLFormElement;
    fireEvent.submit(formElement);
    fireEvent.submit(formElement);
    release();
    await screen.findByText(text.supportSent);
    expect(sendSupport).toHaveBeenCalledTimes(1);
  });

  it("a refusal takes focus, and so does the thank-you and the next form", async () => {
    form({
      sendSupport: vi.fn(async () => {
        throw new ApiError("slow-down");
      }),
    });
    type(text.supportSubjectLabel, "Subject");
    type(text.supportMessageLabel, "Words");
    send();
    const alert = await screen.findByRole("alert");
    await waitFor(() => expect(document.activeElement).toBe(alert));
  });

  it("the thank-you takes focus and Write another puts focus in the subject box", async () => {
    form();
    type(text.supportSubjectLabel, "Subject");
    type(text.supportMessageLabel, "Words");
    send();
    const thanks = await screen.findByText(text.supportSent);
    await waitFor(() => expect(document.activeElement).toBe(thanks));
    fireEvent.click(screen.getByRole("button", { name: text.supportAnother }));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText(text.supportSubjectLabel)),
    );
  });
});

describe("the person check", () => {
  const sendSupport = vi.fn<(t: SupportTicket) => Promise<SupportReceipt>>(async () => ({ reply: true }));
  const start = () => {
    sendSupport.mockClear();
    render(<Support api={{ ...mockApi("table"), sendSupport } as AccountApi} siteKey="key" />);
  };
  const write = (message = "Words") => {
    // Starting to write is what starts the check (focus, not just typing).
    fireEvent.focusIn(screen.getByLabelText(text.supportSubjectLabel));
    type(text.supportSubjectLabel, "Subject");
    type(text.supportMessageLabel, message);
  };
  const press = () => fireEvent.click(screen.getByRole("button", { name: text.supportSend }));

  it("shows up only once someone starts writing", () => {
    start();
    expect(screen.queryByRole("button", { name: "I am a person" })).toBeNull();
    fireEvent.focusIn(screen.getByLabelText(text.supportSubjectLabel));
    expect(screen.getByRole("button", { name: "I am a person" })).toBeTruthy();
  });

  it("without a tick nothing is sent and it says to tick the box", async () => {
    start();
    write();
    press();
    expect((await screen.findByRole("alert")).textContent).toBe(text.supportErrors["not-human"]);
    expect(sendSupport).not.toHaveBeenCalled();
  });

  it("sends with the token, and the next ticket needs a new tick (a token works once)", async () => {
    start();
    write();
    fireEvent.click(await screen.findByRole("button", { name: "I am a person" }));
    press();
    await screen.findByText(text.supportSent);
    expect(sendSupport).toHaveBeenCalledWith({ subject: "Subject", message: "Words", turnstile: "tok" });
    fireEvent.click(screen.getByRole("button", { name: text.supportAnother }));
    write("More words");
    press(); // the spent token must not go again
    expect((await screen.findByRole("alert")).textContent).toBe(text.supportErrors["not-human"]);
    expect(sendSupport).toHaveBeenCalledTimes(1);
  });

  it("a blocked check says so, keeps the words and can be tried again", async () => {
    start();
    write("Keep these");
    fireEvent.click(await screen.findByRole("button", { name: "Check blocked" }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.supportCheckFailed);
    expect((screen.getByLabelText(text.supportMessageLabel) as HTMLTextAreaElement).value).toBe(
      "Keep these",
    );
    fireEvent.click(screen.getByRole("button", { name: text.supportCheckAgain }));
    expect(await screen.findByRole("button", { name: "I am a person" })).toBeTruthy();
  });
});

describe("the API calls", () => {
  const reply = (status: number, body: unknown = {}) =>
    vi.fn(async () => new Response(JSON.stringify(body), { status }));

  it("reads whether the team can reply: only an explicit no changes the words", async () => {
    const ticket = { subject: "S", message: "M", turnstile: "" };
    const ask = (body: unknown) =>
      httpApi("https://api.example", reply(201, body)).sendSupport?.(ticket);
    expect(await ask({ ok: true, reply: true })).toEqual({ reply: true });
    expect(await ask({ ok: true, reply: false })).toEqual({ reply: false });
    expect(await ask({ ok: true })).toEqual({ reply: true });
  });

  it("POSTs the ticket as JSON with the cookie and the site's header", async () => {
    const fetcher = reply(201, { ok: true });
    await httpApi("https://api.example", fetcher).sendSupport?.({
      subject: "S",
      message: "M",
      turnstile: "tok",
    });
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.example/support");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect((init.headers as Record<string, string>)["X-DMbot-Request"]).toBe("1");
    expect(JSON.parse(String(init.body))).toEqual({ subject: "S", message: "M", turnstile: "tok" });
  });

  it.each([
    [403, "not_paying", "not-paying"],
    [429, "one_waiting", "one-waiting"],
    [429, "daily", "daily-limit"],
    [429, "slow_down", "slow-down"],
    [503, "busy", "slow-down"],
    [400, "not_human", "not-human"],
    [400, "empty", "ticket-empty"],
    [400, "too_long", "ticket-too-long"],
    [413, "too_long", "ticket-too-long"],
    [400, "subject_too_long", "subject-too-long"],
    [502, "support_unavailable", "server"],
  ])("%i %s becomes %s", async (status, code, kind) => {
    const api = httpApi("https://api.example", reply(status, { error: code }));
    await expect(api.sendSupport?.({ subject: "S", message: "M", turnstile: "" })).rejects.toEqual(
      new ApiError(kind as never),
    );
  });

  it("a signed-out answer is signed-out", async () => {
    const api = httpApi("https://api.example", reply(401, { error: "signed_out" }));
    await expect(api.sendSupport?.({ subject: "S", message: "M", turnstile: "" })).rejects.toEqual(
      new ApiError("signed-out"),
    );
  });
});

const ROW: SupportTicketRow = {
  id: 7,
  at: 1_800_000_000,
  discordId: "123456789012345678",
  dmbotId: "1P0Z4F6N2K9",
  name: "Alice",
  plan: "table",
  subject: "Invoice looks wrong",
  message: "Line one\nLine two",
  source: "website",
  status: "new",
  email: "alice@example.com",
};

const countsOf = (rows: SupportTicketRow[]): TicketCounts => ({
  new: rows.filter((r) => r.status === "new").length,
  answered: rows.filter((r) => r.status === "answered").length,
  closed: rows.filter((r) => r.status === "closed").length,
  escalated: rows.filter((r) => r.status === "escalated").length,
});

/** The rows as the API answers: every row whatever the filter (the tests of a row's buttons need
 * all statuses on screen); `filteringApi` below honours the filter like the real one. */
function adminApi(rows: SupportTicketRow[], over: Partial<AdminApi> = {}): AdminApi {
  return {
    tickets: vi.fn(async () => ({ tickets: structuredClone(rows), counts: countsOf(rows) })),
    setTicketStatus: vi.fn(async () => undefined),
    ...over,
  } as unknown as AdminApi;
}

function panel(api: AdminApi, hooks: { out?: () => void; off?: () => void } = {}) {
  render(
    <Tickets
      api={api}
      csrf="csrf-1"
      onSignedOut={hooks.out ?? vi.fn()}
      onOff={hooks.off ?? vi.fn()}
    />,
  );
}

describe("the admin page's tickets", () => {
  it("lists each ticket with who, plan, time, status and the words as typed", async () => {
    panel(adminApi([ROW, { ...ROW, id: 6, subject: "Older", status: "closed", name: null }]));
    const item = (await screen.findByText(ROW.subject)).closest("li") as HTMLElement;
    expect(item.textContent).toContain("Alice");
    expect(item.textContent).toContain("DMbot ID 1P0Z4F6N2K9");
    expect(item.textContent).toContain("Table plan");
    expect(item.textContent).toContain("2027");
    expect(item.textContent).toContain(adminText.ticketRowStatuses["new"] ?? "");
    // The line break is kept (the CSS class keeps it on screen).
    expect(within(item).getByText(/Line one/).textContent).toBe("Line one\nLine two");
    // A person who hasn't signed in lately shows as a shortened number, never as nothing.
    expect(screen.getByText("Older").closest("li")?.textContent).toContain("1234…5678");
  });

  it("shows the account's email to reply to, as a link, or says there is none", async () => {
    panel(adminApi([ROW, { ...ROW, id: 6, subject: "No address", email: null }]));
    const withEmail = (await screen.findByText(ROW.subject)).closest("li") as HTMLElement;
    const link = within(withEmail).getByRole("link", { name: "alice@example.com" });
    expect(link.getAttribute("href")).toBe("mailto:alice%40example.com");
    expect(withEmail.textContent).toContain(`${adminText.replyTo}: alice@example.com`);
    const without = screen.getByText("No address").closest("li") as HTMLElement;
    expect(without.textContent).toContain(adminText.noEmail);
    expect(within(without).queryByRole("link")).toBeNull();
  });

  it("an address with odd characters can't change the mail link's target", async () => {
    panel(adminApi([{ ...ROW, email: "a?cc=b@example.com" }]));
    const link = await screen.findByRole("link", { name: "a?cc=b@example.com" });
    expect(link.getAttribute("href")).toBe("mailto:a%3Fcc%3Db%40example.com");
  });

  it("says when there are none", async () => {
    panel(adminApi([]));
    // The default view is what needs a reply, so that is what it says nothing is waiting for.
    expect(await screen.findByText(adminText.ticketsNoneFor["new"] ?? "")).toBeTruthy();
  });

  it.each<[TicketStatus, string, TicketStatus]>([
    ["new", adminText.markAnswered, "answered"],
    ["new", adminText.closeTicket, "closed"],
    ["answered", adminText.closeTicket, "closed"],
    ["closed", adminText.reopenTicket, "new"],
  ])("a %s ticket: %s sets it to %s, with the CSRF token", async (from, button, to) => {
    const api = adminApi([{ ...ROW, status: from }]);
    panel(api);
    fireEvent.click(await screen.findByRole("button", { name: button }));
    await waitFor(() => expect(api.setTicketStatus).toHaveBeenCalledWith("csrf-1", 7, to));
    expect((await screen.findByRole("status")).textContent).toBe(
      adminText.ticketDone(adminText.ticketStatuses[to] ?? to),
    );
    expect(api.tickets).toHaveBeenCalledTimes(2); // the list is read again after a change
  });

  it("offers Mark answered only on a new ticket and Reopen only on a closed one", async () => {
    panel(adminApi([ROW, { ...ROW, id: 6, status: "answered" }, { ...ROW, id: 5, status: "closed" }]));
    await screen.findAllByText(ROW.subject);
    expect(screen.getAllByRole("button", { name: adminText.markAnswered })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: adminText.closeTicket })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: adminText.reopenTicket })).toHaveLength(1);
  });

  it("a ticket that is gone says so and the list is read again", async () => {
    const api = adminApi([ROW], {
      setTicketStatus: async () => {
        throw new AdminApiError("no-ticket");
      },
    });
    panel(api);
    fireEvent.click(await screen.findByRole("button", { name: adminText.closeTicket }));
    expect((await screen.findByRole("alert")).textContent).toBe(adminText.ticketGone);
    await waitFor(() => expect(api.tickets).toHaveBeenCalledTimes(2));
  });

  it("an ended admin session goes back to sign-in, and the page being off shows that", async () => {
    const out = vi.fn();
    panel(
      adminApi([ROW], {
        setTicketStatus: async () => {
          throw new AdminApiError("signed-out");
        },
      }),
      { out },
    );
    fireEvent.click(await screen.findByRole("button", { name: adminText.closeTicket }));
    await waitFor(() => expect(out).toHaveBeenCalled());
    cleanup();
    const off = vi.fn();
    panel(
      adminApi([], {
        tickets: async () => {
          throw new AdminApiError("off");
        },
      }),
      { off },
    );
    await waitFor(() => expect(off).toHaveBeenCalled());
  });

  it("a list that can't be read again after a change stays on screen, with a notice", async () => {
    let calls = 0;
    const api = adminApi([ROW], {
      tickets: vi.fn(async () => {
        calls += 1;
        if (calls > 1) throw new AdminApiError("down");
        return { tickets: [ROW], counts: countsOf([ROW]) };
      }),
    });
    panel(api);
    fireEvent.click(await screen.findByRole("button", { name: adminText.closeTicket }));
    expect((await screen.findByRole("alert")).textContent).toBe(adminText.ticketsDown);
    expect(screen.getByText(ROW.subject)).toBeTruthy();
  });

  it("a change that fails says it didn't save, not that the list can't load", async () => {
    panel(
      adminApi([ROW], {
        setTicketStatus: async () => {
          throw new AdminApiError("down");
        },
      }),
    );
    fireEvent.click(await screen.findByRole("button", { name: adminText.closeTicket }));
    expect((await screen.findByRole("alert")).textContent).toBe(adminText.ticketFailed);
  });

  it("when the list can't load it says so with a Try again button", async () => {
    let fail = true;
    const api = adminApi([ROW], {
      tickets: vi.fn(async () => {
        if (fail) throw new AdminApiError("down");
        return { tickets: [ROW], counts: countsOf([ROW]) };
      }),
    });
    panel(api);
    expect((await screen.findByRole("alert")).textContent).toBe(adminText.ticketsDown);
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: adminText.ticketsTryAgain }));
    expect(await screen.findByText(ROW.subject)).toBeTruthy();
  });
});

describe("the admin API calls", () => {
  const reply = (status: number, body: unknown = {}) =>
    vi.fn(async () => new Response(JSON.stringify(body), { status }));

  it("reads the list and changes a status with the CSRF token", async () => {
    const counts = { new: 1, answered: 0, closed: 0, escalated: 0 };
    const list = reply(200, { tickets: [ROW], counts });
    expect(await httpAdminApi("https://api.example", list).tickets()).toEqual({
      tickets: [ROW],
      counts,
    });
    expect((list.mock.calls[0] as unknown as [string])[0]).toBe(
      "https://api.example/admin/support?status=all&page=1",
    );
    const change = reply(204);
    await httpAdminApi("https://api.example", change).setTicketStatus("csrf-9", 7, "closed");
    const [url, init] = change.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.example/admin/support/7/status");
    expect((init.headers as Record<string, string>)["X-Admin-CSRF"]).toBe("csrf-9");
    expect(JSON.parse(String(init.body))).toEqual({ status: "closed" });
  });

  it.each([
    [401, "signed_out", "signed-out"],
    [404, "no_ticket", "no-ticket"],
    [400, "bad_status", "stale"],
    [403, "not_allowed", "stale"],
    [404, "not_found", "off"],
  ])("%i %s becomes %s", async (status, code, kind) => {
    const api = httpAdminApi("https://api.example", reply(status, { error: code }));
    await expect(api.setTicketStatus("c", 1, "closed")).rejects.toEqual(
      new AdminApiError(kind as never),
    );
  });
});
