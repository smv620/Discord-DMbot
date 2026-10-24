/** @jsxImportSource preact */
// @vitest-environment happy-dom
// Off-topic chat left out of the cleaned transcript (#2568; core #2567): every marker opens the stretch as heard
// (as text only), for anyone who may read the transcript; only a campaign's DMs get Put it back and Leave it out
// again. The API decides who may, and the page says what it did and no more.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/preact";
import { afterEach, describe, expect, it } from "vitest";

import { ApiError, httpApi, type AccountApi } from "../src/account/api";
import { mockApi } from "../src/account/mock";
import Transcripts from "../src/account/Transcripts";
import { PUT_BACK_READS_AGAIN, offTopic as w } from "../src/content/transcripts";

afterEach(cleanup);

const REF = "200000000000000001.cmp1";
const PATH = `/account/transcripts/${REF}/2`;
const calls = (api: unknown, start: string): string[] => (api as { calls: string[] }).calls.filter((c) => c.startsWith(start));

/** The pretend API, with the signed-in person a DM of the campaign (`role`) or not. */
function as(role: "owner" | "co-dm" | "player", api = mockApi("table")): AccountApi & { calls: string[] } {
  const real = api.me.bind(api);
  api.me = async () => {
    const got = await real();
    return got && { ...got, campaigns: [...got.campaigns, { id: "t", ref: REF, name: "The Brynwater Crossing", serverName: "Thursday Table", lastPlayedAt: null, status: "active" as const, role }] };
  };
  return api as AccountApi & { calls: string[] };
}
const show = async (api: AccountApi) => {
  render(<Transcripts api={api} path={PATH} />);
  fireEvent.click(await screen.findByRole("button", { name: w.showFor(3) }));
};

describe("the marker", () => {
  it("says how much was left out, and the counts for the session", async () => {
    render(<Transcripts api={as("player")} path={PATH} />);
    expect(await screen.findByText(w.left(3, 14))).toBeTruthy();
    expect(screen.getByText(w.filter(412, 9, 0))).toBeTruthy();
  });

  it("is not there in the as-heard reading, which has every word", async () => {
    render(<Transcripts api={as("owner")} path={PATH} />);
    await screen.findByText(w.left(3, 14));
    fireEvent.click(screen.getByRole("button", { name: /As heard/ }));
    await waitFor(() => expect(screen.queryByText(w.left(3, 14))).toBeNull());
  });
});

describe("opening what was said", () => {
  it("shows the lines as heard, as text only, for a player too, with no Put it back", async () => {
    const api = as("player");
    await show(api);
    expect(await screen.findByText(/\[0:01:02\] Mia: did you see the match/)).toBeTruthy();
    // markup in the words stays text: it is never turned into an element
    expect(screen.getByText(/<b>no<\/b> I missed it/)).toBeTruthy();
    expect(document.querySelector(".left-out b")).toBeNull();
    expect(screen.queryByRole("button", { name: w.putBack })).toBeNull();
    expect(calls(api, "transcriptStretch")).toEqual([`transcriptStretch:${REF}:2:1:62000:76000`]);
  });

  it("closes again with Hide it", async () => {
    await show(as("player"));
    await screen.findByText(/did you see the match/);
    fireEvent.click(screen.getByRole("button", { name: w.showFor(3) }));
    expect(screen.queryByText(/did you see the match/)).toBeNull();
  });

  it("says plainly when the stretch isn't there any more", async () => {
    const api = as("owner");
    api.transcriptStretch = async () => {
      throw new ApiError("not-found");
    };
    await show(api);
    expect(await screen.findByText(w.problem["not-found"]!)).toBeTruthy();
  });

  it("falls back to plain words for an error it doesn't know", async () => {
    const api = as("owner");
    api.transcriptStretch = async () => {
      throw new ApiError("slow-down");
    };
    await show(api);
    expect(await screen.findByText(w.problem["server"]!)).toBeTruthy();
  });
});

