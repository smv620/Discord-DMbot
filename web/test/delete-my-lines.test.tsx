/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Delete my transcripts" on My Account (#2769): Discord's own steps and words (one warning with the counts, one
// confirmation), a request the bot carries out with the wait shown (#2564), and every way it can end. Shown only
// while DELETE_MY_LINES_LIVE is true (false today): nothing is asked of the API before then.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import Account from "../src/account/Account";
import { ApiError, httpApi, type AccountApi } from "../src/account/api";
import DeleteMyLines from "../src/account/DeleteMyLines";
import { mockApi } from "../src/account/mock";
import { text } from "../src/content/account";
import { deleteMyLinesWords as w } from "../src/content/delete-my-lines-words";
import discord from "./fixtures/discord-delete-words.json";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/delete-my-lines-switch", () => ({
  get DELETE_MY_LINES_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = false;
  vi.useRealTimers();
});

const calls = (api: unknown, start: string): string[] => (api as { calls: string[] }).calls.filter((c) => c.startsWith(start));
const slow = { timeout: 8000 };

async function signedIn(): Promise<AccountApi> {
  const api = mockApi("table");
  await api.me();
  return api;
}
async function section(serverId = "srv-thursday", serverName = "Thursday Table", api?: AccountApi) {
  const useApi = api ?? (await signedIn());
  render(<DeleteMyLines api={useApi} serverId={serverId} serverName={serverName} />);
  return useApi;
}
const press = (name: string) => fireEvent.click(screen.getByRole("button", { name }));

describe("while it is switched off", () => {
  it("shows no button on My Account and asks nothing", async () => {
    const api = mockApi("table");
    render(<Account api={api} go={vi.fn()} search="" />);
    await screen.findByText("Where DMbot may record you");
    expect(screen.queryByRole("button", { name: /Delete my transcripts/ })).toBeNull();
    expect(calls(api, "myLines")).toHaveLength(0);
  });

  it("says under the list that a server left behind is handled in Discord's menu, once it is on", async () => {
    live.on = true;
    render(<Account api={mockApi("table")} go={vi.fn()} search="" />);
    expect(await screen.findByText(text.recordingLeft, {}, slow)).toBeTruthy();
    cleanup();
    live.on = false;
    render(<Account api={mockApi("table")} go={vi.fn()} search="" />);
    await screen.findByText("Where DMbot may record you");
    expect(screen.queryByText(text.recordingLeft)).toBeNull();
  });

  it("shows a button for each server once it is on", async () => {
    live.on = true;
    render(<Account api={mockApi("table")} go={vi.fn()} search="" />);
    expect(await screen.findByRole("button", { name: w.buttonFor("Thursday Table") }, slow)).toBeTruthy();
    expect(screen.getByRole("button", { name: w.buttonFor("Gorrak's Hall") })).toBeTruthy();
  });
});

describe("the words are Discord's", () => {
  it("are word for word what Discord's 🗑 flow says (core/tests/test_web_my_lines.py keeps the file equal to Discord)", () => {
    expect(w.button).toBe(discord.label);
    expect(w.yes).toBe(discord.yes);
    expect(w.cancel).toBe(discord.cancel);
    expect(w.kept).toBe(discord.kept);
    expect(w.failed).toBe(discord.failed);
    expect(w.problem["server"]).toBe(discord.failed);
    expect(w.warning("Thursday Table", 12, 3)).toBe(discord.warning_12_3);
    expect(w.warning("Thursday Table", 1, 1)).toBe(discord.warning_1_1);
    expect(w.done("Thursday Table")).toBe(discord.done);
    expect(w.nothing("Thursday Table")).toBe(discord.nothing);
  });
});

