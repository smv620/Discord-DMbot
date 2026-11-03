/** @jsxImportSource preact */
// @vitest-environment happy-dom
// The game master's "Save the script" button on a session's Transcripts page (#3244): game masters only, behind
// THEATRE_DOWNLOAD_LIVE, a session with no script says so.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, httpApi, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Transcripts from "../src/account/Transcripts";
import { text } from "../src/content/transcripts";

const live = vi.hoisted(() => ({ on: false }));
vi.mock("../src/content/theatre-download-switch", () => ({
  get THEATRE_DOWNLOAD_LIVE() {
    return live.on;
  },
}));

afterEach(() => {
  cleanup();
  live.on = false;
  vi.restoreAllMocks();
});

const REF = "200000000000000001.cmp1";
const open = async (api: AccountApi, number = 1) => {
  render(<Transcripts api={api} path={`/account/transcripts/${REF}/${number}`} />);
  await screen.findByRole("button", { name: text.download });
};
/** The pretend transcripts live under their own address, which the pretend "me" does not list, so each test says which
 * role the person has in it. */
const asRole = (api: ReturnType<typeof mockApi>, role: "owner" | "player"): AccountApi =>
  ({
    ...api,
    me: async () => {
      const me = await api.me();
      return me && { ...me, campaigns: [{ ...me.campaigns[0]!, ref: REF, role }] };
    },
  }) as AccountApi;

describe("the Save the script button", () => {
  it("with the switch off there is no button", async () => {
    await open(asRole(mockApi("table"), "owner"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(screen.queryByRole("button", { name: text.scriptDownload })).toBeNull();
  });

  it("a game master sees it and saves the file named for the session", async () => {
    live.on = true;
    const base = mockApi("table");
    const api = Object.assign(asRole(base, "owner"), { calls: base.calls });
    const clicked: string[] = [];
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    });
    await open(api);
    fireEvent.click(await screen.findByRole("button", { name: text.scriptDownload }));
    await waitFor(() => expect(clicked).toHaveLength(1));
    expect(clicked[0]).toMatch(/-script\.txt$/);
    expect(clicked[0]).not.toMatch(/cleaned|heard/);
    expect(api.calls.some((c) => c.startsWith("transcriptScript:") && c.endsWith(":1"))).toBe(true);
    expect(screen.getByText(text.scriptAbout)).toBeTruthy();
  });

  it("a player never sees it", async () => {
    live.on = true;
    await open(asRole(mockApi("table"), "player"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(screen.queryByRole("button", { name: text.scriptDownload })).toBeNull();
  });

  it("a session with no script says so, and a failure says to try again", async () => {
    live.on = true;
    const base = mockApi("table");
    let kind = "no-script";
    const api = {
      ...asRole(base, "owner"),
      transcriptScript: async () => {
        throw new ApiError(kind as never);
      },
    } as AccountApi;
    await open(api);
    fireEvent.click(await screen.findByRole("button", { name: text.scriptDownload }));
    expect(await screen.findByText(text.scriptNone)).toBeTruthy();
    kind = "network";
    fireEvent.click(screen.getByRole("button", { name: text.scriptDownload }));
    expect(await screen.findByText(text.scriptFailed)).toBeTruthy();
  });
});

describe("the real client", () => {
  it("asks the script's address and reads a session with none as no-script", async () => {
    const seen: string[] = [];
    const api = httpApi("https://api.example", (async (url: string) => {
      seen.push(new URL(url).pathname);
      if (seen.length === 1) return new Response("script", { status: 200 });
      return new Response(JSON.stringify({ error: "no_script" }), { status: 404 });
    }) as typeof fetch);
    await api.transcriptScript!(REF, 1);
    await expect(api.transcriptScript!(REF, 2)).rejects.toMatchObject({ kind: "no-script" });
    expect(seen).toEqual([`/me/campaigns/${REF}/transcripts/1/script`, `/me/campaigns/${REF}/transcripts/2/script`]);
  });
});
