/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "People you play with" (#4226): a DM's remembered players with Forget this player, a player's "DMs who remember you" with Clear,
// the switch, the failures, and the HTTP calls that go to core's routes.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { httpPlayers, mockPlayers, PlayersError } from "../src/account/players-api";
import RememberedPlayers from "../src/account/RememberedPlayers";
import { rememberedWords as w } from "../src/content/remembered-players-words";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/remembered-players-switch", () => ({
  get REMEMBERED_PLAYERS_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = true;
});

describe("the page", () => {
  it("shows nothing but a line while the switch is off, and asks the server nothing", () => {
    live.on = false;
    const api = mockPlayers();
    render(<RememberedPlayers api={api} />);
    expect(screen.getByText(w.notOpen)).toBeTruthy();
    expect(api.calls).toEqual([]);
  });

  it("lists the DM's players with their dates and campaigns, and a name that is not known yet", async () => {
    render(<RememberedPlayers api={mockPlayers()} />);
    expect(await screen.findByText("Eric")).toBeTruthy();
    expect(screen.getByText(w.mine.unknownName)).toBeTruthy();
    expect(screen.getByText(/3 campaigns · since Nov 14, 2023/)).toBeTruthy();
    expect(screen.getByText(/1 campaign · since/)).toBeTruthy();
  });

  it("Forget this player removes only that player and says so", async () => {
    const api = mockPlayers();
    render(<RememberedPlayers api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: w.mine.forget("Eric") }));
    expect(await screen.findByText(w.mine.forgotten("Eric"))).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("Eric")).toBeNull());
    expect(screen.getByText(w.mine.unknownName)).toBeTruthy();
    expect(api.calls).toContain("forget:100000000000000001");
  });

  it("a player who is already forgotten says so, in plain words", async () => {
    const api = mockPlayers();
    render(<RememberedPlayers api={api} />);
    const button = await screen.findByRole("button", { name: w.mine.forget("Eric") });
    await api.forget("100000000000000001"); // (forgotten in another tab)
    fireEvent.click(button);
    expect(await screen.findByText(w.mine.already)).toBeTruthy();
  });

  it("lists the DMs who remember the player, and Clear removes that DM", async () => {
    const api = mockPlayers();
    render(<RememberedPlayers api={api} />);
    expect(await screen.findByText("Belleros")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.by.clear("Belleros") }));
    expect(await screen.findByText(w.by.cleared("Belleros"))).toBeTruthy();
    await waitFor(() => expect(screen.getByText(w.by.none)).toBeTruthy());
    expect(api.calls).toContain("clear:100000000000000009");
  });

  it("Clear all shows only with more than one DM", async () => {
    const api = mockPlayers();
    render(<RememberedPlayers api={api} />);
    await screen.findByText("Belleros");
    expect(screen.queryByRole("button", { name: w.by.clearAll })).toBeNull();
  });

  it("says nothing was found in plain words when both lists are empty", async () => {
    const api = mockPlayers();
    await api.clear("all");
    for (const p of await api.mine()) await api.forget(p.player);
    render(<RememberedPlayers api={api} />);
    expect(await screen.findByText(w.mine.none)).toBeTruthy();
    expect(screen.getByText(w.by.none)).toBeTruthy();
  });

  it("a failed load says so and Try again loads it", async () => {
    const api = mockPlayers();
    const real = api.mine.bind(api);
    let first = true;
    api.mine = async () => {
      if (first) {
        first = false;
        throw new PlayersError("network");
      }
      return real();
    };
    render(<RememberedPlayers api={api} />);
    expect(await screen.findByText(w.failed)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: w.tryAgain }));
    expect(await screen.findByText("Eric")).toBeTruthy();
  });

  it("a signed-out answer says to sign in again", async () => {
    const api = mockPlayers();
    api.mine = async () => {
      throw new PlayersError("signed_out");
    };
    render(<RememberedPlayers api={api} />);
    expect(await screen.findByText(w.signedOut)).toBeTruthy();
  });
});

describe("the calls", () => {
  function fetcher(status: number, body: unknown) {
    return vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch & ReturnType<typeof vi.fn>;
  }

  it("go to core's four routes with the page's own header and the cookie", async () => {
    const f = fetcher(200, { items: [], removed: 1 });
    const api = httpPlayers("/api/", f);
    await api.mine();
    await api.forget("123");
    await api.by();
    await api.clear("all");
    const seen = (f as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => `${(c[1] as RequestInit).method} ${c[0] as string}`);
    expect(seen).toEqual(["GET /api/me/players", "POST /api/me/players/123/forget", "GET /api/me/remembered-by", "POST /api/me/remembered-by/all/clear"]);
    const init = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[0]![1] as RequestInit;
    expect(init.credentials).toBe("include");
    expect((init.headers as Record<string, string>)["X-DMbot-Request"]).toBe("1");
  });

  it("keep the server's word for a refusal, and say signed_out for a 401", async () => {
    await expect(httpPlayers("/api", fetcher(409, { detail: "gone" })).forget("1")).rejects.toMatchObject({ kind: "gone" });
    await expect(httpPlayers("/api", fetcher(401, {})).mine()).rejects.toMatchObject({ kind: "signed_out" });
    const down = vi.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;
    await expect(httpPlayers("/api", down).by()).rejects.toMatchObject({ kind: "network" });
  });
});
