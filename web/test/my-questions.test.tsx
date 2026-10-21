/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Your questions" on the Account page (#1373): the person's own questions to the team, with the
// date sent and where each stands, under the Support form. Subject and date only, and nothing
// at all when there are none.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Account from "../src/account/Account";
import { type AccountApi, ApiError, httpApi, type MySupport } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import { MyQuestions } from "../src/account/MyQuestions";
import { text } from "../src/content/account";

// The person check is not under test here (see support.test.tsx): a stand-in that gives a token.
vi.mock("../src/hello/Turnstile", async () => {
  const { h } = await import("preact");
  return {
    default: ({ onToken }: { onToken: (t: string) => void }) =>
      h("button", { type: "button", onClick: () => onToken("tok") }, "I am a person"),
  };
});

afterEach(cleanup);

const apiWith = (list: MySupport | (() => Promise<MySupport>)): AccountApi =>
  ({ ...mockApi("table"), mySupport: typeof list === "function" ? list : async () => list }) as AccountApi;

const DAY = 1_800_000_000; // 15 January 2027, 08:00 UTC

describe("the list", () => {
  it("tells a person who can write that a closed question can be followed by another", async () => {
    const closed: MySupport = { reply: true, tickets: [{ at: DAY, subject: "Old", status: "closed" }] };
    render(<MyQuestions api={apiWith(closed)} canWrite />);
    expect(await screen.findByText(/Closed — you can write again/)).toBeTruthy();
    cleanup();
    render(<MyQuestions api={apiWith(closed)} />);
    expect(await screen.findByText(/· Closed/)).toBeTruthy();
  });

  it("shows the subject, the date sent and the three status words", async () => {
    render(
      <MyQuestions
        api={apiWith({
          reply: true,
          tickets: [
            { at: DAY, subject: "Invoice", status: "new" },
            { at: DAY - 86400, subject: "Server", status: "answered" },
            { at: DAY - 2 * 86400, subject: "Old", status: "closed" },
          ],
        })}
      />,
    );
    expect(await screen.findByRole("heading", { name: text.questionsHeading })).toBeTruthy();
    const rows = screen.getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      "InvoiceSent 15 January 2027 · WaitingRead the conversation",
      "ServerSent 14 January 2027 · Answered — check your emailRead the conversation",
      "OldSent 13 January 2027 · ClosedRead the conversation",
    ]);
    expect(screen.getByText(text.questionsLead)).toBeTruthy();
    expect(within(rows[1]!).getByText(/Answered/).textContent).toContain("check your email");
  });

  it("says only 'Answered' when the account has no email to check", async () => {
    render(
      <MyQuestions
        api={apiWith({ reply: false, tickets: [{ at: DAY, subject: "Hi", status: "answered" }] })}
      />,
    );
    expect(await screen.findByText(/Sent 15 January 2027 · Answered/)).toBeTruthy();
    expect(screen.queryByText(/check your email/)).toBeNull();
    // And the lead says why, and what to do, so "Answered" is not a dead end.
    expect(screen.getByText(text.questionsLeadNoEmail)).toBeTruthy();
    expect(text.questionsLeadNoEmail).toContain("Discord settings");
  });

  it("shows nothing at all with no questions: no heading and no empty box", async () => {
    const mySupport = vi.fn(async () => ({ reply: true, tickets: [] }));
    const { container } = render(<MyQuestions api={apiWith(mySupport)} />);
    await waitFor(() => expect(mySupport).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });

  it("shows nothing when the list can't be read, or the API has no list yet", async () => {
    const refused = vi.fn(async (): Promise<MySupport> => Promise.reject(new ApiError("network")));
    const failing = render(<MyQuestions api={apiWith(refused)} />);
    await waitFor(() => expect(refused).toHaveBeenCalled());
    // Let the rejection reach the component's own handler before looking.
    await new Promise((done) => setTimeout(done, 0));
    expect(failing.container.innerHTML).toBe("");
    cleanup();
    // An API older than #1373 has no list route at all.
    const { mySupport: _none, ...older } = mockApi("table");
    const old = render(<MyQuestions api={older as AccountApi} />);
    expect(old.container.innerHTML).toBe("");
  });

  it("shows the words as text, never as markup", async () => {
    render(
      <MyQuestions
        api={apiWith({ reply: true, tickets: [{ at: DAY, subject: "<b>bold</b> & more", status: "new" }] })}
      />,
    );
    const row = await screen.findByRole("listitem");
    expect(row.querySelector("b")).toBeNull();
    expect(row.textContent).toContain("<b>bold</b> & more");
  });
});

