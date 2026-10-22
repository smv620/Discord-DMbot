/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Which address a recap's email goes to, and where to change it (#2335): each source, no address,
// emails off, and the payment company's page asked for only on a press.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type RecapEmail } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import RecapEmailLine from "../src/account/RecapEmail";
import Recaps from "../src/account/Recaps";
import { choiceWords } from "../src/content/recap-choices";
import { emailWords as w } from "../src/content/recap-email";
import { text } from "../src/content/recaps";

afterEach(cleanup);

const mount = (email: RecapEmail | undefined, over: { go?: (u: string) => void; portal?: () => Promise<string> } = {}) => {
  const api = mockApi("table");
  const portal = vi.fn(over.portal ?? (async () => "https://billing.stripe.com/p/session/x"));
  api.billingPortalUrl = portal;
  render(<RecapEmailLine api={api} email={email} go={over.go} />);
  return { api, portal };
};

describe("a sign-in email", () => {
  it("names the address, with no link to a setting that does not exist yet (#2349)", () => {
    mount({ address: "dm@example.com", source: "signin", on: true });
    expect(screen.getByText(/We'll email dm@example\.com when it's ready\./)).toBeTruthy();
    expect(screen.getByText(new RegExp(w.signin))).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("a payment email", () => {
  const pay: RecapEmail = { address: "pay@example.com", source: "payment", on: true };

  it("names the address and says how to change it in Stripe, without asking Stripe yet", () => {
    const { portal } = mount(pay);
    expect(screen.getByText(/We'll email pay@example\.com when it's ready\./)).toBeTruthy();
    expect(screen.getByText(w.paymentHelp)).toBeTruthy();
    expect(portal).not.toHaveBeenCalled();
  });

  it("asks for the portal only when pressed, then goes there", async () => {
    const go = vi.fn();
    const { portal } = mount(pay, { go });
    fireEvent.click(screen.getByRole("button", { name: w.changePayment }));
    await waitFor(() => expect(go).toHaveBeenCalledWith("https://billing.stripe.com/p/session/x"));
    expect(portal).toHaveBeenCalledTimes(1);
  });

  it("refuses to go anywhere that isn't the payment company", async () => {
    const go = vi.fn();
    mount(pay, { go, portal: async () => "https://evil.example/login" });
    fireEvent.click(screen.getByRole("button", { name: w.changePayment }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.failed);
    expect(go).not.toHaveBeenCalled();
  });

  it("points to the account page while payments are off", async () => {
    mount(pay, {
      portal: async () => {
        throw new ApiError("payments-off");
      },
    });
    fireEvent.click(screen.getByRole("button", { name: w.changePayment }));
    expect(await screen.findByText(new RegExp(w.paymentsOff))).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("button", { name: w.changePayment })).toBeNull();
  });

  it("says when the session ended", async () => {
    mount(pay, {
      portal: async () => {
        throw new ApiError("signed-out");
      },
    });
    fireEvent.click(screen.getByRole("button", { name: w.changePayment }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.signedOut);
  });
});

describe("no address, or emails turned off", () => {
  it("promises nothing without an address, and has no link to a dead end", () => {
    mount({ address: null, source: "none", on: true });
    expect(screen.getByText(new RegExp(w.none))).toBeTruthy();
    expect(screen.queryByText(/We'll email/)).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("promises nothing with emails off, and links to the switch on the page", () => {
    mount({ address: "dm@example.com", source: "signin", on: false });
    expect(screen.getByText(new RegExp("Emails are off"))).toBeTruthy();
    expect(screen.queryByText(/We'll email/)).toBeNull();
    expect(screen.queryByText(/dm@example\.com/)).toBeNull();
    expect(screen.getByRole("link", { name: w.turnOn }).getAttribute("href")).toBe("#recap-email-switch");
  });

  it("the link lands on the email switch itself", async () => {
    const api = mockApi("table");
    const real = api.recap!.bind(api);
    api.recap = async (r) => ({
      ...(await real(r)),
      working: true,
      state: "working",
      canPost: false,
      canEdit: false,
      canTryAgain: false,
      canDrop: false,
      recapEmail: { address: "dm@example.com", source: "signin", on: false },
    });
    render(<Recaps api={api} path={`/account/recaps/${mockRecaps()[0]!.ref}`} />);
    const link = await screen.findByRole("link", { name: w.turnOn });
    const box = await waitFor(() => {
      const found = document.getElementById("recap-email-switch");
      if (!found) throw new Error("not yet");
      return found;
    });
    fireEvent.click(link);
    expect(document.activeElement).toBe(box);
  });

  it("says nothing from an API that doesn't know the address yet", () => {
    const { container } = render(<RecapEmailLine api={mockApi("table")} email={undefined} />);
    expect(container.textContent).toBe("");
  });
});

describe("under the email switch in the recap choices", () => {
  const withEmail = (recapEmail: RecapEmail) => {
    const api = mockApi("table");
    const real = api.recap!.bind(api);
    api.recap = async (r) => ({ ...(await real(r)), recapEmail });
    render(<Recaps api={api} path={`/account/recaps/${mockRecaps()[0]!.ref}`} />);
  };

  it("does not promise an email when the switch is on and there is no address", async () => {
    withEmail({ address: null, source: "none", on: true });
    await screen.findByLabelText(choiceWords.labels.email);
    expect(await screen.findByText(new RegExp(w.none))).toBeTruthy();
    expect(screen.queryByText(/We'll email/)).toBeNull();
  });

  it("names the address when the switch is on", async () => {
    withEmail({ address: "dm@example.com", source: "signin", on: true });
    await screen.findByLabelText(choiceWords.labels.email);
    expect(await screen.findByText(/We'll email dm@example\.com when it's ready\./)).toBeTruthy();
  });

  it("says nothing about an address once the switch is off", async () => {
    withEmail({ address: "dm@example.com", source: "signin", on: true });
    const box = (await screen.findByLabelText(choiceWords.labels.email)) as HTMLInputElement;
    await screen.findByText(/We'll email/);
    fireEvent.click(box);
    await waitFor(() => expect(screen.queryByText(/We'll email/)).toBeNull());
  });
});

describe("while a recap is being made, with the choices panel on the same page", () => {
  const making = (recapEmail: RecapEmail) => {
    const api = mockApi("table");
    const real = api.recap!.bind(api);
    api.recap = async (r) => ({
      ...(await real(r)),
      working: true,
      state: "working",
      canPost: false,
      canEdit: false,
      canTryAgain: false,
      canDrop: false,
      recapEmail,
    });
    render(<Recaps api={api} path={`/account/recaps/${mockRecaps()[0]!.ref}`} />);
    return api;
  };

  it("names the address once, with one Stripe button", async () => {
    making({ address: "pay@example.com", source: "payment", on: true });
    const box = (await screen.findByLabelText(choiceWords.labels.email)) as HTMLInputElement;
    await waitFor(() => expect(box.checked).toBe(true));
    expect(screen.getAllByText(/We'll email pay@example\.com/)).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: w.changePayment })).toHaveLength(1);
  });

  it("follows the switch: turning emails off takes the promise away", async () => {
    making({ address: "dm@example.com", source: "signin", on: true });
    const box = (await screen.findByLabelText(choiceWords.labels.email)) as HTMLInputElement;
    await screen.findByText(/We'll email dm@example\.com/);
    fireEvent.click(box);
    await waitFor(() => expect(screen.queryByText(/We'll email/)).toBeNull());
    expect(screen.getByText(new RegExp("Emails are off"))).toBeTruthy();
  });

  it("follows the switch the other way: the first read said off, the switch is on", async () => {
    making({ address: "dm@example.com", source: "signin", on: false });
    const box = (await screen.findByLabelText(choiceWords.labels.email)) as HTMLInputElement;
    await waitFor(() => expect(box.checked).toBe(true));
    expect(await screen.findByText(/We'll email dm@example\.com/)).toBeTruthy();
  });
});

describe("on the recap page", () => {
  const ref = mockRecaps()[0]!.ref;

  it("names the address while a recap is being made, and not on a finished one", async () => {
    const api = mockApi("table");
    const real = api.recap!.bind(api);
    api.recap = async (r) => ({ ...(await real(r)), working: true, state: "working", canPost: false, canEdit: false, canTryAgain: false, canDrop: false });
    render(<Recaps api={api} path={`/account/recaps/${ref}`} />);
    expect(await screen.findByText(/We'll email dm@example\.com when it's ready\./)).toBeTruthy();
    cleanup();
    render(<Recaps api={mockApi("table")} path={`/account/recaps/${ref}`} />);
    await screen.findByText(text.onlyYou);
    expect(screen.queryByText(/We'll email/)).toBeNull();
  });

  it("no longer promises an email in the 'making it again' note", () => {
    expect(text.tryingAgain).not.toMatch(/email/i);
  });
});
