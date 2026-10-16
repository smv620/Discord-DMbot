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
    expect(await screen.findByText(/· Closed$/)).toBeTruthy();
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
      "InvoiceSent 15 January 2027 · Waiting",
      "ServerSent 14 January 2027 · Answered — check your email",
      "OldSent 13 January 2027 · Closed",
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
    expect(await screen.findByText(/Sent 15 January 2027 · Answered$/)).toBeTruthy();
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
              null,
            ],
            reply: false,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    );
    const api = httpApi("https://api.dmbot.example", fetchMock as unknown as typeof fetch);
    const found = await api.mySupport!();
    expect(found).toEqual({ tickets: [{ at: 5, subject: "ok", status: "new" }], reply: false });
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
