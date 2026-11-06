/** @jsxImportSource preact */
// @vitest-environment happy-dom
// What the recap page shows while a recap goes out or its audio is made (#2419): the running clock
// counted from the server's start (right whatever the browser's clock says, and the same after a
// reload), the step, the end states, and the page's quiet polling that backs off and stops while
// the tab is hidden.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Recap } from "../src/account/api";
import { mockApi, mockRecaps } from "../src/account/mock";
import Recaps from "../src/account/Recaps";
import { clock, discordLink } from "../src/account/RecapProgress";
import { approveWords } from "../src/content/recap-approve";
import { text } from "../src/content/recaps";

const base = mockRecaps()[0]!;
const path = `/account/recaps/${base.ref}`;

/** The recap as the API says it while it goes out: server time `now`, started `ago` seconds before. */
const posting = (ago: number, step: Recap["postingStep"] = "check", serverNow = 1_900_000_000): Recap => ({
  ...base,
  status: "posting",
  canEdit: false,
  canPost: false,
  canTryAgain: false,
  canDrop: false,
  postingSince: serverNow - ago,
  postingStep: step,
  serverNow,
});

function pageWith(reads: Recap[]) {
  const api = mockApi("table");
  let at = 0;
  const looked: number[] = [];
  api.recap = async () => {
    looked.push(Date.now());
    return reads[Math.min(at++, reads.length - 1)]!;
  };
  return Object.assign(api, { looked });
}

const hidden = (value: boolean) => {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => value });
};

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: false });
  vi.setSystemTime(new Date(1_900_000_000 * 1000));
});
afterEach(() => {
  cleanup();
  hidden(false);
  vi.useRealTimers();
});