describe("Put it back and Leave it out again (DMs)", () => {
  it.each(["owner", "co-dm"] as const)("a %s can put it back, and undo it", async (role) => {
    const api = as(role);
    await show(api);
    const box = (await screen.findByText(/did you see the match/)).closest(".panel") as HTMLElement;
    fireEvent.click(await within(box).findByRole("button", { name: w.putBackFor(3) }));
    expect(await screen.findByText(new RegExp(w.putBackDone))).toBeTruthy();
    expect(calls(api, "transcriptPutBack")).toEqual([`transcriptPutBack:${REF}:2:1:62000:76000`]);
    expect(screen.getByText(w.putBackLine(3))).toBeTruthy();
    fireEvent.click(await within(box).findByRole("button", { name: w.leaveOut }));
    expect(await screen.findByText(new RegExp(w.leftOutDone))).toBeTruthy();
    expect(calls(api, "transcriptLeaveOut")).toHaveLength(1);
    expect(screen.getByText(w.left(3, 14))).toBeTruthy();
  });

  it("does not promise more than the API did: no 'names and story' sentence until that works", async () => {
    expect(PUT_BACK_READS_AGAIN).toBe(false);
    await show(as("owner"));
    fireEvent.click(await screen.findByRole("button", { name: w.putBackFor(3) }));
    expect(await screen.findByText(new RegExp(w.putBackDone))).toBeTruthy();
    expect(document.body.textContent).not.toContain("names and story");
  });

  it("says it was already so when nothing changed", async () => {
    const api = as("owner");
    api.transcriptPutBack = async () => 0;
    await show(api);
    fireEvent.click(await screen.findByRole("button", { name: w.putBackFor(3) }));
    expect(await screen.findByText(new RegExp(w.nothingChanged))).toBeTruthy();
  });

  it("a refusal says so and leaves the stretch as it was", async () => {
    const api = as("owner");
    api.transcriptPutBack = async () => {
      throw new ApiError("not-allowed");
    };
    await show(api);
    fireEvent.click(await screen.findByRole("button", { name: w.putBackFor(3) }));
    expect(await screen.findByText(w.problem["not-allowed"]!)).toBeTruthy();
    expect(screen.getByText(w.left(3, 14))).toBeTruthy();
  });

  it("is not offered when the API has no put-back route", async () => {
    const api = as("owner");
    delete api.transcriptPutBack;
    await show(api);
    await screen.findByText(/did you see the match/);
    expect(screen.queryByRole("button", { name: w.putBackFor(3) })).toBeNull();
  });
});

describe("the real client", () => {
  it("asks for a stretch by its speaker and times, and posts the same to put back and leave out", async () => {
    const seen: string[] = [];
    const api = httpApi("https://api.example", (async (url: string, init?: RequestInit) => {
      seen.push(`${init?.method} ${new URL(url).pathname}${new URL(url).search} ${init?.body ?? ""}`);
      return new Response(JSON.stringify(init?.method === "POST" ? { changed: 3 } : { lines: [] }), { status: 200 });
    }) as typeof fetch);
    const skip = { line: 2, speaker: 3, fromMs: 1000, toMs: 2000, seconds: 1, lines: 2, putBack: false };
    await api.transcriptStretch!("1.c", 2, skip);
    expect(await api.transcriptPutBack!("1.c", 2, skip)).toBe(3);
    await api.transcriptLeaveOut!("1.c", 2, skip);
    expect(seen).toEqual([
      "GET /me/campaigns/1.c/transcripts/2/stretch?speaker=3&fromMs=1000&toMs=2000 ",
      'POST /me/campaigns/1.c/transcripts/2/put-back {"speaker":3,"fromMs":1000,"toMs":2000}',
      'POST /me/campaigns/1.c/transcripts/2/leave-out {"speaker":3,"fromMs":1000,"toMs":2000}',
    ]);
  });
});
