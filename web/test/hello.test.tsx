/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The "Say hello" forms (#665) against a stubbed API: what is sent, and what the person
// is told for every answer.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { lettersLeft, text } from "../src/content/hello";
import { httpSend, type Note, type Refusal, type SendResult } from "../src/hello/api";
import HelloForm from "../src/hello/HelloForm";

afterEach(cleanup);

const POST = "https://github.com/smv620/Discord-DMbot/discussions/7";
const SENT: SendResult = { sent: true, url: POST };
const no = (why: Refusal): SendResult => ({ sent: false, why });

function show(kind: "feedback" | "question", answer: SendResult = SENT) {
  const send = vi.fn<(note: Note) => Promise<SendResult>>(async () => answer);
  render(<HelloForm kind={kind} send={send} siteKey="" />);
  return send;
}

function type(label: string, value: string) {
  fireEvent.input(screen.getByLabelText(label), { target: { value } });
}

describe("the form", () => {
  it("sends feedback with the contact, then thanks the person", async () => {
    const send = show("feedback");
    type(text.feedback.label, "  Love the rules alerts!  ");
    type(text.feedback.contactLabel, "bel#1");
    fireEvent.click(screen.getByRole("button", { name: text.feedback.send }));
    const thanks = await screen.findByRole("status");
    expect(thanks.textContent).toBe(text.sent);
    await waitFor(() => expect(document.activeElement).toBe(thanks));
    expect(screen.getByRole("link", { name: text.seePost }).getAttribute("href")).toBe(POST);
    expect(send).toHaveBeenCalledWith({
      kind: "feedback",
      message: "Love the rules alerts!",
      contact: "bel#1",
      turnstile: "",
    });
    fireEvent.click(screen.getByRole("button", { name: text.sendAnother }));
    expect((screen.getByLabelText(text.feedback.label) as HTMLTextAreaElement).value).toBe("");
  });

  it("sends a question without a contact", async () => {
    const send = show("question");
    type(text.question.label, "Does it work on phones?");
    fireEvent.click(screen.getByRole("button", { name: text.question.send }));
    await screen.findByRole("status");
    expect(send.mock.calls[0]?.[0]).toMatchObject({ kind: "question", contact: "" });
  });

  it("says what to do when the message is empty or too long, without sending", async () => {
    const send = show("feedback");
    fireEvent.click(screen.getByRole("button", { name: text.feedback.send }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.errors.empty);
    const box = screen.getByLabelText(text.feedback.label);
    expect(box.getAttribute("aria-invalid")).toBe("true");
    await waitFor(() => expect(document.activeElement).toBe(box));
    type(text.feedback.label, "a".repeat(2001));
    expect(screen.getByText(lettersLeft(2001))).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: text.feedback.send }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.errors["too-long"]);
    expect(send).not.toHaveBeenCalled();
  });

  it.each([["slow-down"], ["busy"], ["not-human"], ["off"], ["failed"]] as const)(
    "%s: keeps the message and says what to do",
    async (answer) => {
      show("feedback", no(answer));
      type(text.feedback.label, "Hello");
      fireEvent.click(screen.getByRole("button", { name: text.feedback.send }));
      expect((await screen.findByRole("alert")).textContent).toBe(text.errors[answer]);
      expect((screen.getByLabelText(text.feedback.label) as HTMLTextAreaElement).value).toBe(
        "Hello",
      );
    },
  );

  it("shows the count only near the limit", () => {
    show("feedback");
    type(text.feedback.label, "a".repeat(1799));
    expect(screen.queryByText(lettersLeft(1799))).toBeNull();
    type(text.feedback.label, "a".repeat(1800));
    expect(screen.getByText(lettersLeft(1800))).toBeTruthy();
  });

  it("counts letters left", () => {
    expect(lettersLeft(0)).toBe("2,000 letters left");
    expect(lettersLeft(1999)).toBe("1 letter left");
    expect(lettersLeft(2003)).toBe("3 letters too many");
  });
});

