/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The account page (#434): every state from fixture data, every button, and the rule that
// tokens never go in URLs or browser storage.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Account from "../src/account/Account";
import { type Access, type AccountApi, ApiError, httpApi, type Me, type MyPlan } from "../src/account/api";
import {
  candidates,
  incomingOffers,
  mockApi,
  outgoingOffer,
  type Scenario,
  scenarioMe,
} from "../src/account/mock";
import { isSafeRedirect } from "../src/account/redirect";
import { hoursLeftLine, hoursUsedLine, text } from "../src/content/account";

afterEach(() => {
  cleanup();
  localStorage.clear();
  sessionStorage.clear();
});

function show(scenario: Scenario, search = "") {
  const api = mockApi(scenario);
  const go = vi.fn<(url: string) => void>();
  render(<Account api={api} go={go} search={search} />);
  return { api, go };
}

describe("states", () => {
  it("signed out: offers Discord sign-in and the plans", async () => {
    show("signed-out");
    const signIn = await screen.findByRole("link", { name: text.signIn });
    expect(signIn.getAttribute("href")).toBe("#demo-sign-in");
    expect(screen.getByText(text.signInNote)).toBeTruthy();
    expect(screen.getByRole("link", { name: text.seePrices }).getAttribute("href")).toBe(
      "/pricing",
    );
  });

  it("signed out: says so first, then Discord, then the email link, and the plans last (#2003)", async () => {
    show("signed-out");
    await screen.findByRole("heading", { name: text.signedOutHeading });
    const order = [
      document.getElementById("signed-out-heading"),
      screen.getByText(text.signedOutLead),
      screen.getByRole("link", { name: text.signIn }),
      document.getElementById("email-heading"),
      screen.getByLabelText(text.emailLabel),
      screen.getByRole("button", { name: text.emailButton }),
      document.getElementById("new-heading"),
      screen.getByRole("link", { name: text.startTryItFree }),
      screen.getAllByRole("link", { name: /^Choose / })[0]!,
    ];
    for (let i = 1; i < order.length; i++) {
      expect(
        order[i - 1]!.compareDocumentPosition(order[i]!) & Node.DOCUMENT_POSITION_FOLLOWING,
        `step ${i}`,
      ).toBeTruthy();
    }
    expect(screen.getByRole("heading", { name: text.newHeading })).toBeTruthy();
    // Every way in goes through the same Discord sign-in, and there is one sign-in button before the plans.
    expect(screen.getAllByRole("link", { name: text.signIn })).toHaveLength(1);
  });

  it("signed out, an API older than the email link: no email heading, Discord still first (#2003)", async () => {
    const api = mockApi("signed-out");
    delete api.emailSignInRequest;
    render(<Account api={api} go={vi.fn()} search="" />);
    await screen.findByRole("heading", { name: text.signedOutHeading });
    expect(document.getElementById("email-heading")).toBeNull();
    expect(
      screen.getByRole("link", { name: text.signIn }).compareDocumentPosition(
        document.getElementById("new-heading")!,
      ) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("signed out: a notice shows inside the sign-in panel, above the heading (#2003)", async () => {
    show("signed-out", "?signin=failed");
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(text.signInFailed);
    const panel = document.querySelector("[aria-labelledby='signed-out-heading']")!;
    expect(panel.contains(alert)).toBe(true);
    expect(
      alert.compareDocumentPosition(document.getElementById("signed-out-heading")!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("signed out after a failed sign-in: says so and what to do", async () => {
    show("signed-out", "?signin=failed");
    expect((await screen.findByRole("alert")).textContent).toBe(text.signInFailed);
  });

  it("signed out after too many sign-in tries: says to wait", async () => {
    show("signed-out", "?signin=slow");
    expect((await screen.findByRole("alert")).textContent).toBe(text.tooManyTries);
  });

  it("signed in after too many install tries: says to wait", async () => {
    show("table", "?install=slow");
    expect(await screen.findByText(text.install["slow"] ?? "")).toBeTruthy();
  });

  it("API down: says so with a Try again button", async () => {
    const { api } = show("down");
    expect((await screen.findByRole("alert")).textContent).toContain(text.down);
    fireEvent.click(screen.getByRole("button", { name: text.tryAgain }));
    await waitFor(() => expect(api.calls.filter((c) => c === "me")).toHaveLength(2));
  });

  it("no plan: Start Try It first, then the paid plans", async () => {
    const { api, go } = show("no-plan");
    const start = await screen.findByRole("button", { name: text.startTryIt });
    const buttons = screen.getAllByRole("button").map((b) => b.textContent);
    expect(buttons.indexOf(text.startTryIt)).toBeLessThan(buttons.indexOf("Choose Table"));
    expect(screen.getByText(text.startTryItNote)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Choose Two Tables" }));
    await waitFor(() => expect(go).toHaveBeenCalledWith("#demo-checkout-two-tables"));

    fireEvent.click(start);
    await screen.findByText("Try It");
    expect(api.calls).toContain("startTryIt");
    expect(screen.getByText(hoursUsedLine(0, 8))).toBeTruthy();
  });

  it("Table: hours as words and a bar, campaigns, servers", async () => {
    show("table");
    expect(await screen.findByText("Hi, Belleros.")).toBeTruthy();
    expect(screen.getByText(text.planRenews("Table", "2026-10-14"))).toBeTruthy();
    expect(screen.getByText("About 11 of 18 hours used")).toBeTruthy();
    const bar = screen.getByLabelText(text.hoursBarLabel) as HTMLProgressElement;
    expect([bar.value, bar.max]).toEqual([11.2, 18]);
    expect(screen.getByRole("button", { name: text.changePlan })).toBeTruthy();

    const ashen = document.querySelector('[data-campaign="cmp-ashen"]');
    expect(ashen?.textContent).toContain(text.paused);
    expect(ashen?.textContent).toContain(text.notPlayed);
    const varrow = document.querySelector('[data-campaign="cmp-varrow"]');
    expect(varrow?.textContent).toContain(text.youHelp);
    // Only campaigns you own can be handed over.
    expect(varrow?.querySelector("button")).toBeNull();

    expect(document.querySelector('[data-server="200000000000000001"]')?.textContent).toContain(
      text.youAddedIt,
    );
  });

  it("Try It: offers the paid plans instead of the billing page", async () => {
    show("try-it");
    await screen.findByText("Try It");
    expect(screen.queryByRole("button", { name: text.changePlan })).toBeNull();
    expect(screen.getByRole("button", { name: "Choose Table" })).toBeTruthy();
  });

  it("payment grace: warns with the date and a Fix button", async () => {
    const { go } = show("grace");
    const alert = await screen.findByText(/didn't go through/);
    expect(alert.textContent).toMatch(/Fix it by Oct 12 to keep your plan\./);
    fireEvent.click(screen.getByRole("button", { name: text.fixPayment }));
    await waitFor(() => expect(go).toHaveBeenCalledWith("#demo-billing"));
  });

  it("free access: says so, with no hours, prices or plan buttons", async () => {
    show("free");
    expect(await screen.findByText(text.freeAccess)).toBeTruthy();
    expect(screen.queryByLabelText(text.hoursBarLabel)).toBeNull();
    expect(screen.queryByRole("button", { name: text.startTryIt })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Choose / })).toBeNull();
    expect(screen.queryByText(text.pickPlan)).toBeNull();
    expect(screen.queryByText(text.noPlan)).toBeNull();
    expect(screen.queryByRole("button", { name: text.stopPaying })).toBeNull();
  });

  it("free access with an end date shows the date", async () => {
    show("free-until");
    expect(await screen.findByText("Free access until Dec 31")).toBeTruthy();
  });

  it("free access while still paying offers Stop paying, which opens the billing page", async () => {
    const { go } = show("free-paying");
    // The mock's plan renews on Oct 14: the line says when the plan runs until.
    expect(await screen.findByText(text.stillPaying("Table", "2026-10-14"))).toBeTruthy();
    expect(screen.queryByText(/still paying/)).toBeNull();
    expect(screen.queryByLabelText(text.hoursBarLabel)).toBeNull();
    expect(screen.queryByRole("button", { name: text.changePlan })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: text.stopPaying }));
    await waitFor(() => expect(go).toHaveBeenCalledWith("#demo-billing"));
  });

  it("lapsed: says the plan stopped and offers plans", async () => {
    show("lapsed");
    expect(await screen.findByText(text.lapsed)).toBeTruthy();
    expect(screen.queryByLabelText(text.hoursBarLabel)).toBeNull();
    expect(screen.getByRole("button", { name: "Choose Guild" })).toBeTruthy();
  });
});

describe("actions", () => {
  it("adds DMbot through a plain link to the API, which sends on to Discord", async () => {
    show("table");
    const row = await waitFor(() => {
      const found = document.querySelector('[data-server="200000000000000002"]');
      if (!found) throw new Error("not yet");
      return found;
    });
    expect(row.querySelector("a")?.getAttribute("href")).toBe("#demo-install-200000000000000002");
  });

  it("lets the person say they added DMbot to a server it joined by link", async () => {
    const { api } = show("table");
    const row = await waitFor(() => {
      const found = document.querySelector('[data-server="200000000000000003"]');
      if (!found) throw new Error("not yet");
      return found;
    });
    expect(row.textContent).toContain(text.linkNote);
    fireEvent.click(screen.getByRole("button", { name: text.linkServer }));
    await waitFor(() =>
      expect(
        document.querySelector('[data-server="200000000000000003"]')?.textContent,
      ).toContain(text.youAddedIt),
    );
    expect(api.calls).toContain("link:200000000000000003");
  });

  it("says what happened after coming back from adding DMbot", async () => {
    show("table", "?install=done");
    expect(await screen.findByText(text.install["done"] ?? "")).toBeTruthy();
    cleanup();
    show("table", "?install=whatever");
    expect(await screen.findByText(text.install["failed"] ?? "")).toBeTruthy();
  });

  it("keeps other servers' buttons free while one is busy", async () => {
    const api = mockApi("table");
    let release: () => void = () => {};
    api.linkServer = () =>
      new Promise<void>((resolve) => {
        release = resolve;
      });
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: text.linkServer }));
    await screen.findByRole("button", { name: text.busy });
    // Quillon's Corner's Add DMbot link is still there and usable.
    expect(
      document.querySelector('[data-server="200000000000000002"] a')?.getAttribute("href"),
    ).toBe("#demo-install-200000000000000002");
    release();
  });

  it("shows the install result once, then takes it out of the address", async () => {
    window.history.replaceState(null, "", "/account?install=done");
    show("table", "?install=done");
    expect(await screen.findByText(text.install["done"] ?? "")).toBeTruthy();
    await waitFor(() => expect(window.location.search).toBe(""));
  });

  it("says when the install came back signed out, or approved by someone else", async () => {
    show("signed-out", "?install=signed_out");
    expect((await screen.findByRole("alert")).textContent).toBe(text.installSignedOut);
    expect(screen.getByRole("link", { name: text.signIn })).toBeTruthy();
    cleanup();
    show("table", "?install=other_account");
    expect(await screen.findByText(text.install["other_account"] ?? "")).toBeTruthy();
  });

  it("asks for a fresh sign-in when the API wants one", async () => {
    show("table", "?install=sign_in_again");
    expect((await screen.findByRole("alert")).textContent).toBe(text.signInAgain);
    expect(screen.getByRole("link", { name: text.signIn })).toBeTruthy();
  });

  it("says free access means nothing to pay when checkout refuses", async () => {
    const api = mockApi("no-plan");
    api.checkoutUrl = vi.fn(async () => {
      throw new ApiError("free-access");
    });
    render(<Account api={api} go={vi.fn()} search="" />);
    fireEvent.click(await screen.findByRole("button", { name: "Choose Table" }));
    expect(await screen.findByText(text.errors["free-access"] ?? "")).toBeTruthy();
  });

  it("maps the API's has_free_access to free-access", async () => {
    const fetcher = vi.fn<typeof fetch>(
      async () =>
        new Response(JSON.stringify({ error: "has_free_access" }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
    );
    await expect(httpApi("/api", fetcher).startTryIt()).rejects.toMatchObject({
      kind: "free-access",
    });
  });

  it("explains the API's refusals in plain words", async () => {
    const api = mockApi("no-plan");
    api.startTryIt = () => Promise.reject(new ApiError("try-it-used"));
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: text.startTryIt }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.errors["try-it-used"]);
  });

  it("offers a campaign to a chosen person; it stays yours until they accept", async () => {
    const { api } = show("table");
    await screen.findByText("The Brynwater Crossing");
    const row = document.querySelector('[data-campaign="cmp-brynwater"]') as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: text.handOver }));

    const confirm = await screen.findByRole("button", { name: text.handOverConfirm });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText(candidates[0]?.name ?? ""));
    expect((confirm as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(confirm);

    expect(await screen.findByText(text.handOverDone("Oskar Vane"))).toBeTruthy();
    expect(api.calls).toContain("handover:cmp-brynwater:100000000000000002");
    await waitFor(() =>
      expect(within(row).getByRole("button", { name: text.withdraw })).toBeTruthy(),
    );
    expect(row.textContent).toContain("Oskar Vane");
  });

  it("says plainly when nobody can take a campaign", async () => {
    show("table");
    await screen.findByText("Ashen Crown");
    const row = document.querySelector('[data-campaign="cmp-ashen"]') as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: text.handOver }));
    expect(await screen.findByText(text.handOverNobody)).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.handOverConfirm })).toBeNull();
  });

  it("never tells the owner about the other person's plan", async () => {
    const api = mockApi("table");
    api.handover = () => Promise.reject(new ApiError("no-free-slot"));
    render(<Account api={api} go={vi.fn()} />);
    await screen.findByText("The Brynwater Crossing");
    const row = document.querySelector('[data-campaign="cmp-brynwater"]') as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: text.handOver }));
    fireEvent.click(await screen.findByLabelText("Mirelle"));
    fireEvent.click(screen.getByRole("button", { name: text.handOverConfirm }));
    const alert = (await screen.findByRole("alert")).textContent ?? "";
    expect(alert).not.toMatch(/plan/i);
  });

  it("tells a paying person they won't be charged again when deleting", async () => {
    for (const [scenario, says] of [
      ["table", true],
      ["grace", true],
      ["try-it", false],
      ["lapsed", false],
    ] as const) {
      const { unmount } = render(<Account api={mockApi(scenario)} go={vi.fn()} />);
      fireEvent.click(await screen.findByRole("button", { name: text.deleteStart }));
      expect(screen.queryByText(text.deletePlanStops) !== null).toBe(says);
      const refunds = screen.queryByRole("link", { name: text.deleteRefundsLink });
      expect(refunds?.getAttribute("href") ?? null).toBe(says ? "/legal/refunds#stopping" : null);
      unmount();
    }
  });

  it("deletes the account only after a warning and a second yes", async () => {
    const { api } = show("table");
    fireEvent.click(await screen.findByRole("button", { name: text.deleteStart }));
    expect(screen.getByText(text.deleteWarning[0] ?? "")).toBeTruthy();
    expect(api.calls).not.toContain("requestDelete");

    fireEvent.click(screen.getByRole("button", { name: text.deleteNext }));
    const confirm = await screen.findByRole("button", { name: text.deleteConfirm });
    expect(screen.getByText(text.deleteSure)).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("confirmDelete"))).toBe(false);

    fireEvent.click(confirm);
    expect(await screen.findByText(text.deleted)).toBeTruthy();
    expect(api.calls).toContain("confirmDelete:demo-token");
  });

  it("starts over when the last delete step fails, so a retry can work", async () => {
    const api = mockApi("table");
    api.confirmDelete = () => Promise.reject(new ApiError("server"));
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: text.deleteStart }));
    fireEvent.click(screen.getByRole("button", { name: text.deleteNext }));
    fireEvent.click(await screen.findByRole("button", { name: text.deleteConfirm }));
    expect(await screen.findByText(text.actionFailed)).toBeTruthy();
    expect(screen.getByRole("button", { name: text.deleteStart })).toBeTruthy();
    expect(screen.queryByText(text.deleteSure)).toBeNull();
  });

  it("says plainly when only the campaign's DM can do something", async () => {
    const api = mockApi("table");
    api.handoverCandidates = () => Promise.reject(new ApiError("not-allowed"));
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click((await screen.findAllByRole("button", { name: text.handOver }))[0]!);
    expect((await screen.findByRole("alert")).textContent).toBe(text.notTheDm);
  });

  it("says it's the DM's to do when handing over is refused", async () => {
    const api = mockApi("table");
    api.handover = () => Promise.reject(new ApiError("not-allowed"));
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click((await screen.findAllByRole("button", { name: text.handOver }))[0]!);
    fireEvent.click(await screen.findByRole("radio", { name: candidates[0]!.name }));
    fireEvent.click(screen.getByRole("button", { name: text.handOverConfirm }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.notTheDm);
  });

  it("says nothing stale about an install once signed in again", async () => {
    show("table", "?install=signed_out");
    await screen.findByText(text.greeting("Belleros"));
    expect(screen.queryByText(text.install["failed"] ?? "")).toBeNull();
  });

  it("doesn't blame the campaign's DM for account actions", async () => {
    const api = mockApi("table");
    api.billingPortalUrl = () => Promise.reject(new ApiError("not-allowed"));
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: text.changePlan }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.errors["not-allowed"]);
    expect(text.errors["not-allowed"]).not.toMatch(/campaign/);
  });

  it("says when there's no paid plan to change", async () => {
    const api = mockApi("table");
    api.billingPortalUrl = () => Promise.reject(new ApiError("no-paid-plan"));
    let loads = 0;
    const me = api.me.bind(api);
    api.me = () => {
      loads += 1;
      return me();
    };
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: text.changePlan }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.errors["no-paid-plan"]);
    await waitFor(() => expect(loads).toBe(2)); // the page reloads what's true now
  });

  it("says to start again when the delete confirmation ran out", async () => {
    const api = mockApi("table");
    api.confirmDelete = () => Promise.reject(new ApiError("confirm-again"));
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: text.deleteStart }));
    fireEvent.click(screen.getByRole("button", { name: text.deleteNext }));
    fireEvent.click(await screen.findByRole("button", { name: text.deleteConfirm }));
    expect(await screen.findByText(text.errors["confirm-again"] ?? "")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: text.deleteStart }));
    expect(screen.queryByText(text.errors["confirm-again"] ?? "")).toBeNull(); // old news
  });

  it("can back out of deleting", async () => {
    const { api } = show("table");
    fireEvent.click(await screen.findByRole("button", { name: text.deleteStart }));
    fireEvent.click(screen.getByRole("button", { name: text.keep }));
    expect(screen.getByRole("button", { name: text.deleteStart })).toBeTruthy();
    expect(api.calls).not.toContain("requestDelete");
  });

  it("returns to sign-in when the session ends mid-action", async () => {
    const api = mockApi("table");
    api.billingPortalUrl = () => Promise.reject(new ApiError("signed-out"));
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: text.changePlan }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.signedOutNow);
    expect(screen.getByRole("link", { name: text.signIn })).toBeTruthy();
  });

  it("signs out", async () => {
    show("table");
    fireEvent.click(await screen.findByRole("button", { name: text.signOut }));
    expect(await screen.findByRole("link", { name: text.signIn })).toBeTruthy();
  });

  it("never writes to browser storage", async () => {
    show("table");
    fireEvent.click(await screen.findByRole("button", { name: text.deleteStart }));
    fireEvent.click(screen.getByRole("button", { name: text.deleteNext }));
    await screen.findByRole("button", { name: text.deleteConfirm });
    expect(localStorage.length + sessionStorage.length).toBe(0);
  });
});