describe("on the Account page", () => {
  it("lists a question right after it is sent, under the form", async () => {
    const api = mockApi("table");
    render(<Account api={api} go={vi.fn()} search="" />);
    expect(await screen.findByRole("heading", { name: text.supportHeading })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: text.questionsHeading })).toBeNull();
    fireEvent.input(screen.getByLabelText(text.supportSubjectLabel), { target: { value: "My invoice" } });
    fireEvent.input(screen.getByLabelText(text.supportMessageLabel), { target: { value: "Hello" } });
    const check = screen.queryByRole("button", { name: "I am a person" });
    if (check) fireEvent.click(check);
    fireEvent.click(screen.getByRole("button", { name: text.supportSend }));
    const list = await screen.findByRole("heading", { name: text.questionsHeading });
    expect(list).toBeTruthy();
    const row = within(list.closest("section")!).getByRole("listitem");
    expect(row.textContent).toContain("My invoice");
    expect(row.textContent).toContain("Waiting");
  });
});

describe("the API call", () => {
  it("reads GET /support/mine and keeps only well-formed questions", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            tickets: [
              { at: 5, subject: "ok", status: "new" },
              { at: "x", subject: "bad time", status: "new" },
              { at: 6, subject: "bad status", status: "weird" },
              { at: 7, subject: "with the team", status: "escalated" },
              null,
            ],
            reply: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    );
    const api = httpApi("https://api.dmbot.example", fetchMock as unknown as typeof fetch);
    const found = await api.mySupport!();
    // A question that is with the team is kept (it was once dropped from the list).
    expect(found).toEqual({
      tickets: [
        { at: 5, subject: "ok", status: "new" },
        { at: 7, subject: "with the team", status: "escalated" },
      ],
      reply: false,
    });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.dmbot.example/support/mine");
    expect(init.method).toBe("GET");
  });

  it("treats an answer that is not a list as no questions", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ tickets: "nope" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    const api = httpApi("https://api.dmbot.example", fetchMock as unknown as typeof fetch);
    expect(await api.mySupport!()).toEqual({ tickets: [], reply: true });
  });
});

describe("who answered, and the latest answer (#1432)", () => {
  const list = (tickets: MySupport["tickets"], reply = true): MySupport => ({ reply, tickets });

  it("names the helper or the team, and says when a person has it", async () => {
    render(
      <MyQuestions
        api={apiWith(
          list([
            { at: DAY, subject: "A", status: "answered", answeredBy: "helper", latest: "Open the Billing page." },
            { at: DAY - 1, subject: "B", status: "answered", answeredBy: "team", latest: "I fixed it." },
            { at: DAY - 2, subject: "C", status: "escalated", answeredBy: "helper", latest: "Not sure." },
            { at: DAY - 3, subject: "D", status: "new", answeredBy: null, latest: null },
          ]),
        )}
      />,
    );
    await screen.findByRole("heading", { name: text.questionsHeading });
    const rows = screen.getAllByRole("listitem").map((r) => r.textContent);
    expect(rows[0]).toContain("Answered by DMbot's AI helper — check your email");
    expect(rows[0]).toContain(text.questionLatest("Open the Billing page."));
    expect(rows[1]).toContain("Answered by the team — check your email");
    expect(rows[2]).toContain("· With the team");
    expect(rows[2]).toContain(text.questionLatest("Not sure."));
    expect(rows[3]).toContain("· Waiting");
    expect(rows[3]).not.toContain("Last reply");
  });

  it("does not say to check email when the account has none", async () => {
    render(
      <MyQuestions
        api={apiWith(
          list([{ at: DAY, subject: "A", status: "answered", answeredBy: "team", latest: "Done." }], false),
        )}
      />,
    );
    expect(await screen.findByText(/· Answered by the team$/)).toBeTruthy();
    expect(screen.queryByText(/check your email/)).toBeNull();
  });

  it("is unchanged from an API without the new fields", async () => {
    render(<MyQuestions api={apiWith(list([{ at: DAY, subject: "Old", status: "answered" }]))} />);
    expect(await screen.findByText(/· Answered — check your email$/)).toBeTruthy();
    expect(screen.queryByText(/Last reply/)).toBeNull();
  });
});

