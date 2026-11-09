/** @jsxImportSource preact */
// @vitest-environment happy-dom
// "Make the script" on a session's Script tab (#3899, behind SCRIPT_MAKE_LIVE): a game master sees the cost and one button, the
// wait shows its stage and count, the script then appears in the tab; a session that has one says so; the plan gate and scripts
// off say so; a failure says so; a player never gets it; with the switch off there is only today's "none" line.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Transcripts from "../src/account/Transcripts";
import { makeScriptWords as w } from "../src/content/make-script-words";
import { text } from "../src/content/transcripts";

const live = vi.hoisted(() => ({ on: true }));
vi.mock("../src/content/make-script-switch", () => ({
  get SCRIPT_MAKE_LIVE() {
    return live.on;
  },
}));
vi.mock("../src/content/theatre-download-switch", () => ({ THEATRE_DOWNLOAD_LIVE: true }));
vi.mock("../src/content/session-notes-switch", () => ({ SESSION_NOTES_LIVE: true }));

afterEach(() => {
  cleanup();
  live.on = true;
});

const REF = "200000000000000001.cmp1";
const asOwner = (api: ReturnType<typeof mockApi>): AccountApi =>
  Object.assign(
    {
      ...api,
      me: async () => {
        const me = await api.me();
        return me && { ...me, campaigns: [{ ...me.campaigns[0]!, ref: REF, role: "owner" as const }] };
      },
    },
    { calls: api.calls },
  ) as AccountApi;
const openScript = async (api: AccountApi, number: number) => {
  render(<Transcripts api={api} path={`/account/transcripts/${REF}/${number}`} />);
  await screen.findByRole("button", { name: text.versions.cleaned });
  fireEvent.click(await screen.findByRole("button", { name: text.tabs.script }));
};

describe("Make the script", () => {
  it("shows the cost and one button, makes it on one press, and shows the script in the tab", async () => {
    const base = mockApi("table");
    await openScript(asOwner(base), 2);
    expect(await screen.findByText(text.scriptNone)).toBeTruthy();
    expect(await screen.findByText("It uses about 6 cents of your AI allowance.")).toBeTruthy();
    expect(base.calls.some((c) => c.startsWith("makeScript"))).toBe(false); // (nothing is made before the press)
    fireEvent.click(screen.getByRole("button", { name: w.button }));
    expect(await screen.findByText(w.stage)).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/draws her sword/)).toBeTruthy(), { timeout: 5000 });
    expect(base.calls.filter((c) => c.startsWith("makeScript"))).toHaveLength(1);
  });
  it("says a script was already made when the session has one", async () => {
    const api = asOwner(mockApi("table"));
    // (the Script tab reads session 1's script; make the read say none so the offer is what speaks)
    api.transcriptScript = async () => Promise.reject(new ApiError("no-script"));
    await openScript(api, 1);
    expect(await screen.findByText(w.already)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.button })).toBeNull();
  });
  it("says so when the plan has no scripts, or scripts are off, with no button", async () => {
    for (const [why, words] of [["plan", w.plan], ["off", w.off]] as const) {
      const base = mockApi("table");
      const api = asOwner(base);
      api.scriptOffer = async () => ({ state: "none", cost: null, allowed: false, why });
      await openScript(api, 2);
      expect(await screen.findByText(words)).toBeTruthy();
      expect(screen.queryByRole("button", { name: w.button })).toBeNull();
      cleanup();
    }
  });
  it("says what failed and keeps the button", async () => {
    const api = asOwner(mockApi("table"));
    api.makeScript = async () => Promise.reject(new ApiError("server"));
    await openScript(api, 2);
    fireEvent.click(await screen.findByRole("button", { name: w.button }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.failed);
    expect(screen.getByRole("button", { name: w.button })).toBeTruthy();
  });
  it("gives the refusal's own words", async () => {
    const api = asOwner(mockApi("table"));
    api.makeScript = async () => Promise.reject(new ApiError("plan-needed"));
    await openScript(api, 2);
    fireEvent.click(await screen.findByRole("button", { name: w.button }));
    expect((await screen.findByRole("alert")).textContent).toBe(w.problems["plan-needed"]);
  });
  it("shows only today's line with the switch off, and asks for no offer", async () => {
    live.on = false;
    const base = mockApi("table");
    await openScript(asOwner(base), 2);
    expect(await screen.findByText(text.scriptNone)).toBeTruthy();
    await act(async () => void (await new Promise((r) => setTimeout(r, 30))));
    expect(screen.queryByRole("button", { name: w.button })).toBeNull();
    expect(base.calls.some((c) => c.startsWith("scriptOffer"))).toBe(false);
  });
});

describe("a script that is already being made", () => {
  it("follows the job with no button until it exists, then shows the script", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = asOwner(mockApi("table"));
    let looks = 0;
    api.scriptOffer = async () => (++looks < 3 ? { state: "running", cost: null, allowed: true, why: null, progress: { stage: "script_writing", done: 1, total: 3, unit: "parts", startedAt: 100, updatedAt: 101, serverNow: 102, state: "running" } } : { state: "exists", cost: null, allowed: true, why: null });
    api.transcriptScript = async () => (looks < 3 ? Promise.reject(new ApiError("no-script")) : new Blob(["The script, session 2.\n"]));
    await openScript(api, 2);
    expect(await screen.findByText(w.stage)).toBeTruthy();
    expect(screen.queryByRole("button", { name: w.button })).toBeNull(); // (a second press can never queue a second charge)
    await act(async () => void (await vi.advanceTimersByTimeAsync(7000)));
    expect(await screen.findByText(/The script, session 2\./)).toBeTruthy();
    vi.useRealTimers();
  });
});

describe("the presses and the offer", () => {
  it("ignores a second press while the first is in flight", async () => {
    const api = asOwner(mockApi("table"));
    let calls = 0;
    api.makeScript = () => {
      calls += 1;
      return new Promise<void>(() => {});
    };
    await openScript(api, 2);
    const button = await screen.findByRole("button", { name: w.button });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(calls).toBe(1);
  });
  it("tells a game master when the offer cannot be read, and shows a player nothing", async () => {
    const api = asOwner(mockApi("table"));
    api.scriptOffer = async () => Promise.reject(new ApiError("server"));
    await openScript(api, 2);
    expect(await screen.findByText(w.failed)).toBeTruthy();
    cleanup();
    const player = asOwner(mockApi("table"));
    player.scriptOffer = async () => Promise.reject(new ApiError("not-allowed"));
    await openScript(player, 2);
    await act(async () => void (await new Promise((r) => setTimeout(r, 30))));
    expect(screen.queryByText(w.failed)).toBeNull();
  });
});