describe("the steps", () => {
  it("looks the counts up only when asked, then gives the one warning with them and the two choices", async () => {
    const api = await section();
    expect(calls(api, "myLines")).toHaveLength(0);
    press(w.buttonFor("Thursday Table"));
    expect(await screen.findByText(w.warning("Thursday Table", 12, 3))).toBeTruthy();
    expect(w.warning("Thursday Table", 12, 3)).toMatch(/12 lines in 3 sessions.*can't be undone/);
    const buttons = screen.getAllByRole("button").map((b) => b.textContent);
    expect(buttons).toEqual([w.cancel, w.yes]); // the safe choice first
    expect(calls(api, "deleteMyLines")).toHaveLength(0);
  });

  it("says one line in one session in the singular", () => {
    expect(w.warning("A", 1, 1)).toMatch(/1 line in 1 session\./);
  });

  it("Cancel changes nothing, says so in Discord's words, and asks nothing more", async () => {
    const api = await section();
    press(w.buttonFor("Thursday Table"));
    await screen.findByText(w.warning("Thursday Table", 12, 3));
    press(w.cancel);
    expect(screen.queryByText(w.warning("Thursday Table", 12, 3))).toBeNull();
    expect(screen.getByText(w.kept)).toBeTruthy();
    expect(calls(api, "deleteMyLines")).toHaveLength(0);
    expect(screen.getByRole("button", { name: w.buttonFor("Thursday Table") })).toBeTruthy();
  });

  it("with nothing written down there is nothing to confirm", async () => {
    const api = await section("srv-gorrak", "Gorrak's Hall");
    press(w.buttonFor("Gorrak's Hall"));
    expect(await screen.findByText(w.nothing("Gorrak's Hall"))).toBeTruthy();
    expect(calls(api, "deleteMyLines")).toHaveLength(0);
  });
});

describe("the deleting", () => {
  it("queues one request for the server, shows what DMbot is doing, then says it is done", async () => {
    const api = await section();
    press(w.buttonFor("Thursday Table"));
    await screen.findByText(w.warning("Thursday Table", 12, 3));
    press(w.yes);
    expect(await screen.findByText(w.stage["waiting"] as string)).toBeTruthy();
    expect(await screen.findByText(w.stage["deleting"] as string, {}, slow)).toBeTruthy();
    expect(screen.getByText(/Running for/)).toBeTruthy();
    expect(await screen.findByText(w.done("Thursday Table"), {}, slow)).toBeTruthy();
    expect(calls(api, "deleteMyLines")).toEqual(["deleteMyLines:srv-thursday"]);
    // Gone: the next look finds nothing to delete.
    press(w.buttonFor("Thursday Table"));
    expect(await screen.findByText(w.nothing("Thursday Table"))).toBeTruthy();
  }, 20000);

  it("shows a sending state, and no second Yes, from the press until the bot has the request", async () => {
    const api = await signedIn();
    let release: (id: string) => void = () => {};
    api.deleteMyLines = () => new Promise<string>((resolve) => (release = resolve));
    await section("srv-thursday", "Thursday Table", api);
    press(w.buttonFor("Thursday Table"));
    await screen.findByText(w.warning("Thursday Table", 12, 3));
    press(w.yes);
    expect(await screen.findByText(w.sending)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.yes })).toBeNull();
    release("a".repeat(32));
  });

  it("shows DMbot's own clock from its answer, and quiet words that fit the stage", async () => {
    const api = await signedIn();
    const now = Math.floor(Date.now() / 1000);
    let stage = "waiting";
    api.myLinesStatus = async () => ({ stage, state: "stalled", reason: "stalled", startedAt: now - 900, updatedAt: now - 700, serverNow: now, lines: null, sessions: null });
    await section("srv-thursday", "Thursday Table", api);
    press(w.buttonFor("Thursday Table"));
    await screen.findByText(w.warning("Thursday Table", 12, 3));
    press(w.yes);
    expect(await screen.findByText(w.quietWaiting, {}, slow)).toBeTruthy();
    expect(screen.getByText(/Running for 15 min/)).toBeTruthy(); // the server's seconds, not this page's guess
    stage = "deleting";
    expect(await screen.findByText(w.quietDeleting, {}, slow)).toBeTruthy();
  });

  it("offers no second press while it works", async () => {
    await section();
    press(w.buttonFor("Thursday Table"));
    await screen.findByText(w.warning("Thursday Table", 12, 3));
    press(w.yes);
    await screen.findByText(w.stage["waiting"] as string);
    expect(screen.queryByRole("button", { name: w.yes })).toBeNull();
    expect(screen.queryByRole("button", { name: w.buttonFor("Thursday Table") })).toBeNull();
    await screen.findByText(w.done("Thursday Table"), {}, slow);
  }, 20000);

  it("follows a request that is already open (a reload, or another tab) instead of asking for a second", async () => {
    const api = await signedIn();
    await api.deleteMyLines!("srv-thursday"); // one already on its way
    render(<DeleteMyLines api={api} serverId="srv-thursday" serverName="Thursday Table" />);
    press(w.buttonFor("Thursday Table"));
    expect(await screen.findByText(w.stage["waiting"] as string)).toBeTruthy();
    expect(calls(api, "deleteMyLines")).toHaveLength(1);
    await screen.findByText(w.done("Thursday Table"), {}, slow);
  }, 20000);

  it("says DMbot could not do it, in the same words as Discord, and offers it again", async () => {
    const api = await signedIn();
    api.myLinesStatus = async () => ({ stage: "failed", state: "failed", reason: "failed", startedAt: 1, updatedAt: 2, serverNow: 3, lines: null, sessions: null });
    await section("srv-thursday", "Thursday Table", api);
    press(w.buttonFor("Thursday Table"));
    await screen.findByText(w.warning("Thursday Table", 12, 3));
    press(w.yes);
    await waitFor(() => expect(screen.getByRole("alert", { name: "" }).textContent || screen.getAllByRole("alert").map((a) => a.textContent).join("")).toContain("nothing was deleted"), slow);
    expect(screen.getByRole("button", { name: w.buttonFor("Thursday Table") })).toBeTruthy();
  });

  it.each([
    ["too-soon", "too-soon"],
    ["not-found", "not-found"],
  ])("tells the person what a refused request (%s) means", async (kind, key) => {
    const api = await signedIn();
    api.deleteMyLines = async () => {
      throw new ApiError(kind as "too-soon");
    };
    await section("srv-thursday", "Thursday Table", api);
    press(w.buttonFor("Thursday Table"));
    await screen.findByText(w.warning("Thursday Table", 12, 3));
    press(w.yes);
    await waitFor(() => expect(screen.getAllByRole("alert").some((a) => a.textContent === w.problem[key])).toBe(true));
  });

  it("when the lines were already gone by the time it asked, says so rather than failing", async () => {
    const api = await signedIn();
    api.deleteMyLines = async () => {
      throw new ApiError("nothing-to-delete");
    };
    await section("srv-thursday", "Thursday Table", api);
    press(w.buttonFor("Thursday Table"));
    await screen.findByText(w.warning("Thursday Table", 12, 3));
    press(w.yes);
    expect(await screen.findByText(w.nothing("Thursday Table"))).toBeTruthy();
  });

  describe("with time moving", () => {
    it("says so and what to do once DMbot's own answer calls it stalled, and that nothing is deleted yet", async () => {
      const api = await signedIn();
      const now = Math.floor(Date.now() / 1000);
      api.myLinesStatus = async () => ({ stage: "waiting", state: "stalled", reason: "stalled", startedAt: now - 300, updatedAt: now - 300, serverNow: now, lines: null, sessions: null });
      await section("srv-thursday", "Thursday Table", api);
      press(w.buttonFor("Thursday Table"));
      await screen.findByText(w.warning("Thursday Table", 12, 3));
      press(w.yes);
      expect(await screen.findByText(w.quietWaiting, {}, slow)).toBeTruthy();
      expect(screen.getByText(/Quiet for/)).toBeTruthy();
    });

    it("gives up after an hour with words that do not claim it failed", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const api = await signedIn();
      api.myLinesStatus = async () => ({ stage: "waiting", state: "running", reason: null, startedAt: 1, updatedAt: 1, serverNow: 1, lines: null, sessions: null });
      await section("srv-thursday", "Thursday Table", api);
      press(w.buttonFor("Thursday Table"));
      await screen.findByText(w.warning("Thursday Table", 12, 3));
      press(w.yes);
      await screen.findByText(w.stage["waiting"] as string);
      await vi.advanceTimersByTimeAsync(61 * 60_000);
      expect(screen.getByText(w.gaveUp)).toBeTruthy();
    });
  });
});