describe("the whole conversation (#2193)", () => {
  const question: MySupport = {
    reply: true,
    tickets: [{ at: DAY, subject: "My invoice", status: "answered", answeredBy: "team" }],
  };
  const parts = [
    { who: "you" as const, text: "Why was I charged\ntwice?", at: DAY },
    { who: "helper" as const, text: "Check the Billing page.", at: DAY + 60 },
    { who: "you" as const, text: "<b>That</b> did not help.", at: DAY + 86400 },
    { who: "team" as const, text: "Fixed, sorry.", at: DAY + 2 * 86400 },
  ];
  const withThread = (read: AccountApi["supportThread"]): AccountApi =>
    ({ ...apiWith(question), supportThread: read }) as AccountApi;

  it("stays closed, and reads nothing, until it is asked for", async () => {
    const read = vi.fn(async () => parts);
    render(<MyQuestions api={withThread(read)} />);
    const button = await screen.findByRole("button", { name: /^Read the conversation/ });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(read).not.toHaveBeenCalled();
    expect(screen.queryByText("Check the Billing page.")).toBeNull();
  });

  it("shows each part oldest first with who wrote it and its date", async () => {
    const read = vi.fn(async () => parts);
    render(<MyQuestions api={withThread(read)} />);
    fireEvent.click(await screen.findByRole("button", { name: /^Read the conversation/ }));
    const region = await screen.findByRole("group", { name: text.conversationLabel("My invoice") });
    await waitFor(() => expect(within(region).getAllByRole("listitem")).toHaveLength(4));
    expect(read).toHaveBeenCalledWith(DAY);
    expect(within(region).getAllByRole("listitem").map((r) => r.textContent)).toEqual([
      "You15 January 2027Why was I charged\ntwice?",
      "DMbot's helper (a program)15 January 2027Check the Billing page.",
      "You16 January 2027<b>That</b> did not help.",
      "DMbot team17 January 2027Fixed, sorry.",
    ]);
    // Words, never markup.
    expect(region.querySelector("b")).toBeNull();
    const button = screen.getByRole("button", { name: /^Hide the conversation/ });
    expect(button.getAttribute("aria-expanded")).toBe("true");
  });

  it("hides it again, and reads it afresh the next time so a new reply shows", async () => {
    const read = vi.fn(async () => parts);
    render(<MyQuestions api={withThread(read)} />);
    fireEvent.click(await screen.findByRole("button", { name: /^Read the conversation/ }));
    await screen.findByText("Fixed, sorry.");
    fireEvent.click(screen.getByRole("button", { name: /^Hide the conversation/ }));
    expect(screen.queryByText("Fixed, sorry.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Read the conversation/ }));
    await screen.findByText("Fixed, sorry.");
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("says plainly when it can't be read, and the list stays", async () => {
    const read = vi.fn(async (): Promise<never> => Promise.reject(new ApiError("not-found")));
    render(<MyQuestions api={withThread(read)} />);
    fireEvent.click(await screen.findByRole("button", { name: /^Read the conversation/ }));
    expect(await screen.findByText(text.conversationError)).toBeTruthy();
    expect(screen.getByText("My invoice")).toBeTruthy();
    // And there is a way to try again from the message itself.
    fireEvent.click(screen.getByRole("button", { name: text.conversationRetry }));
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("names each button by its question, and says when there is no reply yet", async () => {
    const read = vi.fn(async () => [parts[0]!]);
    render(<MyQuestions api={withThread(read)} />);
    const button = await screen.findByRole("button", { name: "Read the conversation: My invoice" });
    fireEvent.click(button);
    expect(await screen.findByText(text.conversationNoReply)).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe(text.conversationLoaded);
  });

  it("lets only the newest read set what is shown", async () => {
    let first!: (v: typeof parts) => void;
    const read = vi
      .fn()
      .mockImplementationOnce(() => new Promise<typeof parts>((done) => (first = done)))
      .mockImplementation(async () => [{ ...parts[3]! }]);
    render(<MyQuestions api={withThread(read)} />);
    fireEvent.click(await screen.findByRole("button", { name: /^Read the conversation/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Hide the conversation/ }));
    fireEvent.click(screen.getByRole("button", { name: /^Read the conversation/ }));
    await screen.findByText("Fixed, sorry.");
    first(parts);
    await new Promise((done) => setTimeout(done, 0));
    expect(screen.queryByText("Check the Billing page.")).toBeNull();
  });

  it("has no button on an API older than the conversation", async () => {
    const { supportThread: _none, ...older } = apiWith(question) as AccountApi & { supportThread?: unknown };
    render(<MyQuestions api={older as AccountApi} />);
    await screen.findByText("My invoice");
    expect(screen.queryByRole("button", { name: /^Read the conversation/ })).toBeNull();
  });
});

describe("the conversation API call (#2193)", () => {
  const fetchOf = (body: unknown, status = 200) =>
    vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));

  it("reads the person's own question by the time it was sent, and keeps only well-formed parts", async () => {
    const fetcher = fetchOf({
      messages: [
        { who: "you", text: "Hi", at: 5 },
        { who: "someone", text: "odd", at: 6 },
        { who: "team", text: 3, at: 7 },
        { who: "helper", text: "Hello", at: 8 },
      ],
    });
    const api = httpApi("https://api.dmbot.example", fetcher as unknown as typeof fetch);
    expect(await api.supportThread?.(1_800_000_000)).toEqual([
      { who: "you", text: "Hi", at: 5 },
      { who: "helper", text: "Hello", at: 8 },
    ]);
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.dmbot.example/support/mine/1800000000");
    expect(init.method).toBe("GET");
  });

  it("is an error for a question that isn't theirs or isn't there", async () => {
    const api = httpApi("https://api.dmbot.example", fetchOf({ detail: "not_found" }, 404) as unknown as typeof fetch);
    await expect(api.supportThread?.(1)).rejects.toBeInstanceOf(ApiError);
  });
});
