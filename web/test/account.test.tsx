/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The account page (#434): every state from fixture data, every button, and the rule that
// tokens never go in URLs or browser storage.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Account from "../src/account/Account";
import { ApiError, httpApi } from "../src/account/api";
import { candidates, mockApi, type Scenario } from "../src/account/mock";
import { hoursUsedLine, text } from "../src/content/account";

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

  it("signed out after a failed sign-in: says so and what to do", async () => {
    show("signed-out", "?signin=failed");
    expect((await screen.findByRole("alert")).textContent).toBe(text.signInFailed);
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
    expect(screen.getByText("Table")).toBeTruthy();
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
      text.alreadyThere,
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

  it("lapsed: says the plan stopped and offers plans", async () => {
    show("lapsed");
    expect(await screen.findByText(text.lapsed)).toBeTruthy();
    expect(screen.queryByLabelText(text.hoursBarLabel)).toBeNull();
    expect(screen.getByRole("button", { name: "Choose Guild" })).toBeTruthy();
  });
});

describe("actions", () => {
  it("adds DMbot to a server through Discord's page", async () => {
    const { go } = show("table");
    const row = await waitFor(() => {
      const found = document.querySelector('[data-server="200000000000000002"]');
      if (!found) throw new Error("not yet");
      return found;
    });
    fireEvent.click(row.querySelector("button") as HTMLButtonElement);
    await waitFor(() => expect(go).toHaveBeenCalledWith("#demo-install-200000000000000002"));
  });

  it("hands a campaign over to a chosen person", async () => {
    const { api } = show("table");
    await screen.findByText("The Brynwater Crossing");
    const row = document.querySelector('[data-campaign="cmp-brynwater"]') as HTMLElement;
    fireEvent.click(row.querySelector("button") as HTMLButtonElement);

    const confirm = await screen.findByRole("button", { name: text.handOverConfirm });
    expect((confirm as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText(candidates[0]?.name ?? ""));
    expect((confirm as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(confirm);

    expect(
      await screen.findByText(text.handOverDone("The Brynwater Crossing", "Oskar Vane")),
    ).toBeTruthy();
    expect(api.calls).toContain("handover:cmp-brynwater:100000000000000002");
    await waitFor(() =>
      expect(document.querySelector('[data-campaign="cmp-brynwater"]')).toBeNull(),
    );
  });

  it("says plainly when nobody can take a campaign", async () => {
    show("table");
    await screen.findByText("Ashen Crown");
    const row = document.querySelector('[data-campaign="cmp-ashen"]') as HTMLElement;
    fireEvent.click(row.querySelector("button") as HTMLButtonElement);
    expect(await screen.findByText(text.handOverNobody)).toBeTruthy();
    expect(screen.queryByRole("button", { name: text.handOverConfirm })).toBeNull();
  });

  it("explains a full plan when a hand-over is refused", async () => {
    const api = mockApi("table");
    api.handover = () => Promise.reject(new ApiError("no-free-slot"));
    render(<Account api={api} go={vi.fn()} />);
    await screen.findByText("The Brynwater Crossing");
    const row = document.querySelector('[data-campaign="cmp-brynwater"]') as HTMLElement;
    fireEvent.click(row.querySelector("button") as HTMLButtonElement);
    fireEvent.click(await screen.findByLabelText("Mirelle"));
    fireEvent.click(screen.getByRole("button", { name: text.handOverConfirm }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.noFreeSlot);
  });

  it("deletes the account only after a warning and a second yes", async () => {
    const { api } = show("table");
    fireEvent.click(await screen.findByRole("button", { name: text.deleteStart }));
    expect(screen.getByText(text.deleteWarning)).toBeTruthy();
    expect(api.calls).not.toContain("requestDelete");

    fireEvent.click(screen.getByRole("button", { name: text.deleteNext }));
    const confirm = await screen.findByRole("button", { name: text.deleteConfirm });
    expect(screen.getByText(text.deleteSure)).toBeTruthy();
    expect(api.calls.some((c) => c.startsWith("confirmDelete"))).toBe(false);

    fireEvent.click(confirm);
    expect(await screen.findByText(text.deleted)).toBeTruthy();
    expect(api.calls).toContain("confirmDelete:demo-token");
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

describe("hours words", () => {
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

  it("treats 401 on /me as signed out", async () => {
    const { fetcher } = fakeFetch(401);
    expect(await httpApi("https://api.example", fetcher).me()).toBeNull();
  });

  it("maps error codes to plain kinds", async () => {
    const api = httpApi("https://api.example", fakeFetch(409, { error: "no_free_slot" }).fetcher);
    await expect(api.handover("c1", "u1")).rejects.toMatchObject({ kind: "no-free-slot" });
    const down = httpApi("https://api.example", (() =>
      Promise.reject(new TypeError("offline"))) as unknown as typeof fetch);
    await expect(down.me()).rejects.toMatchObject({ kind: "network" });
  });

  it("sends the delete token in the body, not the URL", async () => {
    const { calls, fetcher } = fakeFetch(204);
    await httpApi("https://api.example", fetcher).confirmDelete("secret-token");
    expect(calls[0]?.url).toBe("https://api.example/account/delete");
    expect(calls[0]?.init.body).toBe(JSON.stringify({ confirm_token: "secret-token" }));
  });

  it("escapes ids in paths", async () => {
    const { calls, fetcher } = fakeFetch(200, []);
    await httpApi("https://api.example", fetcher).handoverCandidates("a/../b");
    expect(calls[0]?.url).toBe("https://api.example/campaigns/a%2F..%2Fb/handover-candidates");
  });
});
