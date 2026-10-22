/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Accessibility of this week's signed-in pages (#1466): axe-core on the recap list and review page
// (with the audio part and the names part open), My Account's "Your questions" in every state, and
// the admin Tickets page (the filter, a thread, the reply box). Each page is also checked for the
// things axe cannot see in happy-dom: a name for every control, the status words read together with
// the subject, and a live region for every "Sent / Saved / Posted" message. Colour contrast is
// measured from the stylesheet (a11y-contrast.test.ts), the built pages by `npm run check:a11y`.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type AdminApi, type SupportTicketRow } from "../src/admin/api";
import Tickets from "../src/admin/Tickets";
import { type AccountApi, type MySupport } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import { MyQuestions } from "../src/account/MyQuestions";
import Recaps from "../src/account/Recaps";
import { nameWords } from "../src/content/recap-names";
import { text as adminText } from "../src/content/admin";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/recap-words", async (original) => {
  const real = await original<typeof import("../src/content/recap-words")>();
  return {
    ...real,
    get RECAPS_LIVE() {
      return live.on;
    },
    get RECAP_AUDIO_LIVE() {
      return live.on;
    },
  };
});

afterEach(() => {
  cleanup();
  live.on = true;
});

async function serious(): Promise<string[]> {
  const result = await axe.run(document.body, {
    rules: { "color-contrast": { enabled: false }, region: { enabled: false } },
  });
  return result.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

const REF = "200000000000000001.cmp1.1791000000";

describe("the recap pages", () => {
  it("the list has nothing serious or critical", async () => {
    render(<Recaps api={mockApi("table")} path="/account/recaps" />);
    await screen.findByText("The ferry at dusk");
    expect(await serious()).toEqual([]);
  });

  it("the review page, with the audio and the names part open, has nothing serious or critical", async () => {
    const api = mockApi("table");
    const read = api.recap!.bind(api);
    api.recap = async (ref) => ({
      ...(await read(ref)),
      summary: "《Tense》 The ferry crossed. ((pause)) Nobody spoke.",
      audio: { state: "ready", error: "", current: true, canAskAgain: true },
    });
    render(<Recaps api={api} path={`/account/recaps/${REF}`} />);
    await screen.findByText(nameWords.heading);
    // The voice and music player (#2017) is part of what axe reads here.
    await screen.findByRole("switch", { name: "Voice" });
    const details = document.querySelector("details.names") as HTMLDetailsElement;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    await screen.findByLabelText(nameWords.label("Ka'zeth"));
    expect(await serious()).toEqual([]);
  });

  it("every name's box has its own label, and a result is announced", async () => {
    render(<Recaps api={mockApi("table")} path={`/account/recaps/${REF}`} />);
    await screen.findByText(nameWords.heading);
    const details = document.querySelector("details.names") as HTMLDetailsElement;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    const box = await screen.findByLabelText(nameWords.label("Ka'zeth"));
    fireEvent.input(box, { target: { value: "Kah-zeth" } });
    const save = box.parentElement!.querySelector<HTMLButtonElement>("button[id^='save-said-as']")!;
    save.focus();
    fireEvent.click(save);
    // A live region (role=status), so a screen reader hears "Saved." without moving focus.
    const saved = await within(details).findByText("Saved.");
    expect(saved.getAttribute("role")).toBe("status");
    // Focus is back on the Save button (it was disabled while saving), so the keyboard user is
    // where they were.
    await waitFor(() => expect(document.activeElement?.id).toBe(`save-said-as-${"a".repeat(32)}`));
  });
});

const DAY = 1_800_000_000;
const apiWith = (list: MySupport): AccountApi =>
  ({ ...mockApi("table"), mySupport: async () => list }) as AccountApi;

describe("My Account's Your questions", () => {
  it("has nothing serious or critical in any status, and each item says subject and status together", async () => {
    render(
      <MyQuestions
        api={apiWith({
          reply: true,
          tickets: [
            { at: DAY, subject: "Waiting one", status: "new" },
            { at: DAY - 1, subject: "Helped", status: "answered", answeredBy: "helper", latest: "Try the Billing page." },
            { at: DAY - 2, subject: "Team one", status: "answered", answeredBy: "team", latest: "Fixed it." },
            { at: DAY - 3, subject: "With the team", status: "escalated", answeredBy: "helper", latest: "Not sure." },
            { at: DAY - 4, subject: "Closed one", status: "closed" },
          ],
        })}
        canWrite
      />,
    );
    await screen.findByRole("heading", { name: "Your questions" });
    expect(await serious()).toEqual([]);
    // One list item reads as a whole: the subject, then where it stands, then the last reply.
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(5);
    expect(items[1]!.textContent).toMatch(/^Helped.*Answered by DMbot's AI helper.*Last reply: Try the Billing page\.Read the conversation$/);
    expect(items[3]!.textContent).toMatch(/^With the team.*· With the team.*Last reply: Not sure\.Read the conversation$/);
  });
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

function admin(rows: SupportTicketRow[], over: Partial<AdminApi> = {}) {
  const api = {
    tickets: vi.fn(async () => ({
      tickets: structuredClone(rows),
      counts: { new: 1, answered: 0, closed: 0, escalated: rows.length },
    })),
    setTicketStatus: vi.fn(async () => undefined),
    replyToTicket: vi.fn(async () => undefined),
    ...over,
  } as unknown as AdminApi;
  render(<Tickets api={api} csrf="c" onSignedOut={vi.fn()} onOff={vi.fn()} />);
  return api;
}

describe("the admin Tickets page", () => {
  it("has nothing serious or critical with a thread, a reason, a reply box and the filter", async () => {
    admin([ticket(), ticket({ id: 8, subject: "No email one", email: null }), ticket({ id: 9, subject: "Done", status: "closed" })]);
    await screen.findByText("Invoice looks wrong");
    expect(await serious()).toEqual([]);
  });

  it("every control has a name that says which ticket it is for", async () => {
    admin([ticket(), ticket({ id: 8, subject: "Second one" })]);
    await screen.findByText("Second one");
    // Two reply boxes: each is labelled with its own subject, so they are told apart by name.
    expect(screen.getByLabelText(adminText.replyLabel("Invoice looks wrong"))).toBeTruthy();
    expect(screen.getByLabelText(adminText.replyLabel("Second one"))).toBeTruthy();
    // The filter is a named group of buttons that say which is pressed.
    const group = screen.getByRole("group", { name: adminText.ticketsFilterLabel });
    expect(within(group).getAllByRole("button").every((b) => b.getAttribute("aria-pressed") !== null)).toBe(true);
  });

  it("announces 'Reply sent' in a live region, and an error as an alert", async () => {
    admin([ticket()]);
    await screen.findByText("Invoice looks wrong");
    fireEvent.input(screen.getByLabelText(adminText.replyLabel("Invoice looks wrong")), {
      target: { value: "Hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: adminText.replySend }));
    const status = await screen.findByText(adminText.replySent);
    expect(status.getAttribute("role")).toBe("status");
    cleanup();
    admin([ticket()], {
      replyToTicket: vi.fn(async () => {
        throw new Error("x");
      }),
    });
    await screen.findByText("Invoice looks wrong");
    fireEvent.input(screen.getByLabelText(adminText.replyLabel("Invoice looks wrong")), {
      target: { value: "Hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: adminText.replySend }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  });
});

// A browser drops the focus of a button that becomes disabled; happy-dom does not, so these tests
// do what the browser does (blur the active element while the request runs) and check that focus
// comes back somewhere sensible when it is done (#1466).
const blurring = <T,>(value: T) => async (): Promise<T> => {
  (document.activeElement as HTMLElement | null)?.blur();
  return value;
};

describe("focus after a button was pressed", () => {
  it("returns to the Save button after saving a name", async () => {
    const api = { ...mockApi("table"), saveSaidAs: blurring(undefined) } as unknown as AccountApi;
    render(<Recaps api={api} path={`/account/recaps/${REF}`} />);
    await screen.findByText(nameWords.heading);
    const details = document.querySelector("details.names") as HTMLDetailsElement;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    const box = await screen.findByLabelText(nameWords.label("Ka'zeth"));
    fireEvent.input(box, { target: { value: "Kah-zeth" } });
    const save = box.parentElement!.querySelector<HTMLButtonElement>("button[id^='save-said-as']")!;
    save.focus();
    fireEvent.click(save);
    await within(details).findByText("Saved.");
    await waitFor(() => expect(document.activeElement).toBe(save));
  });

  it("goes to the recap's heading after Approve and post (the buttons are gone)", async () => {
    const base = mockApi("table");
    const api = {
      ...base,
      approveRecap: async (ref: string, choice: "text", options?: { post?: boolean }) => {
        (document.activeElement as HTMLElement | null)?.blur();
        return base.approveRecap!(ref, choice, options);
      },
    } as unknown as AccountApi;
    render(<Recaps api={api} path={`/account/recaps/${REF}`} />);
    const post = await screen.findByRole("button", { name: /Approve and post text only/ });
    post.focus();
    fireEvent.click(post);
    await waitFor(() => expect(document.activeElement?.tagName).toBe("H2"));
    expect(document.activeElement?.textContent).toBe("The ferry at dusk");
  });

  it("goes back into the admin reply box after a reply that failed, words kept", async () => {
    admin([ticket()], {
      replyToTicket: async () => {
        (document.activeElement as HTMLElement | null)?.blur();
        throw new Error("down");
      },
    });
    await screen.findByText("Invoice looks wrong");
    const box = screen.getByLabelText(adminText.replyLabel("Invoice looks wrong")) as HTMLTextAreaElement;
    fireEvent.input(box, { target: { value: "My words" } });
    const send = screen.getByRole("button", { name: adminText.replySend });
    send.focus();
    fireEvent.click(send);
    await screen.findByRole("alert");
    await waitFor(() => expect(document.activeElement).toBe(box));
    expect(box.value).toBe("My words");
  });
});
