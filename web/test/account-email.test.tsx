/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The email setting on the account page (#2349): a payment email is changed in Stripe (asked for only
// on a press), any other only after a confirmation link, an address that is not one is refused, and
// payments-off says so.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Account from "../src/account/Account";
import { ApiError, httpApi } from "../src/account/api";
import { mockApi, type Scenario } from "../src/account/mock";
import { text } from "../src/content/account";

afterEach(cleanup);

function show(scenario: Scenario) {
  const api = mockApi(scenario);
  const go = vi.fn<(url: string) => void>();
  render(<Account api={api} go={go} />);
  return { api, go };
}

describe("a payment email", () => {
  it("names the address and sends the person to Stripe only on a press", async () => {
    const { api, go } = show("table");
    await screen.findByText(text.emailNow("dm@example.com"));
    expect(screen.getByText(text.emailPayment)).toBeTruthy();
    expect(screen.getByText(text.emailPaymentHelp)).toBeTruthy();
    expect(api.calls).not.toContain("portal");
    expect(screen.queryByRole("button", { name: text.emailChange })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: text.emailPaymentButton }));
    await waitFor(() => expect(api.calls).toContain("portal"));
    await waitFor(() => expect(go).toHaveBeenCalled());
  });

  it("says so when payments are not switched on", async () => {
    const { api } = show("table");
    api.billingPortalUrl = () => Promise.reject(new ApiError("payments-off"));
    await screen.findByText(text.emailNow("dm@example.com"));
    fireEvent.click(screen.getByRole("button", { name: text.emailPaymentButton }));
    expect(await screen.findByText(text.emailPortalOff)).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.emailPaymentButton })).toBeNull();
  });
});

describe("an API that does not have the routes yet", () => {
  it("shows no section and no alarm when the answer is 'not found'", async () => {
    const api = mockApi("table");
    api.myEmail = () => Promise.reject(new ApiError("not-found"));
    render(<Account api={api} go={vi.fn()} />);
    await screen.findByText(text.idHeading);
    await waitFor(() => expect(api.calls).toContain("me"));
    expect(screen.queryByText(text.myEmailHeading)).toBeNull();
    expect(screen.queryByText(text.emailLoadFailed)).toBeNull();
  });

  it("still warns when it is a real failure", async () => {
    const api = mockApi("table");
    api.myEmail = () => Promise.reject(new ApiError("server"));
    render(<Account api={api} go={vi.fn()} />);
    expect(await screen.findByText(text.emailLoadFailed)).toBeTruthy();
  });
});