describe("hand-over offers (#614)", () => {
  const [ember, full] = incomingOffers as [(typeof incomingOffers)[0], (typeof incomingOffers)[0]];
  const offerRow = (id: string) => document.querySelector(`[data-offer="${id}"]`) as HTMLElement;

  it("shows no offers section when nobody offered you anything", async () => {
    show("table");
    await screen.findByText("The Brynwater Crossing");
    expect(screen.queryByText(text.offersHeading)).toBeNull();
  });

  it("shows an offer with who, what and where, and Accept and No thanks", async () => {
    show("offers");
    expect(await screen.findByText(text.offersHeading)).toBeTruthy();
    const row = offerRow(ember.id);
    expect(row.textContent).toContain(
      text.offerIncoming(ember.personName, ember.campaignName, ember.serverName),
    );
    expect(row.querySelectorAll("button")).toHaveLength(2);
  });

  it("Accept makes the campaign yours", async () => {
    const { api } = show("offers");
    await screen.findByText(text.offersHeading);
    fireEvent.click(within(offerRow(ember.id)).getByRole("button", { name: text.accept }));
    expect(await screen.findByText(text.accepted(ember.campaignName, ember.personName))).toBeTruthy();
    expect(api.calls).toContain(`accept:${ember.id}`);
    await waitFor(() => expect(offerRow(ember.id)).toBeNull());
    expect(document.querySelector(`[data-campaign="${ember.campaignId}"]`)).toBeTruthy();
  });

  it("each answer says the other person will be told in Discord; a refusal doesn't", async () => {
    for (const line of [
      text.accepted(ember.campaignName, ember.personName),
      text.declined(ember.personName),
      text.withdrawn(outgoingOffer.personName),
    ]) {
      expect(line).toMatch(/We'll tell .+ in Discord\.$/);
    }
    show("offers");
    await screen.findByText(text.offersHeading);
    fireEvent.click(within(offerRow(full.id)).getByRole("button", { name: text.accept }));
    await within(offerRow(full.id)).findByRole("alert");
    expect(screen.queryByText(/We'll tell/)).toBeNull();
  });

  it("Accept without a free slot says what to do, with a link to the plan", async () => {
    show("offers");
    await screen.findByText(text.offersHeading);
    fireEvent.click(within(offerRow(full.id)).getByRole("button", { name: text.accept }));
    expect((await within(offerRow(full.id)).findByRole("alert")).textContent).toBe(
      text.acceptNoSlot,
    );
    const link = within(offerRow(full.id)).getByRole("link", { name: text.seeMyPlan });
    expect(link.getAttribute("href")).toBe("#plan");
    expect(document.getElementById("plan")).toBeTruthy();
  });

  it("No thanks ends the offer", async () => {
    const { api } = show("offers");
    await screen.findByText(text.offersHeading);
    fireEvent.click(within(offerRow(ember.id)).getByRole("button", { name: text.decline }));
    expect(await screen.findByText(text.declined(ember.personName))).toBeTruthy();
    expect(api.calls).toContain(`decline:${ember.id}`);
  });

  it("the answer stays on screen when the last offer goes", async () => {
    show("offers");
    await screen.findByText(text.offersHeading);
    fireEvent.click(within(offerRow(full.id)).getByRole("button", { name: text.decline }));
    await screen.findByText(text.declined(full.personName));
    fireEvent.click(within(offerRow(ember.id)).getByRole("button", { name: text.accept }));
    expect(await screen.findByText(text.accepted(ember.campaignName, ember.personName))).toBeTruthy();
    await waitFor(() => expect(screen.queryByText(text.offersHeading)).toBeNull());
    expect(screen.getByText(text.accepted(ember.campaignName, ember.personName))).toBeTruthy();
  });

  it("Withdraw on an offer that already ended shows what's true now", async () => {
    const api = mockApi("offers");
    api.withdrawOffer = () => Promise.reject(new ApiError("offer-gone"));
    render(<Account api={api} go={vi.fn()} />);
    await screen.findByText(text.offersHeading);
    const before = api.calls.filter((c) => c === "me").length;
    const row = document.querySelector(
      `[data-campaign="${outgoingOffer.campaignId}"]`,
    ) as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: text.withdraw }));
    await waitFor(() => expect(api.calls.filter((c) => c === "me").length).toBe(before + 1));
  });

  it("an offer you made shows on its campaign with Withdraw, instead of Hand over", async () => {
    const { api } = show("offers");
    await screen.findByText(text.offersHeading);
    const row = document.querySelector(
      `[data-campaign="${outgoingOffer.campaignId}"]`,
    ) as HTMLElement;
    expect(row.textContent).toContain(
      text.offerOutgoing(outgoingOffer.personName, outgoingOffer.expiresAt),
    );
    expect(within(row).queryByRole("button", { name: text.handOver })).toBeNull();
    fireEvent.click(within(row).getByRole("button", { name: text.withdraw }));
    expect(await screen.findByText(text.withdrawn(outgoingOffer.personName))).toBeTruthy();
    expect(api.calls).toContain(`withdraw:${outgoingOffer.id}`);
  });

  it("an offer that already ended reloads the page instead of failing quietly", async () => {
    const api = mockApi("offers");
    api.acceptOffer = () => Promise.reject(new ApiError("offer-gone"));
    render(<Account api={api} go={vi.fn()} />);
    await screen.findByText(text.offersHeading);
    const before = api.calls.filter((c) => c === "me").length;
    fireEvent.click(within(offerRow(ember.id)).getByRole("button", { name: text.accept }));
    expect((await within(offerRow(ember.id)).findByRole("alert")).textContent).toBe(
      text.errors["offer-gone"],
    );
    await waitFor(() => expect(api.calls.filter((c) => c === "me").length).toBe(before + 1));
  });
});

