/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Your Discord accounts" on My Account (#2534): the list by name, adding by Discord sign-in, and
// Remove with one confirmation, never for the last one. Shown only while DISCORD_ACCOUNTS_LIVE is true
// (false today), so these tests switch it on.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import Account from "../src/account/Account";
import { ApiError, httpApi, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import { discordAccountsWords as w } from "../src/content/discord-accounts";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/discord-accounts", async (original) => {
  const real = await original<typeof import("../src/content/discord-accounts")>();
  return {
    ...real,
    get DISCORD_ACCOUNTS_LIVE() {
      return live.on;
    },
  };
});

afterEach(() => {
  cleanup();
  live.on = false;
});

const calls = (api: unknown, start: string): string[] => (api as { calls: string[] }).calls.filter((c) => c.startsWith(start));

async function show(search = "", api: AccountApi = mockApi("table")) {
  live.on = true;
  render(<Account api={api} go={vi.fn()} search={search} />);
  const heading = await screen.findByRole("heading", { name: w.heading });
  return { api, section: heading.closest("section") as HTMLElement };
}

describe("your Discord accounts", () => {
  it("shows nothing while it is switched off", async () => {
    render(<Account api={mockApi("table")} go={vi.fn()} search="" />);
    await screen.findByRole("heading", { name: /My Account/ });
    expect(screen.queryByRole("heading", { name: w.heading })).toBeNull();
  });

  it("lists the accounts by name and says the plan works from any of them", async () => {
    const { section } = await show();
    expect(within(section).getByText(w.lead)).toBeTruthy();
    expect(within(section).getByText("Mira (main)")).toBeTruthy();
    expect(within(section).getByText("Mira on the phone")).toBeTruthy();
    expect(within(section).getByText(w.you)).toBeTruthy();
    expect(section.textContent).not.toMatch(/\d{15,}/); // never a raw number
  });

  it("adds a Discord account through Discord's own sign-in, never a typed number", async () => {
    const { section } = await show();
    const add = within(section).getByRole("link", { name: w.add });
    expect(add.getAttribute("href")).toBe("#demo-add-discord");
    expect(section.querySelector("input")).toBeNull();
  });

  it("removes one only after one confirmation, and Keep it changes nothing", async () => {
    const { api, section } = await show();
    fireEvent.click(within(section).getByRole("button", { name: w.removeFor("Mira on the phone") }));
    expect(within(section).getByText(w.confirmFor("Mira on the phone"))).toBeTruthy();
    expect(calls(api, "removeDiscordAccount")).toHaveLength(0);
    fireEvent.click(within(section).getByRole("button", { name: w.keep }));
    expect(calls(api, "removeDiscordAccount")).toHaveLength(0);
    fireEvent.click(within(section).getByRole("button", { name: w.removeFor("Mira on the phone") }));
    fireEvent.click(within(section).getByRole("button", { name: w.confirmYes }));
    await waitFor(() => expect(calls(api, "removeDiscordAccount")).toEqual(["removeDiscordAccount:d2"]));
    await waitFor(() => expect(screen.queryByText("Mira on the phone")).toBeNull());
    expect(screen.getByText(w.removed)).toBeTruthy();
  });

  it("never offers Remove for the one in use, and says why", async () => {
    const { section } = await show();
    expect(within(section).queryByRole("button", { name: w.removeFor("Mira (main)") })).toBeNull();
    expect(within(section).getByText(w.cantRemoveNow)).toBeTruthy();
  });

  it("never offers Remove for the last account", async () => {
    const { section } = await show();
    fireEvent.click(within(section).getByRole("button", { name: w.removeFor("Mira on the phone") }));
    fireEvent.click(within(section).getByRole("button", { name: w.confirmYes }));
    await waitFor(() => expect(screen.queryByText("Mira on the phone")).toBeNull());
    const after = screen.getByRole("heading", { name: w.heading }).closest("section") as HTMLElement;
    expect(within(after).queryByRole("button", { name: /^Remove/ })).toBeNull();
    expect(within(after).getByText(w.lastOne)).toBeTruthy();
    expect(within(after).queryByText(w.cantRemoveNow)).toBeNull();
  });

  it("says why a removal didn't happen", async () => {
    const api = mockApi("table");
    api.removeDiscordAccount = async () => {
      throw new ApiError("last-account");
    };
    const { section } = await show("", api);
    fireEvent.click(within(section).getByRole("button", { name: w.removeFor("Mira on the phone") }));
    fireEvent.click(within(section).getByRole("button", { name: w.confirmYes }));
    expect(await within(section).findByText(w.problem["last-account"] as string)).toBeTruthy();
  });

  it("reads the list again when the account was already gone", async () => {
    const api = mockApi("table");
    const before = calls(api, "me").length;
    api.removeDiscordAccount = async () => {
      throw new ApiError("not-found");
    };
    const { section } = await show("", api);
    const reads = calls(api, "me").length;
    fireEvent.click(within(section).getByRole("button", { name: w.removeFor("Mira on the phone") }));
    fireEvent.click(within(section).getByRole("button", { name: w.confirmYes }));
    expect(await within(section).findByText(w.problem["not-found"] as string)).toBeTruthy();
    await waitFor(() => expect(calls(api, "me").length).toBeGreaterThan(reads));
    expect(reads).toBeGreaterThan(before - 1);
    // the confirmation is closed, and the row's Remove button is back
    expect(within(section).queryByRole("button", { name: w.confirmYes })).toBeNull();
  });

  it("says something for a refusal it has no words for", async () => {
    const api = mockApi("table");
    api.removeDiscordAccount = async () => {
      throw new ApiError("too-big");
    };
    const { section } = await show("", api);
    fireEvent.click(within(section).getByRole("button", { name: w.removeFor("Mira on the phone") }));
    fireEvent.click(within(section).getByRole("button", { name: w.confirmYes }));
    expect(await within(section).findByText(w.problem["default"] as string)).toBeTruthy();
  });

  it.each(["added", "taken", "has_campaigns", "already", "cancelled", "failed"])("says what coming back from Discord (%s) means", async (result) => {
    const { section } = await show(`?discord=${result}`);
    expect(within(section).getByText(w.result[result] as string)).toBeTruthy();
  });

  it("has nothing serious or critical for axe, with the confirmation open", async () => {
    const { section } = await show();
    fireEvent.click(within(section).getByRole("button", { name: w.removeFor("Mira on the phone") }));
    const result = await axe.run(document.body, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
    const bad = result.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  });

  it("is held with its What's new entry", async () => {
    const real = await vi.importActual<typeof import("../src/content/discord-accounts")>("../src/content/discord-accounts");
    const { held } = await import("../src/content/whats-new");
    expect(held.map((h) => h.title).includes("Use more than one Discord account")).toBe(!real.DISCORD_ACCOUNTS_LIVE);
  });
});

describe("the real client", () => {
  it("removes by the opaque key, and sends adding to Discord's sign-in", async () => {
    const seen: string[] = [];
    const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
      seen.push(`${init?.method} ${new URL(url).pathname}`);
      return new Response(null, { status: 204 });
    }) as typeof fetch);
    await api.removeDiscordAccount!("k 1");
    expect(seen).toEqual(["POST /me/discord-accounts/k%201/remove"]);
    expect(api.addDiscordUrl!()).toBe("https://api.example/auth/discord/add?next=%2Faccount");
  });

  it("reads 409 last_account as last-account", async () => {
    const api = httpApi("https://api.example", (async () => new Response(JSON.stringify({ detail: "last_account" }), { status: 409 })) as typeof fetch);
    await expect(api.removeDiscordAccount!("k1")).rejects.toMatchObject({ kind: "last-account" });
  });
});