describe("the Stripe page", () => {
  it("is never opened at an address that is not the payment company's", async () => {
    const { api, go } = show("table");
    api.billingPortalUrl = () => Promise.resolve("https://evil.example/");
    await screen.findByText(text.emailNow("dm@example.com"));
    fireEvent.click(screen.getByRole("button", { name: text.emailPaymentButton }));
    await waitFor(() => expect(screen.getAllByRole("alert").length).toBeGreaterThan(0));
    expect(go).not.toHaveBeenCalled();
    expect(screen.queryByText(text.emailPortalOff)).toBeNull();
  });

  it("says it did not work, not 'payments are off', when the failure is something else", async () => {
    const { api } = show("table");
    api.billingPortalUrl = () => Promise.reject(new ApiError("server"));
    await screen.findByText(text.emailNow("dm@example.com"));
    fireEvent.click(screen.getByRole("button", { name: text.emailPaymentButton }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.actionFailed);
    expect(screen.queryByText(text.emailPortalOff)).toBeNull();
  });
});

describe("opening the emailed link", () => {
  const withPending = async (hash: string, scenario: Scenario = "try-it") => {
    const api = mockApi(scenario);
    await api.changeEmail!("new@example.org");
    const go = vi.fn();
    window.history.replaceState(null, "", `/account${hash}`);
    render(<Account api={api} go={go} hash={hash} />);
    return api;
  };

  it("changes the email when signed in, and takes the code out of the address bar", async () => {
    const api = await withPending("#email-confirm=good-code");
    expect(await screen.findByText(text.emailConfirmed("new@example.org"))).toBeTruthy();
    expect(api.calls).toContain("confirmEmail");
    expect(screen.getByText(text.emailNow("new@example.org"))).toBeTruthy();
    expect(window.location.hash).toBe("");
  });

  it("says a wrong, used or ended link is no good, and keeps the old email", async () => {
    await withPending("#email-confirm=wrong-code");
    expect(await screen.findByText(text.emailConfirmBad)).toBeTruthy();
    expect(screen.getByText(text.emailNow("dm@example.com"))).toBeTruthy();
  });

  it("asks a signed-out person to sign in first, and does not spend the code", async () => {
    const api = mockApi("signed-out");
    window.history.replaceState(null, "", "/account#email-confirm=good-code");
    render(<Account api={api} go={vi.fn()} hash="#email-confirm=good-code" />);
    expect(await screen.findByText(text.emailConfirmSignIn)).toBeTruthy();
    expect(api.calls).not.toContain("confirmEmail");
    expect(window.location.hash).toBe("#email-confirm=good-code");
  });
});

describe("a change that is waiting", () => {
  it("can be cancelled, and the old email carries on", async () => {
    const api = mockApi("try-it");
    await api.changeEmail!("new@example.org");
    render(<Account api={api} go={vi.fn()} />);
    await screen.findByText(text.emailPending("new@example.org", "dm@example.com"));
    fireEvent.click(screen.getByRole("button", { name: text.emailCancel }));
    expect(await screen.findByText(text.emailCancelled)).toBeTruthy();
    expect(screen.queryByText(text.emailPending("new@example.org", "dm@example.com"))).toBeNull();
    expect(api.calls).toContain("cancelEmailChange");
  });

  it("says plainly when another account already uses the new address", async () => {
    show("try-it");
    await screen.findByText(text.emailNow("dm@example.com"));
    fireEvent.click(screen.getByRole("button", { name: text.emailChange }));
    fireEvent.input(screen.getByLabelText(text.emailNewLabel), { target: { value: "taken@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: text.emailSend }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.errors["in-use"]);
  });
});

describe("any other email", () => {
  const open = async () => {
    const shown = show("try-it");
    await screen.findByText(text.emailNow("dm@example.com"));
    fireEvent.click(screen.getByRole("button", { name: text.emailChange }));
    return shown;
  };

  it("changes only after a link is sent, and says which inbox to check", async () => {
    const { api } = await open();
    fireEvent.input(screen.getByLabelText(text.emailNewLabel), { target: { value: "new@example.org" } });
    fireEvent.click(screen.getByRole("button", { name: text.emailSend }));
    expect(await screen.findByText(text.emailPending("new@example.org", "dm@example.com"))).toBeTruthy();
    expect(api.calls).toContain("changeEmail:new@example.org");
    // The address in use has not changed: only the confirmation does that.
    expect(screen.getByText(text.emailNow("dm@example.com"))).toBeTruthy();
  });

  it("refuses something that is not an email address, in plain words", async () => {
    await open();
    fireEvent.input(screen.getByLabelText(text.emailNewLabel), { target: { value: "not an address" } });
    fireEvent.click(screen.getByRole("button", { name: text.emailSend }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.errors["bad-email"]);
  });

  it("asks for an address first when the box is empty, and sends nothing", async () => {
    const { api } = await open();
    fireEvent.click(screen.getByRole("button", { name: text.emailSend }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.emailTypeFirst);
    expect(api.calls.some((c) => c.startsWith("changeEmail"))).toBe(false);
  });

  it("says what the link does before the button is pressed, and what happens until it is opened", async () => {
    await open();
    expect(screen.getByText(text.emailLinkNote)).toBeTruthy();
    expect(text.emailPending("n@example.org", null)).toContain("DMbot has no email for you");
    expect(text.emailPending("n@example.org", "o@example.com")).toContain("keeps emailing o@example.com");
  });

  it("offers a new link, not a plain Change, while one is waiting", async () => {
    await open();
    fireEvent.input(screen.getByLabelText(text.emailNewLabel), { target: { value: "new@example.org" } });
    fireEvent.click(screen.getByRole("button", { name: text.emailSend }));
    await screen.findByText(text.emailPending("new@example.org", "dm@example.com"));
    expect(screen.getByRole("button", { name: text.emailSendAgain })).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.emailChange })).toBeNull();
  });

  it("clears an old complaint when backing out", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: text.emailSend }));
    await screen.findByText(text.emailTypeFirst);
    fireEvent.click(screen.getByRole("button", { name: text.cancel }));
    fireEvent.click(screen.getByRole("button", { name: text.emailChange }));
    expect(screen.queryByText(text.emailTypeFirst)).toBeNull();
  });

  it("lets the person back out", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: text.cancel }));
    expect(screen.getByRole("button", { name: text.emailChange })).toBeTruthy();
  });
});

describe("the real API's calls", () => {
  it("reads with GET and changes with PUT, sending the address in the body only", async () => {
    const seen: { url: string; method: string; body: string }[] = [];
    const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
      seen.push({ url, method: init?.method ?? "GET", body: String(init?.body ?? "") });
      return new Response(JSON.stringify({ address: "a@example.com", source: "signin", pending: "b@example.com" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch);
    await api.myEmail!();
    await api.changeEmail!("b@example.com");
    expect(seen[0]).toMatchObject({ url: "https://api.example/me/email", method: "GET" });
    expect(seen[1]).toMatchObject({ url: "https://api.example/me/email", method: "PUT", body: '{"address":"b@example.com"}' });
    // The address travels in the body only, never in a URL.
    expect(seen.every((s) => !s.url.includes("example.com") || s.url === "https://api.example/me/email")).toBe(true);
    expect(seen.some((s) => s.url.includes("b@example.com"))).toBe(false);
  });

  it("maps bad_email to its own kind", async () => {
    const api = httpApi("https://api.example", (async () =>
      new Response(JSON.stringify({ error: "bad_email" }), { status: 400, headers: { "Content-Type": "application/json" } })) as typeof fetch);
    await expect(api.changeEmail!("x")).rejects.toMatchObject({ kind: "bad-email" });
  });
});

describe("an API without the setting", () => {
  it("shows no email section", async () => {
    const api = mockApi("table");
    delete (api as { myEmail?: unknown }).myEmail;
    render(<Account api={api} go={vi.fn()} />);
    await screen.findByText(text.idHeading);
    expect(screen.queryByText(text.myEmailHeading)).toBeNull();
  });
});