describe("safety", () => {
  it("does an action once, however fast the taps", async () => {
    const api = mockApi("no-plan");
    let release: () => void = () => {};
    let started = 0;
    api.startTryIt = () => {
      started += 1;
      return new Promise<void>((resolve) => {
        release = resolve;
      });
    };
    render(<Account api={api} go={vi.fn()} />);
    const start = await screen.findByRole("button", { name: text.startTryIt });
    fireEvent.click(start);
    fireEvent.click(start);
    await screen.findAllByRole("button", { name: text.busy });
    fireEvent.click(screen.getAllByRole("button", { name: text.busy })[0] as HTMLElement);
    release();
    await waitFor(() => expect(screen.queryAllByRole("button", { name: text.busy })).toHaveLength(0));
    expect(started).toBe(1);
  });

  it("refuses to send the browser anywhere but Discord or the payment company", async () => {
    const api = mockApi("table");
    api.billingPortalUrl = () => Promise.resolve("https://evil.example/login");
    const go = vi.fn<(url: string) => void>();
    render(<Account api={api} go={go} />);
    fireEvent.click(await screen.findByRole("button", { name: text.changePlan }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.actionFailed);
    expect(go).not.toHaveBeenCalled();
  });

  it("shows a failure inside the section that failed", async () => {
    const api = mockApi("table");
    api.linkServer = () => Promise.reject(new ApiError("server"));
    render(<Account api={api} go={vi.fn()} />);
    const row = await waitFor(() => {
      const found = document.querySelector('[data-server="200000000000000003"]');
      if (!found) throw new Error("not yet");
      return found;
    });
    fireEvent.click(row.querySelector("button") as HTMLButtonElement);
    const alert = await screen.findByRole("alert");
    expect(alert.closest("section")?.getAttribute("aria-labelledby")).toBe("servers-heading");
  });

  it("keeps the page when only the refresh after a change fails", async () => {
    const api = mockApi("no-plan");
    const realMe = api.me.bind(api);
    let calls = 0;
    api.me = () => (++calls === 1 ? realMe() : Promise.reject(new ApiError("network")));
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: text.startTryIt }));
    await waitFor(() => expect(calls).toBe(2));
    expect(screen.queryByText(text.down)).toBeNull();
    expect(screen.getByText("Hi, Belleros.")).toBeTruthy();
  });

  it("offers Fix my payment in grace even without a date", async () => {
    const api = mockApi("grace");
    const realMe = api.me.bind(api);
    api.me = async () => {
      const me = await realMe();
      return me && me.plan ? { ...me, plan: { ...me.plan, graceEndsOn: null } } : me;
    };
    render(<Account api={api} go={vi.fn()} />);
    expect(await screen.findByText(text.grace(null))).toBeTruthy();
    expect(screen.getByRole("button", { name: text.fixPayment })).toBeTruthy();
  });

  it("a failed first payment says no plan started and offers the plans again (#922)", async () => {
    const api = mockApi("no-plan");
    const realMe = api.me.bind(api);
    api.me = async () => {
      const me = await realMe();
      return me ? { ...me, firstPaymentFailed: true } : me;
    };
    render(<Account api={api} go={vi.fn()} />);
    expect(await screen.findByText(text.firstPaymentFailed)).toBeTruthy();
    expect(screen.queryByText(text.grace(null))).toBeNull(); // no "fix it in 7 days"
    expect(screen.getByRole("button", { name: text.choose("table") })).toBeTruthy();
  });

  it("says nothing about a failed first payment when none failed", async () => {
    show("no-plan");
    await screen.findByText(text.noPlan);
    expect(screen.queryByText(text.firstPaymentFailed)).toBeNull();
  });

  it("lists where DMbot may record you, with one warning and one confirmation to stop (#1151)", async () => {
    const { api } = show("table");
    const first = await screen.findByText(text.recordingLine("Thursday Table", "2026-09-20"));
    expect(first).toBeTruthy();
    expect(screen.getByText(text.recordingLine("Gorrak's Hall", "2026-08-02"))).toBeTruthy();
    // Nothing is stopped by the first press: it shows the warning and a way back.
    const row = first.closest("li") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: text.stopRecording }));
    expect(within(row).getByText(text.stopWarning("Thursday Table"))).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("stop-recording"))).toBe(false);
    fireEvent.click(within(row).getByRole("button", { name: text.stopKeep }));
    expect(within(row).queryByText(text.stopWarning("Thursday Table"))).toBeNull();
    expect(api.calls.some((c) => c.startsWith("stop-recording"))).toBe(false);
    // The one confirmation stops it, and the page says so.
    fireEvent.click(within(row).getByRole("button", { name: text.stopRecording }));
    fireEvent.click(within(row).getByRole("button", { name: text.stopYes }));
    expect(await screen.findByText(text.stoppedRecording("Thursday Table"))).toBeTruthy();
    expect(api.calls).toContain("stop-recording:srv-thursday");
    await waitFor(() =>
      expect(screen.queryByText(text.recordingLine("Thursday Table", "2026-09-20"))).toBeNull(),
    );
    // The other server is untouched.
    expect(screen.getByText(text.recordingLine("Gorrak's Hall", "2026-08-02"))).toBeTruthy();
  });

  it("says a yes under older wording is not recording anyone (#1151)", async () => {
    const api = mockApi("table");
    const realMe = api.me.bind(api);
    api.me = async () => {
      const me = await realMe();
      return me
        ? {
            ...me,
            recording: [
              { serverId: "srv-x", serverName: "Old Hall", agreedOn: "2026-01-01", on: false },
            ],
          }
        : me;
    };
    render(<Account api={api} go={vi.fn()} />);
    expect(await screen.findByText(text.recordingOld("Old Hall"))).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.stopRecording })).toBeNull();
  });

  it("shows no recording card to someone who never agreed, or from an older API (#1151)", async () => {
    const api = mockApi("table");
    const realMe = api.me.bind(api);
    api.me = async () => {
      const me = await realMe();
      if (!me) return me;
      const { recording: _gone, ...older } = me;
      return older;
    };
    render(<Account api={api} go={vi.fn()} />);
    await screen.findByRole("heading", { name: text.exportHeading });
    expect(screen.queryByText(text.recordingHeading)).toBeNull();
  });

  it("downloads the person's data from the page, as a file (#1150)", async () => {
    const saved: string[] = [];
    URL.createObjectURL = vi.fn(() => "blob:pretend");
    URL.revokeObjectURL = vi.fn((a: string) => void saved.push(a));
    const { api } = show("table");
    fireEvent.click(await screen.findByRole("button", { name: text.exportButton }));
    await waitFor(() => expect(api.calls).toContain("export"));
    await waitFor(() => expect(saved).toEqual(["blob:pretend"]), { timeout: 3000 });
    expect(screen.getByText(text.exportWhat)).toBeTruthy();
  });

  it("says to wait a minute when the API refuses a second download (#1150)", async () => {
    const api = mockApi("table");
    api.downloadExport = async () => {
      throw new ApiError("slow-down");
    };
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: text.exportButton }));
    expect(await screen.findByText(text.exportTooSoon)).toBeTruthy();
  });

  it("goes back to signed out when the download finds the sign-in has ended (#1150)", async () => {
    const api = mockApi("table");
    api.downloadExport = async () => {
      throw new ApiError("signed-out");
    };
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: text.exportButton }));
    expect(await screen.findByRole("link", { name: text.signIn })).toBeTruthy();
  });

  it("does not offer Download my data to someone signed out", async () => {
    show("signed-out");
    await screen.findByRole("link", { name: text.signIn });
    expect(screen.queryByRole("button", { name: text.exportButton })).toBeNull();
  });

  it("lists the person's own characters, read-only, with a link only when there is one (#1164)", async () => {
    show("table");
    const testa = (await screen.findByText("Testa")).closest("li") as HTMLElement;
    expect(within(testa).getByText("Bard 2")).toBeTruthy();
    expect(within(testa).getByText(`${text.characterTyped} ${text.characterRead("2026-10-02T19:30:00Z")}`)).toBeTruthy();
    expect(within(testa).getByText(text.characterWhere("Thursday Table", "The Brynwater Crossing"))).toBeTruthy();
    for (const fact of [text.characterLevel(2), text.characterHp(14), text.characterAc(13)]) {
      expect(within(testa).getByText(fact)).toBeTruthy();
    }
    expect(within(testa).queryByRole("link", { name: text.characterOpen })).toBeNull();
    expect(screen.getAllByText(text.characterChange)).toHaveLength(1); // once, under the heading
    const brannoch = screen.getByText("Brannoch").closest("li") as HTMLElement;
    expect(within(brannoch).getByText("Ranger 3 / Druid 3")).toBeTruthy();
    const link = within(brannoch).getByRole("link", { name: text.characterOpen });
    expect(link.getAttribute("href")).toBe("https://sheets.example/characters/12345678");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("says what to do when there are no characters, and shows a link-only sheet (#1164)", async () => {
    const api = mockApi("table");
    api.characters = async () => [];
    render(<Account api={api} go={vi.fn()} />);
    expect(await screen.findByText(text.charactersNone)).toBeTruthy();
    cleanup();
    const linked = mockApi("table");
    linked.characters = async () => [
      {
        serverName: "Thursday Table",
        campaign: null,
        name: null,
        classLine: null,
        level: null,
        maxHp: null,
        ac: null,
        sheetLink: "https://sheets.example/characters/1",
        typedIn: false,
        readAt: null,
      },
    ];
    render(<Account api={linked} go={vi.fn()} />);
    expect(await screen.findByText(text.characterUnread)).toBeTruthy();
    expect(screen.getByText(text.characterUnreadNote)).toBeTruthy();
    expect(screen.getByRole("link", { name: text.characterOpen })).toBeTruthy();
  });

  it("shows no characters card from an older API or when loading fails (#1164)", async () => {
    const api = mockApi("table");
    api.characters = async () => {
      throw new ApiError("server");
    };
    render(<Account api={api} go={vi.fn()} />);
    await screen.findByRole("heading", { name: text.exportHeading });
    expect(screen.queryByText(text.charactersHeading)).toBeNull();
  });

  it("Sign out everywhere calls the API and then shows signed out (#1163)", async () => {
    const { api } = show("table");
    expect(await screen.findByText(text.signOutEverywhereNote)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: text.signOutEverywhere }));
    await waitFor(() => expect(api.calls).toContain("signOutEverywhere"));
    expect(await screen.findByRole("link", { name: text.signIn })).toBeTruthy();
  });

  it("Sign out everywhere says to wait a minute on a 429 (#1163)", async () => {
    const api = mockApi("table");
    api.signOutEverywhere = async () => {
      throw new ApiError("slow-down");
    };
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: text.signOutEverywhere }));
    expect(await screen.findByText(text.signOutEverywhereWait)).toBeTruthy();
  });

  it("signed out: Start Try It is the main button, with the plans under it", async () => {
    show("signed-out");
    const links = await screen.findAllByRole("link");
    const labels = links.map((l) => l.textContent);
    expect(labels.indexOf(text.startTryItFree)).toBeLessThan(labels.indexOf("Choose Table"));
    expect(screen.getByText(text.startTryItSignIn)).toBeTruthy();
    expect(screen.getByRole("link", { name: text.startTryItFree }).className).toBe("button");
  });

  it("puts Keep first on the last delete step", async () => {
    show("table");
    fireEvent.click(await screen.findByRole("button", { name: text.deleteStart }));
    fireEvent.click(screen.getByRole("button", { name: text.deleteNext }));
    await screen.findByRole("button", { name: text.deleteConfirm });
    const section = document.querySelector('[aria-labelledby="delete-heading"]') as HTMLElement;
    const order = [...section.querySelectorAll("button")].map((b) => b.textContent);
    expect(order).toEqual([text.keep, text.deleteConfirm]);
  });
});

