/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The account page (#434): every state from fixture data, every button, and the rule that
// tokens never go in URLs or browser storage.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import Account from "../src/account/Account";
import { ApiError, httpApi } from "../src/account/api";
import { candidates, mockApi, type Scenario } from "../src/account/mock";
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

  it("explains the API's refusals in plain words", async () => {
    const api = mockApi("no-plan");
    api.startTryIt = () => Promise.reject(new ApiError("try-it-used"));
    render(<Account api={api} go={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: text.startTryIt }));
    expect((await screen.findByRole("alert")).textContent).toBe(text.errors["try-it-used"]);
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
    expect(screen.getByRole("button", { name: text.deleteStart })).toBeTruthy();
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
    ["https://checkout.paddle.com/x", true],
    ["https://sandbox-checkout.paddle.com/x", true],
    ["https://dmbot.lemonsqueezy.com/checkout", true],
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

describe("hours words", () => {
  it("tells a Try It user and a paid user different next steps at the cap", () => {
    expect(hoursLeftLine(8, 8, null, "try-it")).toBe("Pick a plan below to keep playing.");
    expect(hoursLeftLine(18, 18, "2026-10-14", "table")).toBe(
      "Your hours start again on Oct 14. Need more now? Tap Change plan, then add 10 hours for $4.99.",
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

  it("treats 401 on /me as signed out", async () => {
    const { fetcher } = fakeFetch(401);
    expect(await httpApi("https://api.example", fetcher).me()).toBeNull();
  });

  it("maps error codes to plain kinds", async () => {
    const api = httpApi("https://api.example", fakeFetch(409, { error: "no_free_slot" }).fetcher);
    await expect(api.handover("c1", "u1")).rejects.toMatchObject({ kind: "no-free-slot" });
    const expired = httpApi("https://api.example", fakeFetch(403, { error: "confirm_again" }).fetcher);
    await expect(expired.confirmDelete("t")).rejects.toMatchObject({ kind: "confirm-again" });
    const noPlan = httpApi("https://api.example", fakeFetch(409, { error: "no_paid_plan" }).fetcher);
    await expect(noPlan.billingPortalUrl()).rejects.toMatchObject({ kind: "no-paid-plan" });
    const down = httpApi("https://api.example", (() =>
      Promise.reject(new TypeError("offline"))) as unknown as typeof fetch);
    await expect(down.me()).rejects.toMatchObject({ kind: "network" });
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
    const { calls, fetcher } = fakeFetch(200, []);
    await httpApi("https://api.example", fetcher).handoverCandidates("a/../b");
    expect(calls[0]?.url).toBe("https://api.example/campaigns/a%2F..%2Fb/handover-candidates");
  });
});