it("has no serious accessibility problem while asking", async () => {
  await section();
  press(w.buttonFor("Thursday Table"));
  await screen.findByText(w.warning("Thursday Table", 12, 3));
  const result = await axe.run(document.body, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] }, rules: { "color-contrast": { enabled: false } } });
  expect(result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")).toEqual([]);
});

describe("the real client", () => {
  function fake(answers: { status: number; body?: unknown }[]) {
    const seen: string[] = [];
    const queue = [...answers];
    const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
      seen.push(`${init?.method} ${new URL(url).pathname}`);
      const next = queue.shift() ?? { status: 200, body: {} };
      return new Response(JSON.stringify(next.body ?? {}), { status: next.status, headers: { "Content-Type": "application/json" } });
    }) as unknown as typeof fetch);
    return { api, seen };
  }

  it("uses the server's own addresses and reads the answers", async () => {
    const { api, seen } = fake([
      { status: 200, body: { lines: 6, sessions: 2, open: null } },
      { status: 202, body: { id: "a".repeat(32) } },
      { status: 200, body: { stage: "done", state: "done", lines: 6, sessions: 2 } },
    ]);
    expect((await api.myLines!("111")).lines).toBe(6);
    expect(await api.deleteMyLines!("111")).toBe("a".repeat(32));
    expect((await api.myLinesStatus!("111", "a".repeat(32))).sessions).toBe(2);
    expect(seen).toEqual([
      "GET /me/transcripts/111/mine",
      "POST /me/transcripts/111/delete",
      `GET /me/transcripts/111/delete/${"a".repeat(32)}`,
    ]);
  });

  it.each([
    [409, "nothing_to_delete", "nothing-to-delete"],
    [429, "too_soon", "too-soon"],
    [404, "not_found", "not-found"],
  ])("reads %s %s as the %s kind", async (status, code, kind) => {
    const { api } = fake([{ status, body: { error: code } }]);
    await expect(api.deleteMyLines!("111")).rejects.toMatchObject({ kind });
  });
});