describe("redirects", () => {
  it.each([
    ["https://discord.com/oauth2/authorize?client_id=1", true],
    ["https://checkout.stripe.com/c/pay/cs_test_abc", true],
    ["https://billing.stripe.com/p/session/test_abc", true],
    ["https://checkout.stripe.com.evil.example/", false],
    ["https://dashboard.stripe.com/", false],
    ["#demo-billing", true],
    ["http://discord.com/", false],
    ["https://discord.com.evil.example/", false],
    ["https://evildiscord.com/", false],
    ["https://user:pw@discord.com/", false],
    ["javascript:alert(1)", false],
    ["/account", false],
  ])("%s → %s", (url, ok) => {
    expect(isSafeRedirect(url)).toBe(ok);
  });

  it("never follows a same-page link outside a pretend-API build", () => {
    expect(isSafeRedirect("#demo-billing", false)).toBe(false);
  });
});

describe("extra hours (#1089)", () => {
  it("shows the button, with the price and the this-month-only line, over a paid plan", async () => {
    show("table");
    const button = await screen.findByRole("button", { name: text.addHours });
    expect(text.addHours).toBe("Add 10 hours ($4.99)");
    expect(button).toBeTruthy();
    expect(screen.getByText(text.addHoursNote)).toBeTruthy();
    expect(text.addHoursNote).toMatch(/this month only/);
  });

  it("sends the person to the payment company's page when pressed", async () => {
    const { api, go } = show("table");
    fireEvent.click(await screen.findByRole("button", { name: text.addHours }));
    await waitFor(() => expect(go).toHaveBeenCalledWith("#demo-extra-hours"));
    expect(api.calls).toContain("extra-hours");
  });

  it("shows it in the payment grace, where the plan still works", async () => {
    show("grace");
    expect(await screen.findByRole("button", { name: text.addHours })).toBeTruthy();
  });

  it.each(["try-it", "lapsed", "no-plan"] as const)("hides it on %s", async (scenario) => {
    show(scenario);
    await waitFor(() => expect(screen.queryByText(text.loading)).toBeNull());
    expect(screen.queryByRole("button", { name: text.addHours })).toBeNull();
  });

  it("refreshes the page when the plan changed under it", async () => {
    const api = mockApi("table");
    api.extraHoursUrl = () => Promise.reject(new ApiError("no-paid-plan"));
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: text.addHours }));
    await waitFor(() => expect(api.calls.filter((c) => c === "me")).toHaveLength(2));
  });
});