describe("httpSend", () => {
  function stub(status: number, body?: unknown) {
    return vi.fn<typeof fetch>(
      async () =>
        new Response(body === undefined ? null : JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
    );
  }
  const note: Note = { kind: "question", message: "Hi", contact: " ", turnstile: "tok" };

  it("posts the form with the API's header and no cookie", async () => {
    const fetcher = stub(200, { url: POST });
    expect(await httpSend("https://api.example/", fetcher)(note)).toEqual(SENT);
    const [url, init] = fetcher.mock.calls[0] ?? [];
    expect(url).toBe("https://api.example/feedback");
    expect(init?.method).toBe("POST");
    expect(init?.credentials).toBe("omit");
    expect(init?.headers).toMatchObject({ "X-DMbot-Request": "1" });
    expect(JSON.parse(String(init?.body))).toEqual({
      kind: "question",
      message: "Hi",
      contact: null,
      turnstile: "tok",
    });
  });

  it.each([
    [429, "slow_down", "slow-down"],
    [429, "busy", "busy"],
    [400, "too_long", "too-long"],
    [400, "not_human", "not-human"],
    [400, "contact_too_long", "contact-too-long"],
    [503, "feedback_off", "off"],
    [502, "feedback_unavailable", "failed"],
    [500, undefined, "failed"],
  ] as const)("%i %s → %s", async (status, error, result) => {
    const fetcher = stub(status, error === undefined ? undefined : { error });
    expect(await httpSend("/api", fetcher)(note)).toEqual(no(result));
  });

  it("a network failure says try later", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => {
      throw new TypeError("offline");
    });
    expect(await httpSend("/api", fetcher)(note)).toEqual(no("failed"));
  });
});

describe("the page's privacy line", () => {
  it("is the agreed wording", () => {
    expect(text.privacy).toBe(
      "Only your message is shown in public. How to reach you stays with the team.",
    );
    expect(text.sent).toBe("Thanks, we read every message.");
  });
});

describe("sending twice", () => {
  it("a second press while sending doesn't send again", async () => {
    let finish: (r: SendResult) => void = () => undefined;
    const send = vi.fn<(note: Note) => Promise<SendResult>>(
      () => new Promise((resolve) => (finish = resolve)),
    );
    render(<HelloForm kind="feedback" send={send} siteKey="" />);
    type(text.feedback.label, "Hello");
    const button = screen.getByRole("button", { name: text.feedback.send });
    fireEvent.click(button);
    fireEvent.click(button);
    finish(SENT);
    await screen.findByRole("status");
    expect(send).toHaveBeenCalledTimes(1);
  });
});

describe("the person check", () => {
  it("only loads once someone starts using a form", () => {
    render(<HelloForm kind="feedback" send={vi.fn()} siteKey="key" />);
    const script = () => document.head.querySelector('script[src*="challenges.cloudflare.com"]');
    expect(script()).toBeNull();
    fireEvent.focusIn(screen.getByLabelText(text.feedback.label));
    expect(script()).not.toBeNull();
  });
});

describe("the person check failing", () => {
  it("says so in its place at once, without losing the words", async () => {
    // happy-dom can't load the script: its error is what a blocked check looks like.
    render(<HelloForm kind="feedback" send={vi.fn()} siteKey="key" />);
    type(text.feedback.label, "Hello");
    fireEvent.focusIn(screen.getByLabelText(text.feedback.label));
    expect((await screen.findByRole("alert")).textContent).toBe(text.checkFailed);
    expect((screen.getByLabelText(text.feedback.label) as HTMLTextAreaElement).value).toBe(
      "Hello",
    );
  });
});

describe("follow-ups (#710 review)", () => {
  it("doesn't send without a tick from the person check", async () => {
    const send = vi.fn<(note: Note) => Promise<SendResult>>(async () => SENT);
    render(<HelloForm kind="feedback" send={send} siteKey="key" />);
    type(text.feedback.label, "Hello");
    fireEvent.click(screen.getByRole("button", { name: text.feedback.send }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/tick|check/);
    expect(send).not.toHaveBeenCalled();
  });

  it("tells a question sent without a contact where the answer will be", async () => {
    show("question");
    type(text.question.label, "Does it work on phones?");
    fireEvent.click(screen.getByRole("button", { name: text.question.send }));
    expect(await screen.findByText(text.answerOnGitHub)).toBeTruthy();
  });

  it("says nothing extra when the question has a contact", async () => {
    show("question");
    type(text.question.label, "Does it work on phones?");
    type(text.question.contactLabel, "bel#1");
    fireEvent.click(screen.getByRole("button", { name: text.question.send }));
    await screen.findByRole("status");
    expect(screen.queryByText(text.answerOnGitHub)).toBeNull();
  });
});
