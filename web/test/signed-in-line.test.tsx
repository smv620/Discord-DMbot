/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The who-is-signed-in line at the top of every signed-in page (#2301).
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import SignedInLine from "../src/account/SignedInLine";
import TranscriptsIsland from "../src/account/TranscriptsIsland";
import { signedInLine as w } from "../src/content/signed-in-words";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("the signed-in line", () => {
  it("says who is signed in, by display name only, and offers Sign out", async () => {
    const api = mockApi("table");
    const me = await api.me();
    render(<SignedInLine api={api} />);
    const line = await screen.findByText(me!.user.name);
    expect(line.closest(".signed-in-line")?.textContent).toContain(`${w.as} ${me!.user.name}`);
    expect(line.closest(".signed-in-line")?.textContent).toContain(w.notYou);
    // Never a Discord number or an email.
    expect(line.closest(".signed-in-line")?.textContent).not.toContain(me!.user.id);
    expect(screen.getByRole("button", { name: w.signOut })).toBeTruthy();
  });

  it("Sign out ends the sign-in, then reloads the page", async () => {
    const api = mockApi("table");
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    render(<SignedInLine api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: w.signOut }));
    await waitFor(() => expect(reload).toHaveBeenCalled());
    expect(api.calls.includes("signOut")).toBe(true);
    vi.unstubAllGlobals();
  });

  it("says so when Sign out fails, and stays signed in", async () => {
    const api = mockApi("table");
    api.signOut = async () => {
      throw new ApiError("server");
    };
    render(<SignedInLine api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: w.signOut }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.couldNotSignOut);
    expect((screen.getByRole("button", { name: w.signOut }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("when signed out, says so and offers Sign in with Discord", async () => {
    const api = mockApi("signed-out");
    render(<SignedInLine api={api} />);
    expect(await screen.findByText(w.notSignedIn, { exact: false })).toBeTruthy();
    const button = screen.getByRole("link", { name: w.signIn }) as HTMLAnchorElement;
    expect(button.getAttribute("href")).toBe(api.signInUrl());
  });

  it("keeps its height while it asks, and is quiet when the question fails", async () => {
    const api = mockApi("table");
    let answer: (v: null) => void = () => undefined;
    api.me = () => new Promise((resolve) => (answer = resolve as (v: null) => void));
    const { container } = render(<SignedInLine api={api} />);
    expect(container.querySelector(".signed-in-line")).toBeTruthy();
    expect(screen.getByText(w.checking)).toBeTruthy();
    answer(null);
    await screen.findByText(w.notSignedIn, { exact: false });

    cleanup();
    const down = mockApi("table");
    down.me = async () => {
      throw new ApiError("network");
    };
    const second = render(<SignedInLine api={down} />);
    await waitFor(() => expect(second.queryByText(w.checking)).toBeNull());
    expect(within(second.container).queryByText(w.notSignedIn, { exact: false })).toBeNull();
    expect(second.queryByRole("button", { name: w.signOut })).toBeNull();
  });
});

describe("the sign-in link comes back to the same page", () => {
  const original = window.location.pathname;
  afterEach(() => window.history.replaceState({}, "", original));

  it("carries the account page it was opened on", () => {
    window.history.replaceState({}, "", "/account/import/100.cmp1");
    expect(httpApi("/api").signInUrl()).toBe("/api/auth/discord/start?next=%2Faccount%2Fimport%2F100.cmp1");
  });

  it("carries nothing from a page that isn't under /account", () => {
    window.history.replaceState({}, "", "/pricing");
    expect(httpApi("/api").signInUrl()).toBe("/api/auth/discord/start");
  });
});

describe("the pages that show it", () => {
  it("a page's island shows the line above the page", async () => {
    render(<TranscriptsIsland />);
    // The island picks the real API outside a mock build; it only needs to mount the line.
    await waitFor(() => expect(document.querySelector(".signed-in-line")).toBeTruthy());
  });
});

describe("every signed-in page has it", () => {
  // The public recap (no sign-in) and My Account itself ("Hi, <name>. Sign out", its own panel) are the
  // two that don't; any other account page's island must carry the shared line.
  it.each(["Recaps", "Transcripts", "HouseRules", "Homebrew", "Import"])("%sIsland", (name) => {
    const source = readFileSync(`src/account/${name}Island.tsx`, "utf8");
    expect(source).toContain("<SignedInLine api={api} />");
  });
});