describe("hours words", () => {
  it("tells a Try It user and a paid user different next steps at the cap", () => {
    expect(hoursLeftLine(8, 8, null, "try-it")).toBe("Pick a plan below to keep playing.");
    expect(hoursLeftLine(18, 18, "2026-10-14", "table")).toBe(
      "Your hours start again on Oct 14. Need more now? Tap Add 10 hours below.",
    );
  });

  it.each([
    [0, 18, "None of your 18 hours used yet"],
    [0.3, 18, "Less than 1 of 18 hours used"],
    [11.2, 18, "About 11 of 18 hours used"],
    [17.8, 18, "About 17 of 18 hours used"],
    [18, 18, "All 18 hours used"],
    [19.5, 18, "All 18 hours used"],
  ])("%f of %i is %s", (used, cap, words) => {
    expect(hoursUsedLine(used, cap)).toBe(words);
  });
});

describe("the HTTP client", () => {
  type Call = { url: string; init: RequestInit };

  function fakeFetch(status: number, body?: unknown) {
    const calls: Call[] = [];
    const fetcher = (async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return new Response(body === undefined ? null : JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;
    return { calls, fetcher };
  }

  it("sends the cookie and the request header, never a token", async () => {
    const { calls, fetcher } = fakeFetch(200, { url: "https://pay.example/portal" });
    const api = httpApi("https://api.example/", fetcher);
    expect(await api.billingPortalUrl()).toBe("https://pay.example/portal");
    const [call] = calls;
    expect(call?.url).toBe("https://api.example/billing/portal");
    expect(call?.init.credentials).toBe("include");
    expect((call?.init.headers as Record<string, string>)["X-DMbot-Request"]).toBe("1");
    expect(call?.url).not.toMatch(/token|code=/i);
  });

  it("fetches the export as a file, and names a refusal in words (#1150)", async () => {
    const ok = fakeFetch(200, { about: "x" });
    const file = await httpApi("https://api.example", ok.fetcher).downloadExport();
    expect(JSON.parse(await file.text())).toEqual({ about: "x" });
    expect(ok.calls[0]?.url).toBe("https://api.example/me/export");
    expect(ok.calls[0]?.init.credentials).toBe("include");
    const soon = fakeFetch(429, { error: "slow_down" });
    await expect(httpApi("https://api.example", soon.fetcher).downloadExport()).rejects.toMatchObject({
      kind: "slow-down",
    });
    const out = fakeFetch(401);
    await expect(httpApi("https://api.example", out.fetcher).downloadExport()).rejects.toMatchObject({
      kind: "signed-out",
    });
  });

  it("treats 401 on /me as signed out", async () => {
    const { fetcher } = fakeFetch(401);
    expect(await httpApi("https://api.example", fetcher).me()).toBeNull();
  });

  it("maps error codes to plain kinds", async () => {
    const api = httpApi("https://api.example", fakeFetch(409, { error: "no_free_slot" }).fetcher);
    await expect(api.acceptOffer("o1")).rejects.toMatchObject({ kind: "no-free-slot" });
    const expired = httpApi("https://api.example", fakeFetch(403, { error: "confirm_again" }).fetcher);
    await expect(expired.confirmDelete("t")).rejects.toMatchObject({ kind: "confirm-again" });
    const gone = httpApi("https://api.example", fakeFetch(409, { error: "offer_gone" }).fetcher);
    await expect(gone.acceptOffer("o1")).rejects.toMatchObject({ kind: "offer-gone" });
    const noPlan = httpApi("https://api.example", fakeFetch(409, { error: "no_paid_plan" }).fetcher);
    await expect(noPlan.billingPortalUrl()).rejects.toMatchObject({ kind: "no-paid-plan" });
    const down = httpApi("https://api.example", (() =>
      Promise.reject(new TypeError("offline"))) as unknown as typeof fetch);
    await expect(down.me()).rejects.toMatchObject({ kind: "network" });
  });

  it("answers a hand-over offer at its own address, nothing in the body", async () => {
    const { calls, fetcher } = fakeFetch(204);
    const api = httpApi("https://api.example", fetcher);
    await api.acceptOffer("o/1");
    await api.declineOffer("o1");
    await api.withdrawOffer("o1");
    expect(calls.map((c) => c.url)).toEqual([
      "https://api.example/offers/o%2F1/accept",
      "https://api.example/offers/o1/decline",
      "https://api.example/offers/o1/withdraw",
    ]);
    expect(calls.every((c) => c.init.method === "POST" && c.init.body === undefined)).toBe(true);
  });

  it("sends the delete token in the body, not the URL", async () => {
    const { calls, fetcher } = fakeFetch(204);
    await httpApi("https://api.example", fetcher).confirmDelete("secret-token");
    expect(calls[0]?.url).toBe("https://api.example/account/delete/confirm");
    expect(calls[0]?.init.body).toBe(JSON.stringify({ confirm_token: "secret-token" }));
  });

  it("treats a 403 without a code as not allowed, and a broken 500 as a server error", async () => {
    const forbidden = httpApi("https://api.example", fakeFetch(403).fetcher);
    await expect(forbidden.signOut()).rejects.toMatchObject({ kind: "not-allowed" });
    const broken = httpApi("https://api.example", (async () =>
      new Response("<html>oops</html>", { status: 500 })) as unknown as typeof fetch);
    await expect(broken.billingPortalUrl()).rejects.toMatchObject({ kind: "server" });
  });

  it("escapes ids in paths", async () => {
    const { calls, fetcher } = fakeFetch(204);
    await httpApi("https://api.example", fetcher).acceptOffer("a/../b");
    expect(calls[0]?.url).toBe("https://api.example/offers/a%2F..%2Fb/accept");
  });

  it("can't make an offer from the site yet, so an owner sees no Hand over (#625)", async () => {
    expect(httpApi("https://api.example").handover).toBeUndefined();
    expect(httpApi("https://api.example").handoverCandidates).toBeUndefined();
    const api = mockApi("table");
    delete api.handover;
    delete api.handoverCandidates;
    render(<Account api={api} go={vi.fn()} />);
    await screen.findByText("The Brynwater Crossing");
    expect(screen.queryByRole("button", { name: text.handOver })).toBeNull();
  });
});

describe("free access (#806)", () => {
  // A scenario's data with free access added, as the API sends it after #804.
  function showFree(scenario: Scenario, access: Access | null) {
    const api = mockApi(scenario);
    const base = scenarioMe(scenario);
    api.me = vi.fn(async (): Promise<Me | null> => (base && access ? { ...base, access } : base));
    const go = vi.fn<(url: string) => void>();
    render(<Account api={api} go={go} search="" />);
    return { api, go };
  }

  it("says what to do next, and what happens when a dated grant ends", async () => {
    showFree("lapsed", { kind: "free", endsOn: "2026-12-31" });
    const next = await screen.findByText(/type \/dmbot start/);
    expect(next.textContent).toBe(`${text.freeNext} ${text.freeEnds("2026-12-31")}`);
    // A lapsed paid plan under free access: no "plan has stopped", no plans to pick.
    expect(screen.queryByText(text.lapsed)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Choose / })).toBeNull();
  });

  it("a plan in payment grace shows Stop paying, not Fix my payment", async () => {
    showFree("grace", { kind: "free", stillPaying: true, paidPlan: "Two Tables" });
    expect(await screen.findByText(text.stillPaying("Two Tables", "2026-10-14"))).toBeTruthy();
    expect(screen.getByRole("button", { name: text.stopPaying })).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.fixPayment })).toBeNull();
  });

  it("still paying with no plan name or date still reads right", async () => {
    showFree("no-plan", { kind: "free", stillPaying: true });
    expect(await screen.findByText(text.stillPaying(null, null))).toBeTruthy();
    expect(text.stillPaying(null, null)).toMatch(/^Your plan is still being paid for\./);
  });

  it("a dated grant while still paying adds no promise about what comes after", async () => {
    showFree("table", { kind: "free", endsOn: "2026-12-31", stillPaying: true, paidPlan: "Table" });
    await screen.findByText(text.stillPaying("Table", "2026-10-14"));
    expect(screen.queryByText(/pick a plan to keep playing/)).toBeNull();
    expect(screen.queryByText(/back on/)).toBeNull();
  });

  it("Accept without a free slot gives the free-access words and no See my plan", async () => {
    showFree("offers", { kind: "free" });
    await screen.findByText(text.offersHeading);
    const row = document.querySelector('[data-offer="offer-full"]') as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: text.accept }));
    expect((await within(row).findByRole("alert")).textContent).toBe(text.acceptNoSlotFree);
    expect(within(row).queryByRole("link", { name: text.seeMyPlan })).toBeNull();
  });

  it("the delete warning says free access goes too", async () => {
    showFree("table", { kind: "free" });
    fireEvent.click(await screen.findByRole("button", { name: text.deleteStart }));
    expect(screen.getByText(text.deleteWarningFree)).toBeTruthy();
    expect(screen.queryByText(text.deleteWarning[0] ?? "")).toBeNull();
  });

  it("the delete warning also says payments stop when a paid plan is still paid for", async () => {
    showFree("table", { kind: "free", stillPaying: true, paidPlan: "Table" });
    fireEvent.click(await screen.findByRole("button", { name: text.deleteStart }));
    expect(screen.getByText(text.deleteWarningFreePaying)).toBeTruthy();
    expect(screen.queryByText(text.deleteWarningFree)).toBeNull();
  });

  // The page was loaded before free access was given: the first /me has none, the next has it.
  function showBecomingFree(action: (api: AccountApi) => void) {
    const api = mockApi("no-plan");
    const base = scenarioMe("no-plan");
    let calls = 0;
    api.me = vi.fn(async (): Promise<Me | null> => {
      calls += 1;
      return base && calls > 1 ? { ...base, access: { kind: "free" } } : base;
    });
    action(api);
    render(<Account api={api} go={vi.fn()} search="" />);
    return api;
  }

  it("an out-of-date Choose refreshes into the free panel and says so", async () => {
    const api = showBecomingFree((a) => {
      a.checkoutUrl = vi.fn(async () => {
        throw new ApiError("free-access");
      });
    });
    fireEvent.click(await screen.findByRole("button", { name: "Choose Table" }));
    expect(await screen.findByText(text.freeAccess)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Choose Table" })).toBeNull();
    expect(api.me).toHaveBeenCalledTimes(2);
  });

  it("an out-of-date Start Try It refreshes into the free panel too", async () => {
    showBecomingFree((a) => {
      a.startTryIt = vi.fn(async () => {
        throw new ApiError("free-access");
      });
    });
    fireEvent.click(await screen.findByRole("button", { name: text.startTryIt }));
    expect(await screen.findByText(text.freeAccess)).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.startTryIt })).toBeNull();
  });

  // #824: follow-ups from the first free-access page.
  it("tells a still-paying person to cancel on the payment page", async () => {
    showFree("table", { kind: "free", stillPaying: true, paidPlan: "Table" });
    const line = await screen.findByText(/Tap Stop paying/);
    expect(line.textContent).toContain("then cancel on the payment page");
  });

  it("a cancelled plan says when it ends and offers no Stop paying", async () => {
    showFree("table", { kind: "free", paidPlan: "Table", paidPlanEndsOn: "2026-10-14" });
    expect(await screen.findByText(text.planEnds("Table", "2026-10-14"))).toBeTruthy();
    expect(text.planEnds("Table", "2026-10-14")).toBe(
      "Your Table plan ends on Oct 14. You won't be charged again.",
    );
    expect(screen.queryByRole("button", { name: text.stopPaying })).toBeNull();
  });

  it("a capped grant shows its hours line and bar, no limits shows none", async () => {
    showFree("no-plan", {
      kind: "free",
      hours: { used: 11.2, cap: 87, renewsOn: "2026-11-11" },
    });
    expect(await screen.findByText("About 11 of 87 hours used")).toBeTruthy();
    const bar = screen.getByLabelText(text.hoursBarLabel) as HTMLProgressElement;
    expect([bar.value, bar.max]).toEqual([11.2, 87]);
    expect(screen.getByText(text.freeHoursLeft(11.2, 87, "2026-11-11"))).toBeTruthy();
  });

  it("a capped grant that is used up says when the hours come back, or where to ask", () => {
    expect(text.freeHoursLeft(87, 87, "2026-11-11")).toBe(
      "You're out of hours until Nov 11. Then they start again.",
    );
    expect(text.freeHoursLeft(87, 87, null)).toMatch(/Write to us/);
    expect(text.freeHoursLeft(1, 87, null)).toBe("");
  });

  it("no limits: no hours line, and campaigns say 'You run it'", async () => {
    showFree("table", { kind: "free", unlimited: true });
    await screen.findByText(text.freeAccess);
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect((await screen.findAllByText("You run it")).length).toBeGreaterThan(0);
  });

  it("a capped grant says 'You run it' too", async () => {
    showFree("table", { kind: "free", hours: { used: 1, cap: 87, renewsOn: null } });
    expect((await screen.findAllByText("You run it")).length).toBeGreaterThan(0);
  });

  it("an offer to a no-limits person doesn't talk about slots or hours", async () => {
    showFree("offers", { kind: "free", unlimited: true });
    const lines = await screen.findAllByText(/wants to hand you/);
    for (const line of lines) {
      expect(line.textContent).not.toMatch(/slot|hours/);
      expect(line.textContent).toContain("it becomes yours");
      expect(line.textContent).toContain("stays on as a DM");
    }
  });

  it("an offer to a capped grant says slots and hours, not a plan", async () => {
    showFree("offers", { kind: "free", hours: { used: 1, cap: 87, renewsOn: null } });
    const lines = await screen.findAllByText(/wants to hand you/);
    for (const line of lines) {
      expect(line.textContent).toContain("one of your campaign slots and your hours");
      expect(line.textContent).not.toContain("plan");
    }
  });

  it("an offer to a person with limits keeps the slot and hours wording", async () => {
    show("offers");
    const lines = await screen.findAllByText(/wants to hand you/);
    for (const line of lines) {
      expect(line.textContent).toMatch(/campaign slots and your plan's hours/);
    }
  });

  it("a paid plan that was cancelled says when it ends", async () => {
    const api = mockApi("table");
    const base = scenarioMe("table");
    const plan = base?.plan;
    if (!base || !plan) throw new Error("the table scenario has a plan");
    api.me = vi.fn(async (): Promise<Me | null> => ({
      ...base,
      plan: { ...plan, endsOn: "2026-10-14" },
    }));
    render(<Account api={api} go={vi.fn()} search="" />);
    expect(await screen.findByText(text.planEndsLine("Table", "2026-10-14"))).toBeTruthy();
    // The hours don't "start again" on a plan that is ending, and the date isn't said twice.
    expect(screen.queryByText(/Your hours start again/)).toBeNull();
    expect(screen.queryByText(text.planEnds("Table", "2026-10-14"))).toBeNull();
  });
});