async function show(api: ReturnType<typeof pageWith>) {
  render(<Recaps api={api} path={path} />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}
const wait = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

describe("the clock", () => {
  it("formats minutes and seconds", () => {
    expect([0, 5, 59, 60, 61, 600, -3].map(clock)).toEqual(["0:00", "0:05", "0:59", "1:00", "1:01", "10:00", "0:00"]);
  });

  it("counts from when the work started on the server, and runs each second", async () => {
    await show(pageWith([posting(42)]));
    expect(screen.getByText(text.posting)).toBeTruthy();
    expect(screen.getByRole("timer").textContent).toBe("0:42");
    await wait(2000);
    expect(screen.getByRole("timer").textContent).toBe("0:44");
  });

  it("is the same after a reload (it reads the server's start, not the page's)", async () => {
    await show(pageWith([posting(100)]));
    expect(screen.getByRole("timer").textContent).toBe("1:40");
    cleanup();
    await show(pageWith([posting(100)]));
    expect(screen.getByRole("timer").textContent).toBe("1:40");
  });

  it("is right when the browser's clock is wrong, by using the server's own clock", async () => {
    vi.setSystemTime(new Date((1_900_000_000 + 3600) * 1000)); // the browser is an hour fast
    await show(pageWith([posting(30)]));
    expect(screen.getByRole("timer").textContent).toBe("0:30");
  });

  it("counts from when the page first saw it when the bot hasn't recorded a start", async () => {
    const { serverNow: _unknown, ...rest } = posting(0);
    const noStart: Recap = { ...rest, postingSince: null };
    await show(pageWith([noStart]));
    expect(screen.getByRole("timer").textContent).toBe("0:00");
    await wait(2000);
    expect(screen.getByRole("timer").textContent).toBe("0:02");
  });

  it("starts at 0:00 when the DM presses Post, not from when the page loaded (no recorded start)", async () => {
    const { serverNow: _s, ...rest } = posting(0);
    const noStart: Recap = { ...rest, postingSince: null };
    const api = pageWith([{ ...base }]);
    api.approveRecap = async () => noStart;
    await show(api);
    expect(screen.queryByRole("timer")).toBeNull();
    await wait(5 * 60 * 1000); // sits on the page for five minutes
    fireEvent.click(screen.getByRole("button", { name: approveWords.postText }));
    await wait(0);
    expect(screen.getByRole("timer").textContent).toBe("0:00");
    await wait(2000);
    expect(screen.getByRole("timer").textContent).toBe("0:02");
  });

  it("is not in a live region, so it is not read out every second", async () => {
    await show(pageWith([posting(5)]));
    const timer = screen.getByRole("timer");
    expect(timer.closest("[role=status]")).toBeNull();
    expect(timer.closest("[aria-live=polite]")).toBeNull();
  });
});

describe("the step", () => {
  it("says each step plainly, in the one live region", async () => {
    const api = pageWith([posting(1, "check"), posting(4, "post")]);
    await show(api);
    const region = () => screen.getAllByRole("status").find((el) => el.getAttribute("aria-live") === "polite" && /Checking|Posting to/.test(el.textContent ?? ""));
    expect(region()?.textContent).toBe(text.step.check);
    await wait(3000);
    expect(region()?.textContent).toBe(text.step.post);
  });

  it("says only that it is getting ready when the bot doesn't say the step", async () => {
    await show(pageWith([posting(3, null)]));
    expect(screen.getByText(text.posting)).toBeTruthy();
    expect(screen.queryByText(text.step.check)).toBeNull();
    expect(screen.queryByText(text.step.post)).toBeNull();
  });

  it("shows the audio being made, with the same clock", async () => {
    const audio: Recap = {
      ...base,
      approval: { approved: true, choice: "voice", approvedAt: 1, canApprove: false },
      audio: { state: "queued", error: "", current: false, canAskAgain: false },
      postingSince: 1_900_000_000 - 75,
      serverNow: 1_900_000_000,
    };
    await show(pageWith([audio]));
    expect(screen.getByRole("heading", { name: text.audioMaking })).toBeTruthy();
    expect(screen.getByRole("timer").textContent).toBe("1:15");
  });
});

describe("how it ends", () => {
  const posted = (url?: string | null): Recap => ({ ...base, status: "posted", canEdit: false, canPost: false, canTryAgain: false, canDrop: false, postedUrl: url ?? null });

  it("says it posted, with a link to the message in Discord", async () => {
    const link = "https://discord.com/channels/1/2/3";
    await show(pageWith([posting(5), posted(link)]));
    await wait(3000);
    expect(screen.getByText(new RegExp(text.posted))).toBeTruthy();
    const a = screen.getByRole("link", { name: text.postedLink }) as HTMLAnchorElement;
    expect(a.href).toBe(link);
    expect(screen.queryByRole("timer")).toBeNull();
  });

  it("shows no link for an address that isn't Discord's", async () => {
    await show(pageWith([posted("https://evil.example/x")]));
    expect(screen.queryByRole("link", { name: text.postedLink })).toBeNull();
    expect(discordLink("javascript:alert(1)")).toBeNull();
    expect(discordLink("http://discord.com/channels/1")).toBeNull();
    expect(discordLink("https://discord.com.evil.example/x")).toBeNull();
    expect(discordLink("https://discord.com/channels/1/2/3")).toBe("https://discord.com/channels/1/2/3");
  });

  it("says plainly that nothing was posted when it goes back to a draft", async () => {
    await show(pageWith([posting(5), { ...base }]));
    expect(screen.queryByText(text.postFailed)).toBeNull();
    await wait(3000);
    expect(screen.getByText(text.postFailed)).toBeTruthy();
    expect(screen.queryByRole("timer")).toBeNull();
  });
});

describe("the polling", () => {
  it("looks soon at first and then settles, never faster than every 3 seconds", async () => {
    const api = pageWith([posting(1)]);
    await show(api);
    const t0 = Date.now();
    api.looked.length = 0; // (the first read of the page)
    await wait(16000);
    expect(api.looked.map((t) => Math.round((t - t0) / 1000))).toEqual([3, 6, 10, 15]);
  });

  it("makes no request while the tab is hidden, and looks at once when it is shown again", async () => {
    const api = pageWith([posting(1)]);
    await show(api);
    api.looked.length = 0;
    hidden(true);
    document.dispatchEvent(new Event("visibilitychange"));
    await wait(30000);
    expect(api.looked).toHaveLength(0);
    hidden(false);
    document.dispatchEvent(new Event("visibilitychange"));
    await wait(0);
    expect(api.looked).toHaveLength(1);
  });

  it("never has two looks going at once, even if the tab is hidden and shown during one", async () => {
    let started = 0;
    let finish: (() => void) | undefined;
    const api = mockApi("table");
    api.recap = () => {
      started += 1;
      return new Promise((resolve) => {
        finish = () => resolve(posting(1));
      });
    };
    render(<Recaps api={api} path={path} />);
    await wait(0);
    finish?.(); // the first read of the page
    await wait(0);
    started = 0;
    await wait(3000); // a look begins and is left waiting
    expect(started).toBe(1);
    hidden(true);
    document.dispatchEvent(new Event("visibilitychange"));
    hidden(false);
    document.dispatchEvent(new Event("visibilitychange"));
    await wait(0);
    expect(started).toBe(1); // the one already on its way is enough
    finish?.();
    await wait(0);
  });

  it("stops looking once it is posted", async () => {
    const api = pageWith([posting(1), { ...base, status: "posted", canEdit: false, canPost: false, canTryAgain: false, canDrop: false }]);
    await show(api);
    await wait(3000);
    api.looked.length = 0;
    await wait(30000);
    expect(api.looked).toHaveLength(0);
  });
});