describe("the plan line says what the plan is doing (#2165)", () => {
  function withPlan(change: Partial<MyPlan>, status?: MyPlan["status"]) {
    const api = mockApi("table");
    const base = scenarioMe("table");
    const plan = base?.plan;
    if (!base || !plan) throw new Error("the table scenario has a plan");
    const me: Me = { ...base, plan: { ...plan, ...change, ...(status ? { status } : {}) } };
    api.me = vi.fn(async (): Promise<Me | null> => me);
    return api;
  }
  const open = (api: ReturnType<typeof mockApi>) =>
    render(<Account api={api} go={vi.fn()} search="" />);

  it("active: renews on the date", async () => {
    open(withPlan({ state: "active", renewsOn: "2026-11-18" }));
    expect(await screen.findByText("Table (renews on Nov 18)")).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.renew })).toBeNull();
  });

  it("cancelled but still running: ends on the date, with Renew beside Change plan", async () => {
    open(withPlan({ state: "ending", endsOn: "2026-11-18", renewsOn: "2026-11-18" }));
    expect(await screen.findByText("Table (ends on Nov 18)")).toBeTruthy();
    expect(screen.getByRole("button", { name: text.changePlan })).toBeTruthy();
    expect(screen.getByRole("button", { name: text.renew })).toBeTruthy();
  });

  it("another plan starts at the end of the period", async () => {
    open(withPlan({ state: "changing", nextPlan: "guild", nextPlanOn: "2026-11-18" }));
    expect(await screen.findByText("Table (new plan: Guild starts on Nov 18)")).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.renew })).toBeNull();
  });

  it("expired: says when it ended and links to the plans", async () => {
    open(withPlan({ state: "expired", endedOn: "2026-10-01" }, "lapsed"));
    expect(await screen.findByText("Your Table plan ended on Oct 1.")).toBeTruthy();
    // The plans to pick from are right there, with the link to the page that has them all.
    const link = screen.getByRole("link", { name: text.seePrices }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/pricing");
    expect(screen.getByRole("button", { name: text.choose("table") })).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.renew })).toBeNull();
  });

  it("an API without a state still shows a cancel date, but offers no Renew it can't do", async () => {
    open(withPlan({ endsOn: "2026-11-18" }));
    expect(await screen.findByText("Table (ends on Nov 18)")).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.renew })).toBeNull();
  });

  it("a payment still being fixed keeps the plain name", async () => {
    open(withPlan({ state: "active", graceEndsOn: "2026-10-20" }, "grace"));
    expect(await screen.findByText("Table")).toBeTruthy();
  });

  it("Renew asks once, then undoes the cancellation and shows the plan renewing", async () => {
    const api = withPlan({ state: "ending", endsOn: "2026-11-18", renewsOn: "2026-11-18" });
    api.renewPlan = vi.fn(async () => undefined);
    open(api);
    fireEvent.click(await screen.findByRole("button", { name: text.renew }));
    expect(screen.getByText(text.renewAsk("Table", "2026-11-18"))).toBeTruthy();
    expect(api.renewPlan).not.toHaveBeenCalled(); // asked, not done
    fireEvent.click(screen.getByRole("button", { name: text.renewYes }));
    expect(await screen.findByText(text.renewed("Table", "2026-11-18"))).toBeTruthy();
    expect(api.renewPlan).toHaveBeenCalledTimes(1);
  });

  it("Not now leaves everything as it was", async () => {
    const api = withPlan({ state: "ending", endsOn: "2026-11-18" });
    api.renewPlan = vi.fn(async () => undefined);
    open(api);
    fireEvent.click(await screen.findByRole("button", { name: text.renew }));
    fireEvent.click(screen.getByRole("button", { name: text.renewNo }));
    expect(screen.getByRole("button", { name: text.renew })).toBeTruthy();
    expect(api.renewPlan).not.toHaveBeenCalled();
  });

  it("a plan that is no longer ending is refreshed, in words", async () => {
    const api = withPlan({ state: "ending", endsOn: "2026-11-18" });
    api.renewPlan = vi.fn(async () => {
      throw new ApiError("not-ending");
    });
    open(api);
    fireEvent.click(await screen.findByRole("button", { name: text.renew }));
    fireEvent.click(screen.getByRole("button", { name: text.renewYes }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.errors["not-ending"]);
  });

  it("Renew moves focus to the question, and Not now gives it back to Renew", async () => {
    const api = withPlan({ state: "ending", endsOn: "2026-11-18", renewsOn: "2026-11-18" });
    open(api);
    fireEvent.click(await screen.findByRole("button", { name: text.renew }));
    const ask = screen.getByText(text.renewAsk("Table", "2026-11-18"));
    await waitFor(() => expect(document.activeElement).toBe(ask));
    fireEvent.click(screen.getByRole("button", { name: text.renewNo }));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: text.renew })),
    );
  });

  it("Change plan asks for the plan list; Fix my payment and Stop paying do not", async () => {
    const api = withPlan({ state: "active" });
    const portal = vi.fn(async (_flow?: "plans") => "#demo-billing");
    api.billingPortalUrl = portal;
    open(api);
    fireEvent.click(await screen.findByRole("button", { name: text.changePlan }));
    await waitFor(() => expect(portal).toHaveBeenCalledWith("plans"));
    expect(screen.getByText(text.changePlanNote)).toBeTruthy();
  });

  it("the Change plan call sends the flow only when it is asked for", async () => {
    const bodies: string[] = [];
    const reply = httpApi("https://api.example.test", (async (_url: string, init: RequestInit) => {
      bodies.push(String(init.body ?? ""));
      return new Response(JSON.stringify({ url: "https://billing.stripe.com/p/x" }), { status: 200 });
    }) as unknown as typeof fetch);
    await reply.billingPortalUrl("plans");
    await reply.billingPortalUrl();
    expect(bodies).toEqual([JSON.stringify({ flow: "plans" }), ""]);
  });

  it("a company that won't renew from here sends the person to its page, in words", async () => {
    const api = withPlan({ state: "ending", endsOn: "2026-11-18" });
    api.renewPlan = vi.fn(async () => {
      throw new ApiError("use-billing-page");
    });
    open(api);
    fireEvent.click(await screen.findByRole("button", { name: text.renew }));
    fireEvent.click(screen.getByRole("button", { name: text.renewYes }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.errors["use-billing-page"]);
  });

  it("Renew is one POST on the wire and a not_ending answer is read as such", async () => {
    const seen: { url: string; method: string }[] = [];
    const reply = (status: number, body: unknown) =>
      httpApi("https://api.example.test", (async (url: string, init: RequestInit) => {
        seen.push({ url, method: String(init.method) });
        return new Response(JSON.stringify(body), { status });
      }) as unknown as typeof fetch);
    await reply(200, { ok: true }).renewPlan!();
    expect(seen).toEqual([{ url: "https://api.example.test/billing/renew", method: "POST" }]);
    await expect(reply(409, { error: "not_ending" }).renewPlan!()).rejects.toMatchObject({
      kind: "not-ending",
    });
  });
});

describe("sign in by email, DMbot ID, experience points and move code (#1231)", () => {
  it("the email field has a grey hint and keeps its real label (#1778)", async () => {
    render(<Account api={mockApi("signed-out")} go={vi.fn()} />);
    const field = (await screen.findByLabelText(text.emailLabel)) as HTMLInputElement;
    expect(field.placeholder).toBe("your@email.here");
  });

  it("signed out: an email form that always answers in the same words", async () => {
    const api = mockApi("signed-out");
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.input(await screen.findByLabelText(text.emailLabel), {
      target: { value: "  belleros@example.com " },
    });
    fireEvent.click(screen.getByRole("button", { name: text.emailButton }));
    expect(await screen.findByText(text.emailAnswer)).toBeTruthy();
    expect(api.calls).toContain("email-request:belleros@example.com"); // trimmed
  });

  it("signed out: a blank email is asked for, and nothing is sent", async () => {
    const api = mockApi("signed-out");
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: text.emailButton }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.emailMissing);
    expect(api.calls.some((c) => c.startsWith("email-request"))).toBe(false);
  });

  it("signed out: too many tries says to wait", async () => {
    const api = mockApi("signed-out");
    api.emailSignInRequest = async () => {
      throw new ApiError("slow-down");
    };
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.input(await screen.findByLabelText(text.emailLabel), {
      target: { value: "a@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: text.emailButton }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.emailWait);
  });

  it("no email form from an API older than this", async () => {
    const api = mockApi("signed-out");
    delete api.emailSignInRequest;
    render(<Account api={api} go={vi.fn()} />);
    await screen.findByRole("link", { name: text.signIn });
    expect(screen.queryByLabelText(text.emailLabel)).toBeNull();
  });

  it("an emailed link in the address signs in once and is taken out of the address bar", async () => {
    window.history.replaceState(null, "", "/account#email-link=good-link");
    const api = mockApi("signed-out");
    render(<Account api={api} go={vi.fn()} hash="#email-link=good-link" />);
    expect(await screen.findByText(text.greeting(scenarioMe("table")?.user.name ?? ""))).toBeTruthy();
    expect(api.calls).toContain("email-use:good-link");
    expect(window.location.hash).toBe(""); // not left to a reload or a screenshot
  });

  it("a wrong or used link says so, stays signed out, and offers a new one", async () => {
    const api = mockApi("signed-out");
    render(<Account api={api} go={vi.fn()} hash="#email-link=old-link" />);
    expect((await screen.findByRole("alert")).textContent).toBe(text.emailLinkBad);
    expect(screen.getByRole("button", { name: text.emailButton })).toBeTruthy();
  });

  it("too many tries on a link says to wait, not that the link was used", async () => {
    const api = mockApi("signed-out");
    api.emailSignInUse = async () => {
      throw new ApiError("slow-down");
    };
    render(<Account api={api} go={vi.fn()} hash="#email-link=good-link" />);
    expect((await screen.findByRole("alert")).textContent).toBe(text.emailLinkWait);
  });

  it("anything else in the address is ignored", async () => {
    const api = mockApi("signed-out");
    render(<Account api={api} go={vi.fn()} hash="#something-else" />);
    await screen.findByRole("link", { name: text.signIn });
    expect(api.calls.some((c) => c.startsWith("email-use"))).toBe(false);
  });

  it("shows the DMbot ID and the experience points", async () => {
    show("table");
    const section = (await screen.findByRole("heading", { name: text.idHeading })).closest(
      "section",
    ) as HTMLElement;
    const me = scenarioMe("table");
    expect(within(section).getByText(text.idLine(me?.dmbotId ?? ""))).toBeTruthy();
    expect(within(section).getByText(text.pointsLine(me?.experiencePoints ?? 0))).toBeTruthy();
  });

  it("no ID section from an API older than this", async () => {
    const api = mockApi("table");
    const original = api.me;
    api.me = async () => {
      const me = await original();
      if (!me) return me;
      const { dmbotId: _id, experiencePoints: _points, canMove: _move, ...rest } = me;
      return rest;
    };
    render(<Account api={api} go={vi.fn()} />);
    await screen.findByText(text.greeting("Belleros"));
    expect(screen.queryByRole("heading", { name: text.idHeading })).toBeNull();
    expect(screen.queryByRole("button", { name: text.moveButton })).toBeNull();
  });

  it("a paid plan can ask for a move code; the steps show only after it was sent", async () => {
    const { api } = show("table");
    expect(await screen.findByText(text.moveText)).toBeTruthy();
    expect(screen.queryByText(text.moveSent)).toBeNull(); // no steps before the code exists
    fireEvent.click(screen.getByRole("button", { name: text.moveButton }));
    expect(await screen.findByText(text.moveSent)).toBeTruthy();
    expect(api.calls).toContain("move-code");
  });

  it("the move advice names Hand over for giving campaigns to someone else", async () => {
    show("table");
    expect((await screen.findByText(text.moveText)).textContent).toContain("Hand over");
  });

  it("a move code that isn't open yet says so", async () => {
    const { api } = show("table");
    api.getMoveCode = async () => {
      throw new ApiError("not-ready");
    };
    fireEvent.click(await screen.findByRole("button", { name: text.moveButton }));
    expect(await screen.findByText(text.moveNotReady)).toBeTruthy();
  });

  it.each([
    ["no-email", text.moveNoEmail],
    ["too-soon", text.moveTooSoon],
    ["email-failed", text.moveEmailFailed],
    ["slow-down", text.moveWait],
  ] as const)("a move code that is refused for %s says why in plain words (#1246)", async (kind, words) => {
    const { api } = show("table");
    api.getMoveCode = async () => {
      throw new ApiError(kind);
    };
    fireEvent.click(await screen.findByRole("button", { name: text.moveButton }));
    expect(await screen.findByText(words)).toBeTruthy();
  });

  it("no move code for Try It or no plan", async () => {
    show("try-it");
    await screen.findByText(text.greeting("Belleros"));
    expect(screen.queryByRole("button", { name: text.moveButton })).toBeNull();
  });
});

describe("a long campaigns list folds (#1991)", () => {
  // Invented campaigns on the pretend API's first signed-in account.
  function showMany(count: number, extra: Partial<Me> = {}) {
    const api = mockApi("table");
    const base = scenarioMe("table")!;
    const campaigns = Array.from({ length: count }, (_, i) => ({
      id: `cmp-test-${i}`,
      name: `Test Campaign ${String(i).padStart(2, "0")}`,
      serverName: "Test Table",
      lastPlayedAt: `2026-09-${String(i + 1).padStart(2, "0")}T19:00:00Z`,
      status: "active" as const,
      role: "owner" as const,
    }));
    api.me = vi.fn(async (): Promise<Me | null> => ({ ...base, campaigns, ...extra }));
    render(<Account api={api} go={vi.fn()} search="" />);
  }
  const rows = () => document.querySelectorAll("#campaigns-list > li").length;

  it("three campaigns: no button", async () => {
    showMany(3);
    await screen.findByText("Test Campaign 02");
    expect(rows()).toBe(3);
    expect(screen.queryByRole("button", { name: /older campaign/ })).toBeNull();
  });

  it("twelve: five shown and a button for seven; pressing it shows all, Show fewer folds again", async () => {
    showMany(12);
    await screen.findByText("Test Campaign 11");
    expect(rows()).toBe(5);
    const more = screen.getByRole("button", { name: text.showOlder(7) });
    expect(more.getAttribute("aria-expanded")).toBe("false");
    expect(more.getAttribute("aria-controls")).toBe("campaigns-list");
    expect(document.getElementById("campaigns-list")).toBeTruthy();
    fireEvent.click(more);
    await waitFor(() => expect(rows()).toBe(12));
    const fewer = screen.getByRole("button", { name: text.showFewer });
    expect(fewer.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(fewer);
    await waitFor(() => expect(rows()).toBe(5));
    expect(document.activeElement?.id).toBe("campaigns-heading");
  });

  it("says one older campaign in the singular", () => {
    expect(text.showOlder(1)).toBe("Show 1 older campaign");
    expect(text.showOlder(7)).toBe("Show 7 older campaigns");
  });

  it("an old paused campaign shows first too", async () => {
    const base = scenarioMe("table")!;
    const api = mockApi("table");
    const campaigns = Array.from({ length: 12 }, (_, i) => ({
      id: `cmp-test-${i}`,
      name: `Test Campaign ${String(i).padStart(2, "0")}`,
      serverName: "Test Table",
      lastPlayedAt: `2026-09-${String(i + 1).padStart(2, "0")}T19:00:00Z`,
      status: i === 0 ? ("paused" as const) : ("active" as const),
      role: "owner" as const,
    }));
    api.me = vi.fn(async (): Promise<Me | null> => ({ ...base, campaigns }));
    render(<Account api={api} go={vi.fn()} search="" />);
    await screen.findByText("Test Campaign 00");
    expect(rows()).toBe(6);
  });

  it("an old campaign with an unanswered hand-over offer shows first", async () => {
    const base = scenarioMe("table")!;
    showMany(12, {
      offers: {
        incoming: base.offers.incoming,
        outgoing: [{ id: "off-test", campaignId: "cmp-test-0", campaignName: "Test Campaign 00", serverName: "Test Table", personName: "Test Person", expiresAt: "2026-12-01T00:00:00Z" }],
      },
    });
    await screen.findByText("Test Campaign 00");
    expect(rows()).toBe(6);
    expect(screen.getByRole("button", { name: text.showOlder(6) })).toBeTruthy();
  });
});
